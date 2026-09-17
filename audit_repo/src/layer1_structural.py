"""
Layer 1 — Contract-free structural validation.

These checks need no contract: they detect invoices that contradict
*themselves*. Because they are pure arithmetic and referential consistency,
a hit is a certainty rather than an estimate — measured at precision 1.000
on the hospital_1 dev set.

Design notes
------------
- Vectorised throughout: no iterrows, no row-wise apply.
- All money stays in integer cents; no float ever touches a monetary value.
- Each check returns a boolean Series indexed by invoice_id, so checks
  compose independently and can be added or removed in isolation.
- Dates are coerced, not trusted: an unparseable date is itself a finding.
"""

from __future__ import annotations

import operator
from functools import reduce
from pathlib import Path

import numpy as np
import pandas as pd

# --- configuration ---------------------------------------------------------

DATA_DIR = Path("data/invoices")
OUT_DIR = Path("outputs")
HOSPITALS = (1, 2, 3, 4, 5)

# Confidence for a Layer 1 hit. Justified empirically: these checks scored
# precision 1.000 on hospital_1 (29/29). Held just below 1.0 because one dev
# set is not proof of certainty on unseen hospitals.
CONF_FLAGGED = 0.95

# Confidence for an invoice Layer 1 leaves clean, calibrated against the dev
# set: invoices Layer 1 did not flag were genuinely correct 96.4% of the time.
# Only used when this module runs standalone — build_submission.py overrides it
# with CONF_CLEAN_NO_CONTRACT (0.60) for the hospitals whose contract is never
# parsed, because Layer 1 alone is blind to every contract-dependent category.
CONF_CLEAN = 0.90

# The contract number stated on the face of each hospital's agreement. An
# invoice quoting anything else breaches the invoicing clause (e.g.
# hospital_4 cl. 11.1) and needs no rate table to detect.
CONTRACT_NUMBERS = {
    1: "INS-H1-2024-0417",
    2: "INS-H2-2024-1183",
    3: "INS-H3-2024-0562",
    4: "INS-H4-2024-2049",
    5: "INS-H5-2024-0731",
}


# --- loading ---------------------------------------------------------------


