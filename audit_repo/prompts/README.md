# Prompts and AI assistance

## How I used AI

I used Claude as a pair programmer throughout: exploring the data, writing and
refactoring the pipeline, diagnosing where the false positives came from, and
drafting documentation.

The judgement calls are mine, and they are the ones I would want to be asked
about — which contract to price, where to set the matching threshold given the
measured trade-off, discarding my first base-rate comparison once it scored
precision 0.125, leaving hospital_5 unpriced after finding its multiplier tables
missing from the text sources, and what each confidence tier is entitled to
claim. Those are in `reports/decision_log.md`.

The two prompts below are the LLM work itself. Neither runs in the default
pipeline — the submission reproduces with regex table parsing and no API key —
but both were written and tested against the real contracts, and the second is
the path I would take to raise matching recall with another week.

## The prompts

Both versioned.

### `extract_base_rates_v1.txt`

Turns contract prose into a base-rate table.

Design decisions:
- **One entry per rate period, not per service.** hospital_3 reprices mid-term
  by amendment. Asking for one row per service silently loses the second rate.
- **Integer cents, stated with an example.** "GBP 1,701.25 -> 170125" removes the
  ambiguity that produces floats, which then produce rounding errors that look
  like billing errors.
- **An explicit exclusion list.** Naming the clauses that do *not* set a rate
  (notices, audit rights, confidentiality, force majeure) matters most on
  hospital_2, where 22 of 35 articles are boilerplate and only 13 carry rates.
- **Adjustments are out of scope, on purpose.** Premiums and discounts go to
  `ambiguities`, not to `services`. Mixing the two produced a rate table where I
  could not tell a base rate from an adjusted one.
- **An asymmetric instruction on uncertainty:** omit rather than guess, and say
  why. An omission is a gap I can see; a wrong rate propagates into every
  invoice touching that service and looks correct.

### `resolve_ambiguous_match_v1.txt`

Resolves a single description that lexical matching could not settle.

Design decisions:
- **Only ambiguous cases reach it.** 98% of descriptions resolve offline once
  expanded through the abbreviation dictionary. Sending all of them would be
  slower, more expensive, and less accurate than the deterministic path.
- **It is told not to re-rank on surface similarity.** That has already been
  done and failed; repeating it would just return the same wrong answer with
  extra steps.
- **Unit basis and price are given as corroborating evidence, with the
  contract's real adjustment range.** This is the same signal that fixed my
  false-positive problem: a ratio of 5x or 0.03x means the wrong service was
  matched, not that the invoice is wrong.
- **`runner_up` and `why_not_runner_up` are required fields.** They make the
  answer reviewable. Without them I have a label and no way to audit it.
- **The match and its confidence are scored as two separate decisions.** My
  first version listed "null" as if it were a fourth confidence level, which
  conflated *which service is it* with *how sure am I* and produced
  inconsistent output — a null came back with no usable score attached. They
  are now decided in order, and a null is scored on the same scale as a name:
  a confident null is 0.9, an unsure null is 0.5.
- **The cost of each failure is stated, and it is asymmetric.** A null costs one
  unpriced line; a wrong service mis-prices every invoice carrying that
  description and nobody catches it. The prompt says so, because "when in
  doubt, null" is not obvious unless you know what doubt costs.
- **It is told not to round up**, and why: the value is thresholded, so an
  optimistic score is not a harmless courtesy — it puts a wrong match into the
  submission.

### What I would change next

`extract_base_rates_v1` has not been run against hospital_5, whose multiplier
tables live in a separate PDF. It would need a second pass that takes the
extracted tables as input rather than the agreement text, and I would want to
diff the extracted numbers against the PDF by hand before trusting any of them.
