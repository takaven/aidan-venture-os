# Arm T — AIDAN staged decision process (treatment)

> **Status:** PLACEHOLDER. Not yet authored. See `../README.md` for the frozen design.

**Role in the experiment:** the treatment. The **full AIDAN structured decision process**, staged
across explicit steps, same model, same frozen evidence pack. This is the arm whose added complexity
the experiment is testing.

TODO — author the staged prompt(s), one stage per step, mirroring the AIDAN Intelligence-plane flow
(`docs/architecture/ARCHITECTURE.md`):
1. Evidence intake from the frozen pack (no live research).
2. Observations → Claims (with evidence category per claim).
3. Interpretations → Assumptions (surface the decisive assumptions).
4. Kill Case (the condition that would falsify the opportunity).
5. Capital decision under the scarce frame (cheapest decisive action).
6. Highest-value next action + GO / KILL / DEFER.

Requirements:
- Driven by `../runners/treatment_runner.py` (staged), unlike the single-pass controls.
- Uses only the supplied evidence (no live research / web / tools).
- Emits the **same** machine-readable output schema as C0/C1 for apples-to-apples scoring (schema
  TODO).
- Records tokens/cost across **all** stages (the cost the simplicity kill test weighs against C1).
