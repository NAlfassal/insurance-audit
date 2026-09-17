"""
Build submission.csv for hospitals 2-5.

Coverage is deliberately uneven, and the confidence column says so:

  hospital_4   full audit — contract parsed, every line re-priced, structural
               checks. Validated end to end on hospital_1
               (P 0.892 / R 0.569 / Brier 0.0404).
  hospital_2   structural checks only (Layer 1). Its contract states rates in
  hospital_3   prose or across several documents / a separate PDF, which the
  hospital_5   table parser does not read, so no line is re-priced. These rows
               assert only what the invoice data alone can prove.

Rows are submitted for every invoice, correct ones included, as the task asks.
An invoice Layer 1 leaves clean carries confidence 0.90 where no contract was
consulted — that is an honest "nothing in the data contradicts this", not
"this matches the contract".
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from audit import audit_hospital
from layer1_structural import run_layer1

OUT_DIR = Path("outputs")
SUBMISSION_COLUMNS = [
    "invoice_id",
    "flagged",
    "error_category",
    "expected_total_cents",
    "billed_total_cents",
    "confidence",
]

# Confidence for an invoice that passed the structural checks at a hospital
# whose contract was never parsed. Set below the 0.85 and 0.90 tiers because
# those were measured on hospital_1, where the contract *was* applied. Here
# there is no measurement to lean on at all, so the claim is smaller.
CONF_CLEAN_NO_CONTRACT = 0.60

FULLY_AUDITED = (4,)
STRUCTURAL_ONLY = (2, 3, 5)


def build() -> pd.DataFrame:
    frames = []

    for hospital_id in FULLY_AUDITED:
        frames.append(audit_hospital(hospital_id)[SUBMISSION_COLUMNS])

    for hospital_id in STRUCTURAL_ONLY:
        # Layer 1 cannot produce a correct total, so expected_total_cents stays
        # empty rather than being guessed from an unparsed contract.
        frame = run_layer1(hospital_id)[SUBMISSION_COLUMNS]
        # No contract was parsed for these hospitals, so "clean" here means only
        # that the invoice does not contradict itself — weaker evidence than for
        # hospital_4, where the contract was applied as well. The confidence
        # states that difference rather than hiding it behind one flat number.
        frame.loc[frame["flagged"] == 0, "confidence"] = CONF_CLEAN_NO_CONTRACT
        frames.append(frame)

    submission = pd.concat(frames, ignore_index=True).sort_values("invoice_id")
    submission["expected_total_cents"] = submission["expected_total_cents"].astype("Int64")
    return submission


if __name__ == "__main__":
    OUT_DIR.mkdir(exist_ok=True)
    submission = build()
    submission.to_csv(OUT_DIR / "submission.csv", index=False)

    print(f"submission.csv: {len(submission)} rows, {int(submission['flagged'].sum())} flagged")
    print(f"  expected_total asserted on {int(submission['expected_total_cents'].notna().sum())} rows")
    print()
    print(submission.groupby(submission["invoice_id"].str.slice(4, 6)).agg(
        rows=("flagged", "size"), flagged=("flagged", "sum")
    ).to_string())
