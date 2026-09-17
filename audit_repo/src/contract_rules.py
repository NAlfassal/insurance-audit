"""
Parse a markdown contract into a complete, machine-usable rule set.

Layer 2's first attempt extracted base rates only, and scored precision 0.125
on the dev set: a billed price legitimately differs from its base rate whenever
a premium, discount or bundle applies, so "price != base rate" flagged mostly
correct invoices. Pricing an invoice honestly requires every mechanism, not
just the rate table.

The six mechanisms, and the clauses they come from (hospital_1 numbering):
  §4  base rates            — the rate and unit basis per service
  §5  threshold premiums    — uplift when daily quantity exceeds a threshold
  §6  non-business-day      — uplift when the service date is a weekend
  §7  volume discounts      — discount once cumulative utilisation passes a bar
  §8  daily caps            — maximum billable units per patient per day
  §9  bundles               — substituted rates when a pair share a service day
  §10 exclusion windows     — service not billable near another service

Parsing is regex over markdown tables rather than an LLM call: these contracts
state their rules in regular tables, so a parser is exact, free and repeatable.
The LLM path in layer2_contract_rules.py remains for prose-only contracts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path("data")

# "GBP 1,301.25" -> 130125 cents. Money never becomes a float beyond this point.
MONEY = r"GBP\s*([\d,]+\.\d{2})"


def _cents(amount: str) -> int:
    return int(round(float(amount.replace(",", "")) * 100))


def _first_int(text: str) -> int | None:
    """First integer in a cell, preferring a parenthesised numeral.

    hospital_4 writes thresholds in words with the numeral in brackets —
    "eighty (80)", "fifteen percent (15%)" — so the bracketed form is read
    first and the plain form used as a fallback.
    """
    if bracketed := re.search(r"\((\d+)", text):
        return int(bracketed.group(1))
    if plain := re.search(r"(\d+)", text):
        return int(plain.group(1))
    return None


def _section(text: str, *keywords: str) -> str:
    """Return the body of the '## N. Heading' section whose title contains any
    of the given keywords.

    Contracts number and name their sections differently — hospital_1 has
    "## 7. Cumulative Volume Discounts", hospital_4 has "## 8. Discounts" —
    so sections are found by keyword rather than by an exact heading.
    """
    for match in re.finditer(r"^## \d+\.\s*(?P<title>.+?)$(?P<body>.*?)(?=^## |\Z)", text, re.S | re.M):
        title = match.group("title").lower()
        if any(keyword.lower() in title for keyword in keywords):
            return match.group(0)
    return ""


def _rows(section: str) -> list[list[str]]:
    """Data rows of a markdown table, minus the header and separator rows."""
    lines = [line for line in section.splitlines() if line.strip().startswith("|")]
    return [
        [cell.strip() for cell in line.strip().strip("|").split("|")]
        for line in lines[2:]  # skip header + |---| separator
    ]


@dataclass
class ContractRules:
    """Every pricing mechanism in one contract, keyed by service name."""

    base_rates: dict[str, int] = field(default_factory=dict)
    unit_basis: dict[str, str] = field(default_factory=dict)
    # service -> (daily quantity threshold, uplift factor)
    threshold_premiums: dict[str, tuple[int, float]] = field(default_factory=dict)
    # service -> uplift factor applied on Saturdays and Sundays
    weekend_uplifts: dict[str, float] = field(default_factory=dict)
    # service -> [(cumulative threshold, discount factor)], deepest last
    volume_discounts: dict[str, list[tuple[int, float]]] = field(default_factory=dict)
    # service -> max units per patient per service day
    daily_caps: dict[str, int] = field(default_factory=dict)
    # service -> (partner service, substituted rate for this service)
    bundles: dict[str, tuple[str, int]] = field(default_factory=dict)
    # service -> [(window in days, service whose delivery excludes it)]
    exclusions: dict[str, list[tuple[int, str]]] = field(default_factory=dict)


def parse_contract(path: Path) -> ContractRules:
    """Build a ContractRules from a markdown contract document."""
    text = path.read_text(encoding="utf-8")
    rules = ContractRules()

    # §4 base rates — | Service | Unit basis | GBP x | cap |
    for row in _rows(_section(text, "rate schedule", "base rate")):
        if len(row) < 3 or not (money := re.search(MONEY, row[2])):
            continue
        service = row[0]
        rules.base_rates[service] = _cents(money.group(1))
        rules.unit_basis[service] = row[1]
        # The rate table carries its own daily-cap column ("6 days" / "—").
        if len(row) > 3 and (cap := _first_int(row[3])) is not None:
            rules.daily_caps[service] = cap

    # §5 threshold premiums — | Service | 6 visits | +20% |
    for row in _rows(_section(text, "threshold premium")):
        if len(row) < 3:
            continue
        threshold = _first_int(row[1])
        uplift = re.search(r"\+(\d+)%", row[2])
        if threshold is not None and uplift:
            rules.threshold_premiums[row[0]] = (threshold, 1 + int(uplift.group(1)) / 100)

    # §6 non-business-day uplifts — | Service | +20% |
    for row in _rows(_section(text, "non-business-day")):
        if len(row) >= 2 and (uplift := re.search(r"\+(\d+)%", row[1])):
            rules.weekend_uplifts[row[0]] = 1 + int(uplift.group(1)) / 100

    # §7 volume discounts — | Service | 60 nights | 10% |
    for row in _rows(_section(text, "volume discount", "discount")):
        if len(row) < 3:
            continue
        threshold = _first_int(row[1])
        discount = _first_int(row[2])
        if threshold is not None and discount is not None:
            rules.volume_discounts.setdefault(row[0], []).append(
                (threshold, 1 - discount / 100)
            )

    # §8 daily caps — | Service | 6 days |
    for row in _rows(_section(text, "daily quantity")):
        if len(row) >= 2 and (cap := _first_int(row[1])) is not None:
            rules.daily_caps[row[0]] = cap

    # §9 bundles. Column order differs by contract:
    #   hospital_1: | Service A | Service B | rate A | rate B |
    #   hospital_4: | Service A | rate A | Service B | rate B |
    # so the money columns are located rather than assumed.
    for row in _rows(_section(text, "bundled")):
        if len(row) < 4:
            continue
        money_at = [i for i, cell in enumerate(row) if re.search(MONEY, cell)]
        name_at = [i for i, cell in enumerate(row) if not re.search(MONEY, cell) and cell not in {"", "—"}]
        if len(money_at) != 2 or len(name_at) != 2:
            continue
        service_a, service_b = row[name_at[0]], row[name_at[1]]
        rate_a = _cents(re.search(MONEY, row[money_at[0]]).group(1))
        rate_b = _cents(re.search(MONEY, row[money_at[1]]).group(1))
        rules.bundles[service_a] = (service_b, rate_a)
        rules.bundles[service_b] = (service_a, rate_b)

    # §10 exclusion windows — | Service | 7 days | Other service |
    for row in _rows(_section(text, "exclusion window")):
        if len(row) >= 3 and (window := _first_int(row[1])) is not None:
            rules.exclusions.setdefault(row[0], []).append((window, row[2]))

    # Deepest discount last, so the pricing engine can scan and take the last match.
    for thresholds in rules.volume_discounts.values():
        thresholds.sort()

    return rules


def summarise(rules: ContractRules) -> str:
    return (
        f"{len(rules.base_rates)} base rates | "
        f"{len(rules.threshold_premiums)} threshold premiums | "
        f"{len(rules.weekend_uplifts)} weekend uplifts | "
        f"{len(rules.volume_discounts)} volume discounts | "
        f"{len(rules.daily_caps)} daily caps | "
        f"{len(rules.bundles)} bundle members | "
        f"{len(rules.exclusions)} exclusion windows"
    )


if __name__ == "__main__":
    rules = parse_contract(DATA_DIR / "contracts/hospital_1/provider_services_agreement.md")
    print(summarise(rules))
