# Write-up

## What I built

Two layers, separated deliberately because they carry very different weights of
evidence. Coverage is uneven and the submission says so: 835 hospital_4
invoices are audited against their contract, and the other 3,107 carry
structural findings only.

The first needs no contract at all. It catches invoices that contradict
themselves: a line total that is not quantity times unit price, an invoice total
that is not the sum of its lines, a reused invoice id, a service date after the
invoice that bills it, a malformed date, a contract number that belongs to
another hospital. Each of these is a breach I can point to a clause for — cl.
4.4 and 11.1–11.3 in hospital_4, and the equivalents in the others. On the
hospital_1 development set this layer scores precision 1.000: 29 flags, 29 real
errors, no false positives. Six label categories are fully covered by it.

I sequenced it this way because the structural layer generalises for free: the
same code runs on all five hospitals with no contract work at all, and it
already accounts for 148 of the 151 flags in the submission. Contract pricing
costs a full parse per contract and generalises to nothing, so it was the part
to spend the remaining time on, once, properly.

The second layer parses a contract into one rule schema — base rates, threshold
premiums, non-business-day uplifts, cumulative volume discounts, daily caps,
bundles, exclusion windows — and re-prices every line in the order the contract
mandates, rounding half-up after each step. I implemented and validated this on
hospital_4 only.

Overall on hospital_1: **precision 0.892, recall 0.569, F1 0.695, Brier 0.0404.**

## How I measured

Everything is measured on hospital_1, which never enters the submission. I ran
the pipeline against the labels after each change rather than at the end, which
is how the two most important decisions below got made.

Confidence is not a feeling; each value is a measured rate:

| confidence | meaning | observed accuracy on dev |
|---|---|---|
| 0.95 | structural breach | 1.000 |
| 0.90 | clean, fully re-priced under the contract | 0.996 |
| 0.85 | clean, contract partly applied (some line unmatched) | 0.963 |
| 0.70 | pricing disagrees, nothing corroborates it | 0.500 |
| 0.60 | clean, but the contract is not priced by this pipeline | — |

I set 0.85 rather than 0.60 for the partly-priced rows because the dev set says
so: they are right 96.3% of the time. I had them at 0.60 first, which pushed
Brier from 0.040 to 0.126 — being too cautious is a calibration error in exactly
the same way that being too confident is, and only the measurement caught it.

The 0.60 tier matters most for how the submission should be read. It covers
hospitals 2, 3 and 5 in full — the three I chose not to price, for the reasons
in the decision log. It carries no dev figure because hospital_1 *is* priced, so
the dev set cannot tell me what the structural checks are worth on their own
against an unpriced contract. Absent that measurement I claim less rather than
borrow a number from a case that does not apply. For those rows, "not flagged"
means the invoice does not contradict itself, and nothing more.

## Where I was uncertain, and why

**Description matching is the bottleneck, not pricing.** Billing descriptions are
compressed shorthand — `INTENS PALL WD BD OCC /SA-8184` — that shares almost no
characters with `Intensive Palliative Ward Bed Occupancy`. Direct similarity gave
a mean score of 0.370 and matched nothing usefully. I built an abbreviation
dictionary from the token frequencies in the line-item files themselves, which
lifted the mean to 0.899. Even so, I only accept a match that also leads the
runner-up by a margin, and everything below that stays unpriced. That single
threshold is why recall on the price-based categories is low: the price
comparison is not wrong, it simply never runs on those lines.

**I rejected my own first pricing implementation.** It flagged any line whose unit
price differed from the base rate. On hospital_1 that scored precision 0.125 —
182 false positives — because premiums, discounts and bundles move the price
legitimately. I discarded it rather than ship it, which is what led to the full
re-pricing engine.

**Two ambiguities I could not settle.** Whether the daily cap applies before or
after a premium changes a handful of totals (recall on `daily_cap_exceeded` is
0.50). And hospital_1 cl. 10 says a service inside an exclusion window is "not
billable" — I read that as the later service being unpayable, but if the intent
is the reverse, the sign of the error flips.

**hospital_5 I left unpriced on purpose.** Its cl. 3.3 requires a facility
multiplier and a plan-tier multiplier on every line, from Tables 2 and 3. Those
tables exist only in the separate rate-tables PDF; they are absent from the .md
and .txt. Pricing from base rates alone would have produced a wrong expected
total for every hospital_5 invoice while looking perfectly confident. I would
rather submit 1,050 honest structural verdicts than 1,050 confident wrong ones.

## What I would do with another week

**Days 1–2 — unit basis, then cross-invoice rules.** The contracts state a unit
basis per service and the invoices carry `unit_basis_as_billed`; I never compare
them. That is 11 labelled cases at recall 0.36 and the cheapest remaining win.
Then the same-service-same-patient-same-date rule (cl. 11.4), which is the whole
of `cross_invoice_duplicate` at recall 0.25.

**Day 3 — hospital_5's multiplier tables.** Extract Tables 2 and 3 from the PDF,
validate the extracted numbers against the text by hand, and turn on pricing for
its 1,050 invoices. The engine already has the multiplier steps in the right
place in the adjustment order.

**Day 4 — hospital_2 and hospital_3.** hospital_2 states each rate in one
formulaic sentence, so extraction is regular; hospital_3 needs date-effective
rates because of its amendment, which the schema supports but I have not
exercised.

**Day 5 — raise recall by fixing matching, not by lowering the threshold.** Right
now the margin test is a blunt instrument. I would corroborate a weak match
against the billed unit basis and the billed price before rejecting it, which
should recover much of the suppressed recall without reintroducing false
positives.

**Days 6–7 — resolve the two ambiguities against the labels, and build a
regression test per label category** so a change that fixes one category and
breaks another cannot pass silently.

The ordering is deliberate: unpriced hospitals first, because a hospital with no
contract applied is a larger gap than an imperfect rule on a hospital that has
one.
