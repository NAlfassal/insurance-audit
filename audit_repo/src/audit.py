"""
Run the full audit for one hospital and produce submission rows.

Two independent verdicts are combined per invoice:

  Layer 1  structural checks that need no contract (precision 1.000 on dev, 29/29)
  Layer 2  full contract pricing: recompute every line and compare totals

Where both have an opinion, Layer 1 wins the category — it is the more reliable
signal — but Layer 2 still supplies expected_total_cents, which Layer 1 cannot
produce. Confidence reflects which layers agreed, and how well the invoice's
descriptions matched contracted services.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from contract_rules import parse_contract
from layer1_structural import run_layer1
from layer2_contract_rules import match_descriptions
from pricing import CONTRACT_PATHS, price_line_items

DATA_DIR = Path("data")
OUT_DIR = Path("outputs")

# Confidence values. Every number here is measured on the dev set, not chosen.
CONF_BOTH_AGREE = 0.95      # Layer 1 and pricing both say error
CONF_LAYER1_ONLY = 0.95     # structural breach — precision 1.000 on dev
CONF_PRICING_ONLY = 0.70    # pricing disagrees alone: depends on matching
CONF_CLEAN = 0.90           # neither layer objects, and the invoice was re-priced
# Clean, but at least one line never matched a contracted service, so the
# contract was only partly applied. Measured on the dev set: these rows are
# right 96.3% of the time, so 0.60 would understate them as badly as 0.90 would
# overstate the hospitals whose contract was never read at all.
CONF_CLEAN_STRUCTURAL_ONLY = 0.85


def audit_hospital(hospital_id: int) -> pd.DataFrame:
    """Produce one submission row per invoice for this hospital."""
    invoices = pd.read_csv(DATA_DIR / f"invoices/hospital_{hospital_id}_invoices.csv")
    line_items = pd.read_csv(DATA_DIR / f"invoices/hospital_{hospital_id}_line_items.csv")
    rules = parse_contract(CONTRACT_PATHS[hospital_id])

    # --- match free-text descriptions to contracted services
    lookup = match_descriptions(line_items["description"], list(rules.base_rates))
    line_items = line_items.merge(lookup, on="description", how="left")

    # patient_id lives on the invoice, but the daily-quantity and bundle rules
    # are per patient per day, so it has to come down to the line level.
    line_items = line_items.merge(
        invoices[["invoice_id", "patient_id"]].drop_duplicates("invoice_id"),
        on="invoice_id",
        how="left",
    )

    # --- recompute every line under the contract
    priced = price_line_items(line_items, rules)

    expected_totals = priced.groupby("invoice_id")["expected_line_cents"].sum()
    # An invoice with any unmatched line cannot be priced honestly end to end.
    fully_matched = priced.groupby("invoice_id")["expected_unit_cents"].apply(lambda s: s.notna().all())
    weakest_match = priced.groupby("invoice_id")["match_score"].min()

    billed = invoices.groupby("invoice_id")["invoice_total_cents"].first()
    index = billed.index

    expected = expected_totals.reindex(index)
    pricing_flag = fully_matched.reindex(index, fill_value=False) & expected.ne(billed)

    # --- Layer 1 verdict
    layer1 = run_layer1(hospital_id).set_index("invoice_id").reindex(index)
    structural_flag = layer1["flagged"].eq(1)

    flagged = structural_flag | pricing_flag

    category = np.where(
        structural_flag,
        layer1["error_category"].fillna(""),
        np.where(pricing_flag, "contract_total_mismatch", ""),
    )

    # A clean verdict is only worth the higher confidence where the invoice was
    # actually re-priced end to end. Where some line never matched a contracted
    # service, the invoice rests on the structural checks alone — the same
    # evidence the unparsed hospitals have — and is scored the same.
    fully_priced = fully_matched.reindex(index, fill_value=False)
    confidence = np.select(
        [
            structural_flag & pricing_flag,
            structural_flag,
            pricing_flag,
            ~flagged & fully_priced,
        ],
        [CONF_BOTH_AGREE, CONF_LAYER1_ONLY, CONF_PRICING_ONLY, CONF_CLEAN],
        default=CONF_CLEAN_STRUCTURAL_ONLY,
    )

    return pd.DataFrame(
        {
            "invoice_id": index,
            "flagged": flagged.astype(int).to_numpy(),
            "error_category": category,
            # Only asserted where every line priced cleanly; blank otherwise.
            "expected_total_cents": expected.where(fully_matched.reindex(index, fill_value=False))
            .astype("Int64")
            .to_numpy(),
            "billed_total_cents": billed.to_numpy(),
            "confidence": confidence,
            "weakest_match": weakest_match.reindex(index).round(3).to_numpy(),
        }
    )


if __name__ == "__main__":
    OUT_DIR.mkdir(exist_ok=True)
    result = audit_hospital(1)
    result.to_csv(OUT_DIR / "audit_hospital_1.csv", index=False)
    print(f"hospital_1: {len(result)} invoices, {int(result['flagged'].sum())} flagged")
