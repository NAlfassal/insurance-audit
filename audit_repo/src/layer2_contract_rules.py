"""
Layer 2 — Contract-dependent validation.

Layer 1 finds invoices that contradict themselves. This layer finds invoices
that contradict the *contract*, which needs three things Layer 1 never does:

  1. extract  — read the contract prose and produce a machine-usable rate
                table (LLM, prompt versioned in prompts/)
  2. match    — map a free-text billing description to a contracted service
                (lexical + character n-gram similarity, no LLM)
  3. compare  — check the billed unit price against the contracted rate

Deliberate scope limit
----------------------
This layer applies BASE RATES ONLY. Threshold premiums, cumulative volume
discounts, bundled substitutions, daily caps and exclusion windows are all
present in the contracts and are NOT implemented here. A line item subject to
one of those adjustments will legitimately differ from its base rate, so this
layer can only flag a *price* disagreement, never prove the final total. That
is why it never writes expected_total_cents and never claims high confidence.
See decision_log.md.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from abbreviations import expand

DATA_DIR = Path("data")
PROMPT_PATH = Path("prompts/extract_base_rates_v1.txt")
OUT_DIR = Path("outputs")

# Contract file to read per hospital. Only the clean text sources are used:
# hospital_2 also ships a *_scanned.pdf whose OCR is visibly corrupted
# ("Parfies", "Agrcement"), so it is deliberately excluded. See decision_log.md.
CONTRACT_FILES = {
    1: ["contracts/hospital_1/provider_services_agreement.md"],
    2: ["contracts/hospital_2/master_services_agreement.md"],
    3: [
        "contracts/hospital_3/base_agreement.md",
        "contracts/hospital_3/appendix_b_rate_schedule.md",
        "contracts/hospital_3/amendment_no_1.md",
    ],
    4: ["contracts/hospital_4/conditional_reimbursement_agreement.md"],
    # hospital_5's Tables 2 and 3 (facility and plan-tier multipliers) exist
    # ONLY in the separate rate-tables PDF, not in the .md/.txt. Extracting
    # base rates without them would be confidently wrong, so it is left out.
    5: ["contracts/hospital_5/network_reimbursement_agreement.md"],
}

# Ratios of billed price to base rate that the contract itself allows.
# 1.00 is the unadjusted base rate; the uplifts are the threshold premiums and
# non-business-day uplifts (+12% .. +40%); the reductions are the cumulative
# volume discounts (10% .. 30%). A ratio outside this set cannot be produced by
# any adjustment the contract provides for, so it is unexplainable.
KNOWN_ADJUSTMENT_FACTORS = (
    1.00,
    1.12, 1.15, 1.20, 1.25, 1.30, 1.40,   # premiums and uplifts
    0.90, 0.88, 0.85, 0.82, 0.80, 0.75, 0.70,  # volume discounts
)

# A match below this is treated as "no contracted service identified" rather
# than forced onto the nearest name.
MIN_MATCH_SCORE = 0.55

# Minimum lead the best match must hold over the runner-up. Measured on the dev
# set: lines priced correctly had a mean margin of 0.317, mis-priced lines only
# 0.108. A description that fits two services almost equally well is treated as
# unidentified rather than assigned to the marginally closer one.
MIN_MATCH_MARGIN = 0.20

# Confidence for a price disagreement found by this layer. Well below Layer 1:
# it depends on an LLM extraction and a fuzzy match, and base rates alone do
# not account for premiums or discounts that legitimately move the price.
CONF_PRICE_MISMATCH = 0.55


# --- 1. extract ------------------------------------------------------------


def _read_contract(hospital_id: int) -> str:
    """Concatenate this hospital's contract documents in reading order."""
    parts = [(DATA_DIR / path).read_text(encoding="utf-8") for path in CONTRACT_FILES[hospital_id]]
    return "\n\n".join(parts)


