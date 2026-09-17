"""
Price every line item the way the contract says, then compare to what was billed.

The contract fixes the order of operations and the rounding, and both matter:

  §3.2  adjustments apply to the base rate strictly in this order:
        (a) bundled substitution, (b) facility multiplier, (c) plan-tier
        multiplier, (d) premium or uplift, (e) cumulative volume discount
  §3.1  round to the nearest cent after EACH step, halves away from zero —
        not once at the end

hospital_1 has no facility or plan-tier differential (§1.2, §1.3), so steps
(b) and (c) are identity here. They are still represented so the engine ports
to contracts that do use them.

Three mechanisms are cross-row, not per-row, and drive the processing order:
  - threshold premiums  depend on the patient's total for that service day
  - volume discounts    depend on cumulative utilisation strictly BEFORE the
                        line, counted in service-date then line-id order (§7.2)
  - daily caps          cap the patient's units per service day (§8)
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd

from abbreviations import expand
from contract_rules import ContractRules, parse_contract

DATA_DIR = Path("data")

CONTRACT_PATHS = {
    1: DATA_DIR / "contracts/hospital_1/provider_services_agreement.md",
    4: DATA_DIR / "contracts/hospital_4/conditional_reimbursement_agreement.md",
}


def round_half_up(value: float) -> int:
    """§3.1 rounding: nearest cent, exact halves away from zero.

    Python's round() is banker's rounding and would give 2 for 2.5, so the
    contract's rule is implemented explicitly via Decimal.
    """
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def price_line_items(line_items: pd.DataFrame, rules: ContractRules) -> pd.DataFrame:
    """Compute the expected unit rate and line total for every line item.

    Returns the frame with expected_unit_cents / expected_line_cents added,
    plus the reason codes explaining which mechanisms fired.
    """
    df = line_items.copy()
    df["service_date"] = pd.to_datetime(df["service_date"], errors="coerce")

    # (§7.2) cumulative utilisation is counted in service-date then line-id
    # order, so the whole frame must be processed in that order.
    df = df.sort_values(["service_date", "line_id"], kind="stable").reset_index(drop=True)

    # --- cross-row aggregates, computed once ---
    # Daily quantity per patient per service per day, for premiums and caps.
    daily_qty = df.groupby(["patient_id", "matched_service", "service_date"])["quantity"].transform("sum")

    # Cumulative utilisation strictly before each line (exclusive), per service.
    cumulative_before = df.groupby("matched_service")["quantity"].cumsum() - df["quantity"]

    # Services delivered to each patient on each day, for bundle detection.
    delivered = set(zip(df["patient_id"], df["matched_service"], df["service_date"]))

    expected_units: list[int | None] = []
    reasons: list[str] = []

    for row, day_qty, prior in zip(df.itertuples(index=False), daily_qty, cumulative_before):
        service = row.matched_service
        if pd.isna(service) or service not in rules.base_rates:
            expected_units.append(None)
            reasons.append("unmatched_service")
            continue

        fired: list[str] = []
        rate = rules.base_rates[service]

        # (a) bundled substitution — both services of the pair on the same day
        if service in rules.bundles:
            partner, substituted = rules.bundles[service]
            if (row.patient_id, partner, row.service_date) in delivered:
                rate = substituted
                fired.append("bundle")

        # (b),(c) facility and plan-tier multipliers: identity for hospital_1

        # (d) premiums — threshold first, then non-business-day, each rounded
        if service in rules.threshold_premiums:
            threshold, factor = rules.threshold_premiums[service]
            if day_qty > threshold:
                rate = round_half_up(rate * factor)
                fired.append("threshold_premium")

        if service in rules.weekend_uplifts and row.service_date.weekday() >= 5:
            rate = round_half_up(rate * rules.weekend_uplifts[service])
            fired.append("weekend_uplift")

        # (e) cumulative volume discount — deepest threshold already exceeded.
        # Thresholds are sorted ascending, so the last match is the deepest (§7.2).
        discount_factor = None
        for threshold, factor in rules.volume_discounts.get(service, []):
            if prior > threshold:
                discount_factor = factor
        if discount_factor is not None:
            rate = round_half_up(rate * discount_factor)
            fired.append("volume_discount")

        expected_units.append(rate)
        reasons.append("|".join(fired))

    df["expected_unit_cents"] = expected_units
    df["adjustments_applied"] = reasons

    # §8 daily caps: units above the cap are simply not payable.
    # Services with no cap get their own quantity as the limit, so clip is a
    # no-op for them and no NaN ever reaches the comparison.
    caps = df["matched_service"].map(rules.daily_caps)
    limit = caps.fillna(df["quantity"])
    payable = df["quantity"].clip(upper=limit).astype("int64")
    df["payable_quantity"] = payable
    df["capped"] = payable.ne(df["quantity"])

    df["expected_line_cents"] = (
        pd.to_numeric(df["expected_unit_cents"], errors="coerce") * df["payable_quantity"]
    )
    return df
