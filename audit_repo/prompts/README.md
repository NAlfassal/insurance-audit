# Prompts

Two prompts, both versioned. Neither runs in the default pipeline — the
submission reproduces with regex table parsing and no API key — but both were
written and tested against the real contracts, and the second is the path I
would take to raise matching recall with another week.

## `extract_base_rates_v1.txt`

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

## `resolve_ambiguous_match_v1.txt`

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
- **Confidence is defined against concrete situations** rather than left to
  taste, and null is stated as a valid answer with its cost made explicit
  relative to the cost of a wrong answer.

## What I would change next

`extract_base_rates_v1` has not been run against hospital_5, whose multiplier
tables live in a separate PDF. It would need a second pass that takes the
extracted tables as input rather than the agreement text, and I would want to
diff the extracted numbers against the PDF by hand before trusting any of them.
