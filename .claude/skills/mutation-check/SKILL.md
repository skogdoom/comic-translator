---
name: mutation-check
description: Verify that a test would actually fail if the thing it guards broke, by deliberately breaking that thing and watching. Use this right after writing or changing any test, after adding a regression test for a bug you just fixed, when consolidating or refactoring tests, and before claiming in a commit message, docstring or reply that something is "covered", "tested" or "held". Also use it when asked whether a suite is any good, or when a test has never been observed to fail. A passing test proves the code passes the test — it says nothing about whether the test bites.
---

# Mutation-check

A passing test proves the code passes the test. It does not prove the test
would notice if the code broke. The only way to find out is to break the code
on purpose and watch what happens.

This is cheap — seconds per mutation when you run only the affected test — and
it routinely finds three different kinds of problem, of which "the test is
weak" is only one.

## The loop

For each distinct claim the test makes:

1. **Break exactly that claim in the thing under test** — one small, plausible
   edit to the code, or to the document or config the test asserts about.
   Never edit the test: making a test fail by changing the test proves
   nothing.
2. **Run only the affected test.** A single test file is usually well under a
   second; the whole suite is minutes. Mutation-checking stops happening when
   each round costs a coffee break.
3. **Confirm it fails, and read the failure.** The right test must fail, at the
   right assertion, for the reason you expect. An import error, a fixture
   blowing up, or a different test going red means the mutation escaped its
   blast radius — you have learned nothing about the test you were checking.
4. **Restore, and verify the tree is clean.**

Then report the count. "Seven mutations, all caught" is a claim someone else
can check. "Well tested" is not.

## Choose mutations a refactor might actually make

The point is not to prove the test detects arbitrary damage — random noise is
caught by everything, so catching it tells you nothing. The question to hold
in mind is: **what could someone change here, believing it harmless, that this
test exists to stop?**

Productive mutations:

- Flip a comparison (`>` to `>=`), or move a boundary by one
- Delete a guard clause, an early return, or a `not`
- Swap two operations that look independent
- Replace a computed value with a constant, or a constant with its neighbour
- Drop one element from a list the code iterates or checks against
- Make an error path succeed silently
- For a test that asserts about prose or config: rename the heading, change
  the key, reword the line it matches on

If nobody would ever write the mutation, neither its survival nor its capture
tells you anything.

## A surviving mutant is a finding, not a chore

Three outcomes, needing three different fixes. Work out which one you have
before reaching for the test file.

**The test is weak.** The common case. Add the case that would have caught it,
or tighten the assertion.

**The code is unreachable or redundant.** Nothing can tell the difference
because there is no difference. Consider deleting it — dead code that looks
live is worse than no code, because the next reader budgets for it.

**Your description of the test is wrong.** The rarest and the most valuable.
A real example: a test's docstring claimed that checking the left margin was
"the whole signal" for telling an indented annotation line from a package
line. Mutating the margin check did not make the test fail — because a
separate `"==" in requirement` check was quietly carrying most of the load.
The test was fine. The sentence explaining it was false, and would have
misled the next person to touch that parser.

Mutation-checking is the only routine that catches that class, because nothing
else compares what a test *does* against what you *said* it does.

## The failure mode: mutants that never ran

A mutation that did not change behaviour is not a passed check, and it is easy
to score one by accident:

- The string you edited was not in the file — a typo, or you guessed at
  content you had not read
- The file you edited is not the one under test
- The test was skipped: a missing optional dependency, a platform guard. SKIP
  is not PASS
- The branch you mutated is not reached by that test at all
- **A cached build ran instead of your edit.** Python's `__pycache__` validates
  a `.pyc` on the source's mtime *in whole seconds* plus its size, so a
  scripted loop that edits and re-runs within the same second can silently
  execute the old bytecode — and a same-length edit (`15.0` to `16.0`, or
  `"bronze"` to `"silver"`) defeats the size check too. The same hazard lives
  in any incremental build or test cache. It is the worst variant because it
  is invisible and non-deterministic: the identical mutation is caught by
  hand and survives in a loop. Clear the cache between mutations rather than
  trusting invalidation.

So before counting a mutant as caught, confirm the test **ran** and **failed**.
And when a mutation produces a pass, ask "did it actually apply?" before
"is the test weak?" — checking that first saves you from strengthening a test
that was never the problem.

## Restore cleanly

Work through version control, not from memory. Copy the file aside first, or
rely on `git diff` and `git checkout` to put it back, and confirm the tree is
clean before moving on. A mutation left behind is the worst kind of bug: it
reads as deliberate work, and it is already committed.

When you plan more than two or three, script the apply-and-restore rather than
hand-editing each one.

## Where the effort pays

Spend it on:

- **Guards and defences** — a limit, a validation, a refusal. A defence that
  nothing holds you to is one the next refactor removes without noticing.
- **A regression test for a bug you just fixed.** Reinstate the original
  defect and confirm the new test catches *that*. If it does not, you have
  written a test for a different bug and the original is still unguarded.
- **Boundaries and thresholds**, where the off-by-one is the entire point.
- **Tests that assert facts about documentation or configuration**, which
  drift silently in a way code does not.
- **Tests you are consolidating.** A helper now serving two callers means one
  weak spot serves both, so gaps that were tolerable become worth closing.

Skip it for pure plumbing, generated code, and tests you expect to delete.

## Reporting

Say what you did and what it found: how many mutations, whether all were
caught, and what any survivor revealed. Include the survivors — they are the
interesting part, and a report with no survivors and no count is
indistinguishable from not having checked.
