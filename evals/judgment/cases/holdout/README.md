# Holdout cases — SEALED

> **Status:** EMPTY / PLACEHOLDER. **12–15 sealed** holdout cases go here. Not yet authored — do
> **not** implement case contents in the skeleton PR. See `../../README.md` for the frozen design.

## ⚠️ Sealing rule (frozen)

**Do NOT look at holdout case contents or answer keys while building the harness.** The holdout is
scored **exactly once**, after the harness, prompts, rubric, and PASS/AMBIGUOUS/FAIL thresholds are
frozen and validated on the development set. Inspecting the holdout — or letting the harness peek at
its answer keys — **invalidates** the affected cases (contamination kill, `../../README.md`).

Same schema as the development cases (fixed frozen evidence pack + metadata + sealed answer key), but
kept sealed. Author these separately from harness development, ideally with the answer keys stored so
they are not read at run time.