def extract_rates_llm(hospital_id: int) -> dict:
    """Ask Gemini to turn contract prose into a base-rate table.

    The prompt lives in prompts/ as a versioned file so the extraction is
    auditable and re-runnable. Output is cached to outputs/ because the
    contract does not change between runs and re-calling costs money.
    """
    from dotenv import load_dotenv
    from google import genai

    load_dotenv()
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

    prompt = PROMPT_PATH.read_text(encoding="utf-8").replace(
        "{contract_text}", _read_contract(hospital_id)
    )
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config={"response_mime_type": "application/json"},
    )
    return json.loads(response.text)


def extract_rates_regex(hospital_id: int) -> dict:
    """Offline fallback for hospital_2 only.

    hospital_2 states every rate in a fixed sentence form:
      'In respect of <Service>, the Provider shall invoice the Payer at the
       rate of GBP <amount> per <unit>.'
    That regularity makes a regex reliable here — it is NOT a general
    substitute for the LLM path on the other contracts, whose rates sit in
    cross-referenced tables.
    """
    pattern = re.compile(
        r"In respect of (?P<name>[^,]+?), the Provider shall invoice the Payer "
        r"at the rate of GBP (?P<amount>[\d,]+\.\d{2}) (?P<unit>per [^.]+?)\."
    )
    services = [
        {
            "name": m.group("name").strip(),
            "unit_basis": m.group("unit").strip(),
            "rate_cents": int(round(float(m.group("amount").replace(",", "")) * 100)),
        }
        for m in pattern.finditer(_read_contract(hospital_id))
    ]
    return {"contract_number": None, "services": services, "ambiguities": ["regex fallback"]}


def extract_rates_markdown_table(hospital_id: int) -> dict:
    """Offline extractor for contracts whose rates sit in a markdown table.

    Used for hospital_1 (the dev set) so Layer 2 can be *measured* against
    labels without spending an API call. Rows look like:
      | Advanced Cardiac Recovery Room Occupancy | per hour | GBP 200.00 | — |
    """
    pattern = re.compile(
        r"^\|\s*(?P<name>[A-Z][^|]+?)\s*\|\s*(?P<unit>per [^|]+?)\s*\|\s*"
        r"GBP\s*(?P<amount>[\d,]+\.\d{2})\s*\|"
    )
    services = [
        {
            "name": m.group("name").strip(),
            "unit_basis": m.group("unit").strip(),
            "rate_cents": int(round(float(m.group("amount").replace(",", "")) * 100)),
        }
        for line in _read_contract(hospital_id).splitlines()
        if (m := pattern.match(line))
    ]
    return {"contract_number": None, "services": services, "ambiguities": ["markdown table extractor"]}


def get_rates(hospital_id: int, use_llm: bool = True) -> dict:
    """Load cached rates, else extract them and cache the result."""
    cache = OUT_DIR / f"rates_hospital_{hospital_id}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    if use_llm:
        rates = extract_rates_llm(hospital_id)
    elif hospital_id == 1:
        rates = extract_rates_markdown_table(hospital_id)
    else:
        rates = extract_rates_regex(hospital_id)
    OUT_DIR.mkdir(exist_ok=True)
    cache.write_text(json.dumps(rates, indent=2, ensure_ascii=False), encoding="utf-8")
    return rates


# --- 2. match --------------------------------------------------------------


def match_descriptions(descriptions: pd.Series, service_names: list[str]) -> pd.DataFrame:
    """Map each free-text description to its closest contracted service.

    Descriptions are expanded out of billing shorthand first (see
    abbreviations.py) — without that step nothing clears the threshold,
    because the shorthand shares almost no characters with contract wording.

    Two independent signals are then averaged so neither is a single point of
    failure: token-level fuzzy ratio catches reordered words and partial
    overlap; character n-gram TF-IDF catches shared stems the token matcher
    misses. Matching only unique descriptions keeps this ~20x cheaper.
    """
    unique = descriptions.drop_duplicates().to_list()
    expanded = [expand(value) for value in unique]

    # signal A — token fuzzy, vectorised over the whole cross-product
    fuzzy = process.cdist(expanded, service_names, scorer=fuzz.token_set_ratio, workers=-1) / 100.0

    # signal B — character n-gram cosine similarity
    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))
    service_matrix = vectorizer.fit_transform(service_names)
    tfidf = cosine_similarity(vectorizer.transform(expanded), service_matrix)

    combined = (fuzzy + tfidf) / 2.0
    best_idx = combined.argmax(axis=1)
    best_score = combined.max(axis=1)

    # Margin over the runner-up: a description that matches two services almost
    # equally well is ambiguous even when its top score looks respectable.
    partitioned = np.partition(combined, -2, axis=1)
    margin = partitioned[:, -1] - partitioned[:, -2]

    lookup = pd.DataFrame(
        {
            "description": unique,
            "matched_service": [service_names[i] for i in best_idx],
            "match_score": best_score.round(3),
            "match_margin": margin.round(3),
        }
    )
    # Below the floor we admit we did not identify the service.
    weak = (lookup["match_score"] < MIN_MATCH_SCORE) | (lookup["match_margin"] < MIN_MATCH_MARGIN)
    lookup.loc[weak, "matched_service"] = pd.NA
    return lookup


