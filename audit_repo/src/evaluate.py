"""
Evaluate the full pipeline against the hospital_1 labels (the dev set).

Reports classification quality and calibration for exactly the verdict that
goes into the submission — both layers combined, not Layer 1 alone. The point
of this script is that every confidence value in submission.csv is *measured
here* rather than chosen by intuition, so the numbers in reports/ can be
reproduced by running it.

Pass --layer1 to score the structural checks in isolation instead, which is how
the precision 1.000 figure for that layer was obtained.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

import sys

from audit import audit_hospital
from layer1_structural import run_layer1

LABELS_PATH = Path("data/labels/hospital_1_labels.csv")
DEV_HOSPITAL = 1


def main() -> None:
    layer1_only = "--layer1" in sys.argv
    preds = run_layer1(DEV_HOSPITAL) if layer1_only else audit_hospital(DEV_HOSPITAL)
    labels = pd.read_csv(LABELS_PATH)

    df = preds.merge(labels, on="invoice_id", validate="one_to_one")
    y_pred = df["flagged"].eq(1)
    y_true = df["is_erroneous"].eq(1)

    tp = int((y_pred & y_true).sum())
    fp = int((y_pred & ~y_true).sum())
    fn = int((~y_pred & y_true).sum())
    tn = int((~y_pred & ~y_true).sum())

    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else float("nan")

    print("=" * 62)
    scope = "LAYER 1 ONLY" if layer1_only else "FULL PIPELINE"
    print(f"{scope} — classification on hospital_1 (dev set)")
    print("=" * 62)
    print(f"  TP={tp}  FP={fp}  FN={fn}  TN={tn}")
    print(f"  precision : {precision:.3f}   <- of what we flag, how much is real")
    print(f"  recall    : {recall:.3f}   <- of all errors, how much we catch")
    print(f"  F1        : {f1:.3f}")

    # Calibration: is the stated confidence honest about how often we are right?
    df["correct"] = y_pred.eq(y_true).astype(int)
    brier = ((df["confidence"] - df["correct"]) ** 2).mean()
    print()
    print("=" * 62)
    print("CALIBRATION (Brier score — lower is better, 0 is perfect)")
    print("=" * 62)
    print(f"  Brier: {brier:.4f}")
    print()
    reliability = (
        df.groupby("confidence")
        .agg(n=("correct", "size"), stated=("confidence", "first"), actual=("correct", "mean"))
        .round(3)
    )
    print(reliability.to_string())

    # Which true error categories does Layer 1 reach, and which does it miss?
    exploded = (
        df.loc[y_true, ["invoice_id", "error_categories", "flagged"]]
        .assign(cat=lambda d: d["error_categories"].str.split("|"))
        .explode("cat")
    )
    coverage = (
        exploded.groupby("cat")
        .agg(total=("invoice_id", "size"), caught=("flagged", "sum"))
        .assign(recall=lambda d: (d["caught"] / d["total"]).round(2))
        .sort_values("recall", ascending=False)
    )
    print()
    print("=" * 62)
    print("RECALL BY TRUE ERROR CATEGORY")
    print("=" * 62)
    print(coverage.to_string())


if __name__ == "__main__":
    main()