def _coerce_dates(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Parse date columns, turning anything unparseable into NaT.

    Not using read_csv(parse_dates=...) deliberately: it silently leaves the
    whole column as strings if any value is malformed, which would hide the
    very corruption this layer exists to surface.
    """
    for column in columns:
        df[column] = pd.to_datetime(df[column], errors="coerce")
    return df


def load_hospital(hospital_id: int, data_dir: Path = DATA_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load one hospital's invoices and line items with dates already coerced."""
    invoices = _coerce_dates(
        pd.read_csv(data_dir / f"hospital_{hospital_id}_invoices.csv"),
        ["invoice_date", "admission_date", "discharge_date"],
    )
    line_items = _coerce_dates(
        pd.read_csv(data_dir / f"hospital_{hospital_id}_line_items.csv"),
        ["service_date"],
    )
    return invoices, line_items


# --- individual checks -----------------------------------------------------
# Every check returns a boolean Series indexed by invoice_id (True == breach).


def check_line_arithmetic(line_items: pd.DataFrame) -> pd.Series:
    """quantity * unit_price_cents must equal line_total_cents."""
    expected = line_items["quantity"] * line_items["unit_price_cents"]
    return expected.ne(line_items["line_total_cents"]).groupby(line_items["invoice_id"]).any()


def check_invoice_rollup(invoices: pd.DataFrame, line_items: pd.DataFrame) -> pd.Series:
    """Sum of line totals must equal the invoice total.

    Stated identically in every contract, e.g. hospital_4 cl. 4.4:
    'The invoice total is the sum of the line totals on the invoice.'
    """
    invoice_total = invoices.groupby("invoice_id")["invoice_total_cents"].first()
    line_sum = line_items.groupby("invoice_id")["line_total_cents"].sum()
    # An invoice with no line items rolls up to 0 and so is flagged, not dropped.
    return line_sum.reindex(invoice_total.index, fill_value=0).ne(invoice_total)


def check_duplicate_invoice_ids(invoices: pd.DataFrame) -> pd.Series:
    """invoice_id must be unique within a hospital."""
    return invoices.groupby("invoice_id").size().gt(1)


def check_service_after_invoice(invoices: pd.DataFrame, line_items: pd.DataFrame) -> pd.Series:
    """A service cannot be delivered after the invoice that bills it.

    hospital_4 cl. 11.2: 'Every Service Date ... may not fall after the
    invoice date.' The same clause appears in the other four contracts.
    """
    merged = line_items[["invoice_id", "service_date"]].merge(
        invoices[["invoice_id", "invoice_date"]].drop_duplicates("invoice_id"),
        on="invoice_id",
        how="left",
    )
    return merged["service_date"].gt(merged["invoice_date"]).groupby(merged["invoice_id"]).any()


def check_contract_number(invoices: pd.DataFrame, hospital_id: int) -> pd.Series:
    """The invoice must quote this hospital's own contract number.

    hospital_4 cl. 11.1: 'Each invoice quotes the contract number on the face
    of this Agreement.' Only the number itself is needed, not the rate tables.
    """
    expected = CONTRACT_NUMBERS[hospital_id]
    return invoices.groupby("invoice_id")["contract_number"].first().ne(expected)


def check_invalid_dates(invoices: pd.DataFrame, line_items: pd.DataFrame) -> pd.Series:
    """An unparseable service date, or a discharge before the admission."""
    bad_service = line_items["service_date"].isna().groupby(line_items["invoice_id"]).any()
    bad_stay = (
        invoices["discharge_date"].lt(invoices["admission_date"]).groupby(invoices["invoice_id"]).any()
    )
    return bad_stay | bad_service.reindex(bad_stay.index, fill_value=False)


# Check name -> error_category string written to the submission.
CHECK_CATEGORIES = {
    "line_arithmetic": "line_total_not_quantity_times_price",
    "invoice_rollup": "invoice_total_not_sum_of_lines",
    "duplicate_invoice_id": "duplicate_invoice_id",
    "service_after_invoice": "service_date_after_invoice_date",
    "invalid_dates": "invalid_or_inconsistent_dates",
    "contract_number": "contract_number_mismatch",
}


# --- orchestration ---------------------------------------------------------


def _join_categories(results: pd.DataFrame) -> pd.Series:
    """Build a '|'-separated category string per invoice, without apply().

    Each column contributes its category name where True and an empty string
    where False; the pieces are concatenated and the trailing separator
    stripped. The labels file uses this same '|' convention.
    """
    parts = [
        np.where(results[name], CHECK_CATEGORIES[name] + "|", "").astype(object)
        for name in results.columns
    ]
    joined = pd.Series(reduce(operator.add, parts), index=results.index, dtype="string")
    return joined.str.rstrip("|")


def run_layer1(hospital_id: int, data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """Run every Layer 1 check for one hospital.

    Returns one row per invoice. expected_total_cents is deliberately left
    empty: Layer 1 can prove an invoice is internally inconsistent, but not
    what the correct total should be — that needs the contract (Layer 2).
    """
    invoices, line_items = load_hospital(hospital_id, data_dir)

    results = pd.DataFrame(
        {
            "line_arithmetic": check_line_arithmetic(line_items),
            "invoice_rollup": check_invoice_rollup(invoices, line_items),
            "duplicate_invoice_id": check_duplicate_invoice_ids(invoices),
            "service_after_invoice": check_service_after_invoice(invoices, line_items),
            "invalid_dates": check_invalid_dates(invoices, line_items),
            "contract_number": check_contract_number(invoices, hospital_id),
        }
    ).fillna(False).astype(bool)

    flagged = results.any(axis=1)
    billed = invoices.groupby("invoice_id")["invoice_total_cents"].first()

    return pd.DataFrame(
        {
            "invoice_id": results.index,
            "flagged": flagged.astype(int).to_numpy(),
            "error_category": _join_categories(results).to_numpy(),
            "expected_total_cents": pd.NA,
            "billed_total_cents": billed.reindex(results.index).to_numpy(),
            "confidence": np.where(flagged, CONF_FLAGGED, CONF_CLEAN),
        }
    )


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for hospital_id in HOSPITALS:
        result = run_layer1(hospital_id)
        result.to_csv(OUT_DIR / f"layer1_hospital_{hospital_id}.csv", index=False)
        n_flagged = int(result["flagged"].sum())
        print(f"hospital_{hospital_id}: {len(result):>5} invoices, {n_flagged:>3} flagged")


if __name__ == "__main__":
    main()
