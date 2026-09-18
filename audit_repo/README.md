# Invoice audit — Meridian Health Assurance Group

Audits hospital invoices against their service contracts and produces one
verdict per invoice with a calibrated confidence.

|---|---|
| Development set (hospital_1) | precision **0.892** · recall **0.569** · F1 **0.695** · Brier **0.0404** |
| Submission | `outputs/submission.csv` — 3,942 rows, hospitals 2–5, 151 flagged (3.8%) |
| Coverage | hospital_4 fully audited; hospitals 2, 3, 5 structural checks only |
| LLM calls in the pipeline | none by default — the run is pure Python and reproducible |

Coverage is uneven on purpose and the confidence column says so. See
`reports/decision_log.md` for why each contract was or was not priced.

## Installation & Setup

Dependencies are pinned in `pyproject.toml` and `requirements.txt`. Requires **Python 3.11+**.

```bash
git clone https://github.com/NAlfassal/insurance-audit.git
cd insurance-audit/audit_repo
```

### Standard Setup 

**Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
**Windows:**
```bash
# Command Prompt
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
# PowerShell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```
### Recommended Setup (With uv)
> **Tip:** I highly recommend using uv. It is significantly faster at resolving and installing Python dependencies than standard pip, and automatically manages project environments.
```bash
uv sync                                  
```
## Reproduce
Execute the pipeline and generate evaluation metrics:
```bash
# Using standard virtual environment
PYTHONPATH=src python src/build_submission.py    # writes outputs/submission.csv
PYTHONPATH=src python src/evaluate.py            # reproduces every number in reports/
PYTHONPATH=src python src/evaluate.py --layer1   # scores the structural checks alone

# Using uv
PYTHONPATH=src uv run python src/build_submission.py
PYTHONPATH=src uv run python src/evaluate.py
PYTHONPATH=src uv run python src/evaluate.py --layer1
```

On Windows PowerShell, set the path once with `$env:PYTHONPATH="src"` and drop
the prefix from each command.

## How an invoice is audited

**1. Structural checks** (`src/layer1_structural.py`) — six checks that need no
contract, because they detect an invoice contradicting itself: line arithmetic,
invoice roll-up, duplicate invoice id, service date after invoice date,
malformed or inconsistent dates, contract number. Measured precision 1.000 on
the dev set; these six label categories have recall 1.00.

**2. Contract parsing** (`src/contract_rules.py`) — reduces a contract to one
schema: base rates, threshold premiums, non-business-day uplifts, cumulative
volume discounts, daily caps, bundles, exclusion windows. Regex over the
contract's own tables, so extraction is exact and repeatable. Section lookup is
by keyword, not heading number, so the same parser reads hospital_1's
"Cumulative Volume Discounts" and hospital_4's "Discounts".

**3. Description matching** (`src/abbreviations.py`, `src/layer2_contract_rules.py`) —
billing descriptions are compressed shorthand (`INTENS PALL WD BD OCC /SA-8184`)
and share almost no characters with contract wording. Expanding them through a
dictionary built from the data's own token frequencies lifts the mean match
score from 0.370 to 0.899. A match is accepted only when it scores ≥ 0.55 and
leads the runner-up by ≥ 0.20; otherwise the line is left unidentified rather
than assigned to the nearest name.

**4. Re-pricing** (`src/pricing.py`) — applies the adjustments in the order the
contract mandates (bundle → facility → plan tier → premium → volume discount),
rounding half-up after each step, with daily aggregates and cumulative
utilisation computed hospital-wide in service-date then line-id order.

**5. Verdict** (`src/audit.py`) — combines both layers. Structural findings win
the category; pricing supplies `expected_total_cents`, which is asserted only
where every line on the invoice matched confidently.

## Confidence

Tiered by the evidence behind each row, and every tier is a rate measured on the
dev set rather than a number chosen by feel:

| value | rows | meaning | observed accuracy on dev |
|---|---|---|---|
| 0.95 | 148 | structural breach | 1.000 |
| 0.90 | 87 | clean, and fully re-priced under the contract | 0.996 |
| 0.85 | 712 | clean, contract partly applied (some line unmatched) | 0.963 |
| 0.70 | 3 | pricing disagrees, nothing corroborates it | 0.500 |
| 0.60 | 2,992 | clean, but the contract is not priced by this pipeline | — |

The 0.60 tier is the one to read carefully: it covers hospitals 2, 3 and 5 in
full, and carries no dev figure because hospital_1 *is* priced — the dev set
cannot say what the structural checks are worth on their own against an
unpriced contract. There, "not flagged" means only that the invoice does not
contradict itself.

## Layout

```
src/
  layer1_structural.py      contract-free structural checks
  contract_rules.py         contract -> one rule schema
  abbreviations.py          billing shorthand -> contract vocabulary
  layer2_contract_rules.py  description matching, LLM extraction path
  pricing.py                re-prices each line in the mandated order
  audit.py                  one verdict per invoice
  evaluate.py               scores the pipeline against the hospital_1 labels
  build_submission.py       writes outputs/submission.csv
prompts/                    versioned prompts, with notes on how each was designed
reports/                    writeup.md, evaluation.md, decision_log.md
data/                       the exercise package, unchanged
```

## Use of AI assistance

See `prompts/README.md` — it covers how AI was used, both versioned prompts, and
the design decisions behind each. Neither prompt runs in the default pipeline:
`build_submission.py` parses contracts with regex only, so the submission
reproduces without an API key.
