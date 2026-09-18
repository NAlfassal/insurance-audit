# Decision log

One page of the judgement calls behind the submission: what I assumed, what I
could not resolve, and what I decided in each case.

## Scope decisions

**I priced hospital_4 only; hospitals 2, 3 and 5 get structural checks only.**
I could not validate a pricing implementation for four contracts inside the time
budget, so I picked one and did it properly. I chose hospital_4 because it is the only one that is a single
document with every mechanism in a parseable table and an explicit order of
adjustments (cl. 4.1), so I could write the pricing logic against a
contract whose rules are unambiguous. I would rather state partial coverage in
the submission than price four contracts with a parser I never validated.

**I excluded hospital_5 from pricing deliberately, not by oversight.**
Its Tables 2 and 3 — the facility and plan-tier multipliers that cl. 3.3
requires for *every* line — exist only in
`network_reimbursement_agreement_rate_tables.pdf`. They are absent from the
`.md` and `.txt` versions. Pricing from base rates alone would have given a wrong
expected total for every hospital_5 invoice while looking confident, so I priced
none of them.

**I ignored hospital_2's scanned PDF as a source.**
`master_services_agreement_scanned.pdf` carries the same text as the clean
version but with OCR corruption ("Parfies", "Agrcement", "freated", "cvent").
I worked from the clean `.md`. If the two ever disagree materially I have
treated the clean text as authoritative, and I did not verify that clause by
clause.

## Assumptions

**hospital_1 has no facility or plan-tier differential.** Its cl. 1.2 and 1.3
state a single facility and a single tier, so those two steps of the
adjustment order are identity. The pricing engine still models them, so the
same code ports to hospital_5 once its multiplier tables are read.

**"Business Day" means Monday to Friday.** Public holidays are not enumerated
in any contract and no holiday calendar was supplied. I therefore treat a public weekday as a
business day, which will mis-price any non-business-day uplift falling on a
holiday.

**Cumulative utilisation counted hospital-wide and across patients**, per
hospital_1 cl. 7.1, in service-date then line-id order per cl. 7.2. Where a
contract is less explicit I assumed the hospital_1 convention.

**Duplicate invoice ids keep their own line items.** Line items are joined on
`invoice_id`, so a reused id pools the line items of both invoices. This is
visible in the four `duplicate_invoice_id` invoices and means their recomputed
totals are unreliable; the structural flag is still correct.

## Ambiguities found and not resolved

**Rounding interacts with the daily cap.** Whether the cap applies to the
quantity before or after a premium changes the total in a handful of cases. I
could not settle it from the text; recall on `daily_cap_exceeded` is 0.50 and
this is my best explanation for it.

**Whether an excluded service is unpayable or merely flagged.** hospital_1
cl. 10 says a service inside an exclusion window is "not billable". I read that
as the later service being unpayable and price it at quantity zero. If the
intent is the reverse, the sign of the error flips. Recall on
`exclusion_window_violation` is 0.50.

**`unit_basis_as_billed` is not validated against the contract's unit basis.**
The contracts state a unit basis per service (per night, per hour, per test)
and the invoices carry their own. Comparing them would catch
`wrong_unit_basis` directly, where recall is currently 0.36 and comes only
from incidental arithmetic failures. This is the highest-value check I did not
get to, and it is first on my list for another week.

## Approaches considered and dropped

**Base-rate-only price comparison — my own first attempt.** It flagged a line
whenever its unit price differed from the base rate. Measured on hospital_1:
precision 0.125, 182 false positives on 913 invoices. A billed
price legitimately differs from the base rate whenever a premium, discount or
bundle applies, so it flags mostly correct invoices. I discarded it and
built the full re-pricing engine instead.

**A classifier trained on hospital_1.** I ruled this out on principle: the label
depends on the contract, not the invoice, so a model trained on hospital_1's
rates would transfer its rates to hospitals with different ones. The dev set
also holds only ~3 examples per error category, and the task asks for an exact
`expected_total_cents`, which a classifier does not produce.

**Semantic embeddings for description matching.** My abbreviation dictionary
plus token and character similarity reached a mean match score of 0.899. I did
not add embeddings because that is a dependency and a model download for a gain
I had no evidence I needed.

## Confidence values

I set every value from the dev set rather than by intuition:

| confidence | meaning | n (dev) | observed accuracy |
|---|---|---|---|
| 0.95 | structural breach, or both layers agree | 29 | 1.000 |
| 0.90 | clean, and fully re-priced under the contract | 224 | 0.996 |
| 0.85 | clean, contract partly applied (some line unmatched) | 652 | 0.963 |
| 0.70 | pricing disagrees alone | 8 | 0.500 |

The 0.70 tier is the weak one: pricing-only disagreements are right about half
the time, so that is what I claim for them. The 0.60 tier covers every invoice I
never priced — the three unparsed hospitals, and any hospital_4 invoice with an
unmatched line. There, "not flagged" means the invoice does not contradict
itself, and nothing more.
