# Arm C0 — General expert prompt (control)

> **Status:** PLACEHOLDER. Not yet authored. See `../README.md` for the frozen design.

**Role in the experiment:** the baseline. A strong, generic venture-analyst prompt with **no** AIDAN
structure and **no** AIDAN doctrine. Same model, same frozen evidence pack as every other arm.

TODO — author the prompt. It must:
- Present the case's frozen evidence pack and ask for a GO / KILL / DEFER decision plus the single
  cheapest action that would materially change the decision.
- Use only the supplied evidence (no live research / web / tools).
- Emit the decision in the shared machine-readable output schema the scorer expects (schema TODO).
- Contain **no** AIDAN-specific scaffolding, vocabulary, or staged process (that is what C1 and T
  add).
