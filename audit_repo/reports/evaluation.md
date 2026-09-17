# Evaluation report

Measured on hospital_1 (913 invoices, 58 labelled erroneous). hospital_1 is
never in the submission; it is the only place performance can be measured, so
every number below comes from it, and every confidence value in
`submission.csv` is set from this table rather than chosen.

## Invoice-level flagging

| | value |
|---|---|
| Precision | **0.892** |
| Recall | **0.569** |
| F1 | **0.695** |
| TP / FP / FN | 33 / 4 / 25 |

Precision was prioritised over recall throughout, because someone has to
review every flag. Where a threshold traded the two, the operating point was
chosen on measured F1, not preference — see "Matching threshold" below.

## Calibration

| stated confidence | n | observed accuracy |
|---|---|---|
| 0.95 | 29 | 1.000 |
| 0.90 | 224 | 0.996 |
| 0.85 | 652 | 0.963 |
| 0.70 | 8 | 0.500 |

**Brier score 0.0404.** Each tier lands within about 1.5 points of its stated
value. The 0.70 tier is the honest weak spot: a pricing disagreement with no
structural corroboration is right about half the time, and it is stated that
way rather than rounded up.

The 0.60 tier does not appear here because it cannot: it is reserved for
hospitals whose contract was never parsed, and hospital_1's was.

## Per-category recall

Six categories are fully covered. These are the structural checks, which need
no contract and are deterministic:

| category | n | recall |
|---|---|---|
| contract_number_mismatch | 5 | 1.00 |
| duplicate_invoice_id | 5 | 1.00 |
| service_date_after_invoice_date | 5 | 1.00 |
| malformed_service_date | 6 | 1.00 |
| line_total_arithmetic | 6 | 1.00 |
| invoice_total_mismatch | 6 | 1.00 |

Contract-dependent categories are partial:

| category | n | recall |
|---|---|---|
| service_date_out_of_window | 5 | 0.80 |
| bundle_not_applied | 5 | 0.60 |
| daily_cap_exceeded | 4 | 0.50 |
| exclusion_window_violation | 4 | 0.50 |
| volume_discount_incorrectly_applied | 4 | 0.50 |
| premium_incorrectly_applied | 6 | 0.50 |
| wrong_unit_basis | 11 | 0.36 |
| unknown_service | 12 | 0.33 |
| unit_price_mismatch | 10 | 0.30 |
| cross_invoice_duplicate | 4 | 0.25 |
| volume_discount_omitted | 4 | 0.25 |
| premium_omitted | 3 | 0.00 |

## Failure analysis — four systematic patterns

### 1. Conservative matching suppresses the price-based categories
The dominant failure. A line is only re-priced when its description matches a
contracted service by score ≥ 0.55 **and** leads the runner-up by ≥ 0.20.
Descriptions that fail the margin test are left unpriced, so any pricing error
on them is invisible.

*Example:* `unit_price_mismatch` has recall 0.30 — not because the price
comparison is wrong, but because 7 of the 10 invoices carrying it contain at
least one line the matcher declined to identify.

This is a deliberate trade. At margin 0.15 recall rises to 0.690 but precision
falls to 0.606 (26 false positives). The measured sweep:

| margin | precision | recall | F1 | FP |
|---|---|---|---|---|
| 0.15 | 0.606 | 0.690 | 0.645 | 26 |
| **0.20** | **0.892** | **0.569** | **0.695** | **4** |
| 0.25 | 1.000 | 0.500 | 0.667 | 0 |

### 2. Unit basis is never checked against the contract
`wrong_unit_basis` (11 cases, recall 0.36) and part of `unknown_service` come
from a check that does not exist: the contracts state a unit basis per service
and the invoices carry `unit_basis_as_billed`, but the two are never compared.
The cases caught were caught incidentally, through arithmetic or total
mismatches.

*Example:* a service contracted "per night of occupancy" billed "per hour" is
currently priced at the correct rate for the wrong basis and passes.

### 3. Cross-invoice rules are only partly implemented
`cross_invoice_duplicate` (recall 0.25) and `premium_omitted` (recall 0.00)
both depend on aggregating across invoices. Daily aggregates and cumulative
utilisation are computed hospital-wide, but the same-service-same-patient-
same-date duplicate rule (hospital_1 cl. 11.4) is not implemented at all, and
the premium threshold is evaluated only where the service matched.

*Example:* `premium_omitted`, 3 cases, 0 caught — the premium is omitted by
the hospital and the engine agrees with the hospital because the relevant
lines were unmatched.

### 4. The four false positives are rounding-order artefacts
All four FPs are invoices where the recomputed total differs from the billed
total by a small amount on a line carrying both a premium and a discount. The
contract rounds after each step (cl. 4.2), and the order in which the cap and
the premium interact is ambiguous (see decision log). These are the residual
cost of asserting a total at all.

## What the submission actually claims

3,942 rows, 151 flagged (3.8%).

| hospital | rows | flagged | basis |
|---|---|---|---|
| hospital_2 | 1,125 | 40 | structural only |
| hospital_3 | 932 | 38 | structural only |
| hospital_4 | 835 | 36 | full audit |
| hospital_5 | 1,050 | 37 | structural only |

Confidence is tiered by how much evidence stands behind the row:

| value | rows | meaning |
|---|---|---|
| 0.95 | 148 | structural breach |
| 0.90 | 87 | clean, and fully re-priced under the contract |
| 0.85 | 712 | clean, contract partly applied (some line unmatched) |
| 0.70 | 3 | pricing disagrees, nothing corroborates it |
| 0.60 | 2,992 | clean, but the contract was never parsed |

The 0.60 tier covers hospitals 2, 3 and 5 in full. There, "not flagged" means
only that the invoice does not contradict itself — not that it matches its
contract.

`expected_total_cents` is asserted on 93 rows only — the hospital_4 invoices
where every line matched a contracted service confidently. It is left blank
everywhere else rather than guessed.