# --- 3. compare ------------------------------------------------------------


def run_layer2(hospital_id: int, use_llm: bool = True) -> pd.DataFrame:
    """Flag invoices whose billed unit price disagrees with the base rate.

    Returns one row per invoice that this layer has an opinion about. Invoices
    it cannot assess (no confident service match) are simply absent, so Layer 1
    remains their only verdict.
    """
    rates = get_rates(hospital_id, use_llm=use_llm)
    rate_table = pd.DataFrame(rates["services"])
    if rate_table.empty:
        return pd.DataFrame()

    line_items = pd.read_csv(DATA_DIR / f"invoices/hospital_{hospital_id}_line_items.csv")

    lookup = match_descriptions(line_items["description"], rate_table["name"].to_list())
    lines = line_items.merge(lookup, on="description", how="left").merge(
        rate_table.rename(columns={"name": "matched_service"}),
        on="matched_service",
        how="left",
    )

    assessable = lines["rate_cents"].notna()

    # A billed price that differs from the base rate is NOT evidence of error
    # by itself: the contract's premiums (+15%..+40%) and volume discounts
    # (10%..30%) legitimately move it. Only a ratio that matches no adjustment
    # the contract provides for is unexplainable, and only that is flagged.
    ratio = lines["unit_price_cents"] / lines["rate_cents"]
    explained = pd.Series(False, index=lines.index)
    for factor in KNOWN_ADJUSTMENT_FACTORS:
        explained |= np.isclose(ratio, factor, rtol=0.005)

    price_differs = assessable & ~explained

    per_invoice = (
        pd.DataFrame(
            {
                "invoice_id": lines["invoice_id"],
                "price_differs": price_differs,
                "assessable": assessable,
                "match_score": lines["match_score"],
            }
        )
        .groupby("invoice_id")
        .agg(
            price_differs=("price_differs", "any"),
            assessable=("assessable", "any"),
            weakest_match=("match_score", "min"),
        )
    )

    opinions = per_invoice[per_invoice["assessable"]].copy()
    return pd.DataFrame(
        {
            "invoice_id": opinions.index,
            "flagged": opinions["price_differs"].astype(int).to_numpy(),
            "error_category": np.where(
                opinions["price_differs"], "unit_price_mismatch_vs_base_rate", ""
            ),
            # Never asserted: base rates alone cannot produce a correct total
            # while premiums, discounts and bundles remain unimplemented.
            "expected_total_cents": pd.NA,
            "confidence": np.where(opinions["price_differs"], CONF_PRICE_MISMATCH, 0.50),
        }
    )


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    use_llm = bool(os.environ.get("GEMINI_API_KEY"))
    if not use_llm:
        print("GEMINI_API_KEY not set — using the hospital_2 regex fallback only.\n")

    for hospital_id in [2]:
        result = run_layer2(hospital_id, use_llm=use_llm)
        result.to_csv(OUT_DIR / f"layer2_hospital_{hospital_id}.csv", index=False)
        print(
            f"hospital_{hospital_id}: {len(result)} invoices assessed, "
            f"{int(result['flagged'].sum())} price mismatches"
        )


if __name__ == "__main__":
    main()
