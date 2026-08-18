# Gate 0 — Preserve & Canonicalise — Local Execution Record

**Execution date:** 2026-08-18
**Scope:** Local portion only. Remote GitHub creation/push is explicitly deferred by owner instruction.

## Objective

Establish a clean, provenance-aware local canonical repository and independently baseline the eight historical donor repositories without beginning Gate 1 or importing donor implementation code.

## First local execution

The first Gate 0 local execution substantively passed and produced these transient commits:

- `3d03c6f9f53da21a124054ba0abf11ef2b311c63`
- `4a97ec96c83bd0ba30251f5659eae242ca637861`

**Status:** Historical execution evidence only — Git object database unavailable.

The working-tree documents survived, but the transient sandbox no longer contained `.git`. The original commit objects therefore cannot be recovered from the available artifacts and must not be manufactured or represented as recovered.

## Replacement canonicalisation execution

A bounded preservation correction was authorized to supersede the unrecoverable transient Git history while preserving the accepted Gate 0 substance.

Reconstruction used the surviving verified Gate 0 repository documents and integrity checker. Architecture, donor dispositions, licensing posture, secret-risk findings and the Gate 0 substantive conclusions were not reopened.

The following material was reconstructed rather than recovered byte-for-byte from the lost workspace:

- `.gitignore`, recreated as a minimal Gate-0-appropriate exclusion policy;
- empty architectural directories represented by `.gitkeep` files;
- `.github/workflows/gate0-integrity.yml`, added before preservation so the future unchanged remote push can produce baseline GitHub Actions evidence without changing canonical history.

The replacement Git history created by this correction is the recoverable canonical history intended for unchanged push to `takaven/aidan-venture-os`. Its final HEAD and artifact hashes are deliberately recorded in the external `GATE_0_PRESERVATION_RECEIPT.md`, not inside this committed file, to avoid self-reference.

## Actions executed across Gate 0

1. Created a fresh canonical repository on `main` containing only Gate 0 structure, documentation and verification material.
2. Verified current GitHub accessibility/visibility/archive/default-branch metadata and exact default-branch HEAD SHAs for all eight donors during the first local execution.
3. Inspected meaningful branch/PR signals required for finite salvage decisions; selected PR metadata was independently fetched where status/provenance mattered.
4. Verified root licensing where available and recorded unresolved licensing explicitly.
5. Performed a bounded secret-risk filename review without reading or recording secret values; tracked `.env` and `.env.production` in `hybrid-micro-offer-factory` were recorded as historical rotation-check risk, not proof of live credentials.
6. Created the donor register, salvage manifest, security/licensing register and Gate 0 ADRs.
7. Reconstructed a recoverable replacement Git history after the first transient Git object database was lost.
8. Added a minimal Gate 0 GitHub Actions workflow before preservation.
9. Executed local integrity and Git-object verification, bundle verification and recovery-clone verification before declaring the replacement history preserved.

## Evidence classification

- GitHub metadata/files/PR state inspected during the first local Gate 0 run: **Observed**.
- Local reconstruction, Git verification and preservation commands executed during the replacement run: **Observed**.
- Frozen donor roles and architecture: **Source-confirmed** from Programme source files.
- Salvage dispositions: **Strategic judgement**, bounded to later gate-specific verification.
- Tags/releases and any uncommitted historical working-tree content: **Unverified** and not relied upon.

## Known limitations

- The GitHub connector used for the donor review did not provide a reliable tags/releases inventory; no salvage entry depends on one.
- No claim is made about uncommitted local changes that may exist on an owner's machine.
- Secret review is a bounded Gate 0 risk review, not a full historical credential-forensics exercise.
- PR descriptions/test claims are not treated as executed evidence.
- The original transient Git objects are unavailable; only their recorded SHAs remain as historical execution evidence.
- Files generated under `/mnt/data` are not evidence of durable external persistence. The owner must save the preservation artifacts outside the transient sandbox or establish the remote repository.

## Deferred remote verification

The replacement preserved canonical repository must later be pushed unchanged to private `takaven/aidan-venture-os`. Until then the following remain **DEFERRED REMOTE VERIFICATION**, not failures:

- repository exists under `takaven` and is private;
- remote default branch is `main`;
- remote `main` HEAD equals the preserved replacement canonical HEAD;
- the already-committed Gate 0 integrity workflow executes successfully on GitHub Actions;
- required repository/branch settings are verified;
- confirmation that the preserved replacement canonical history was pushed unchanged.

## Human intervention

The owner explicitly approved the bounded recovery/preservation correction after the first transient Git object database was found unavailable. This intervention changes canonicalisation/provenance handling only; it does not alter architecture, donor dispositions or future-gate implementation scope.

No manual code repair, deployment repair or outcome transcription occurred.

## Exit status

The local portion may pass only after the replacement committed tree, Git object database, preservation bundle and recovery clone all pass the required checks. Gate 1 must not begin in this run.
