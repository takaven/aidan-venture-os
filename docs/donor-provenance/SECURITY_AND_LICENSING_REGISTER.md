# Security and Licensing Register — Gate 0

No secret value is stored in this repository or reproduced in this record.

## Licensing boundaries

| Donor | Status | Gate 0 rule |
|---|---|---|
| `aidan-managing-director` | **UNRESOLVED** — no root `LICENSE` or `LICENSE.md` observed on current `main`. | No implementation import until exact candidate provenance/licence is established. |
| `ai-dan-factory` | MIT, Copyright 2026 Ismael. | Preserve required notice/attribution for substantial copied material. |
| `AI-DAN-FRAMEWORK` | MIT, Copyright 2025 JeweledTech. | Treat as third-party provenance; preserve notice/attribution and verify exact candidate origin. |
| `idea-factory` | MIT, Copyright 2025 Ismael Sudally. | Preserve required notice/attribution for substantial copied material. |
| `autonomous-product-factory` | **UNRESOLVED** — README says MIT but no root `LICENSE`/`LICENSE.md` was observed. | README claim alone does not authorize code import. |
| `dyad-reworked` | Split: Apache-2.0 outside `src/pro/`; FSL-1.1-ALv2 in `src/pro/`, with competing-use restriction until future licence conversion. | Exact path/version/date check required. No `src/pro/` import unless licence eligibility is explicitly resolved. Preserve Apache/FSL notices as applicable. |
| `ai-dan-factory-core` | **UNRESOLVED** — no root `LICENSE` or `LICENSE.md` observed. | No implementation import. |
| `hybrid-micro-offer-factory` | MIT, Copyright 2026 Ismael. | Process donor only unless later exact code is separately justified. |

**Global prohibition:** no donor code with unresolved licensing enters canonical implementation. There are no blind repository-wide licence assumptions and no bulk merges.

## Historical secret-risk review

The review was deliberately bounded and read-only: repository/root filenames and known security-relevant evidence were inspected; secret values were not requested, displayed or stored. This is not a full Git-history secret scan.

| Repository | Finding | Classification | Required action |
|---|---|---|---|
| `aidan-managing-director` | Root `.env.example` observed; no root real `.env` in bounded listing. | `BENIGN` for observed example file; deeper history `UNRESOLVED`. | Before any future salvage, scan exact candidate/history needed for that gate. |
| `ai-dan-factory` | Root `.env.example` observed; no root real `.env` in bounded listing. Merged PR #13 reports earlier redaction weaknesses. | `HISTORICAL_RISK_REQUIRING_ROTATION_CHECK` for any credentials that may have appeared in historical logs; example file itself `BENIGN`. | Never trust old token hygiene; use newly scoped credentials in canonical system. |
| `AI-DAN-FRAMEWORK` | Root `.env.example` observed; no root real `.env` in bounded listing. | `BENIGN` for observed example file; history `UNRESOLVED`. | Exact-path/history scan before implementation import. |
| `idea-factory` | Root `.env.example` observed; README references backend env templates. | `BENIGN` for template/example filenames; nested/history review `UNRESOLVED`. | Do not copy environment files; inspect exact candidate paths later. |
| `autonomous-product-factory` | README instructs `.env.example`; no secret value inspected. | `UNRESOLVED` beyond documented example usage. | Exact candidate/history scan if archaeology ever becomes implementation salvage. |
| `dyad-reworked` | Secret/provider work exists; split licence is the larger immediate import constraint. No secret value inspected. | `UNRESOLVED` for historical credential exposure. | Exact candidate/history scan before any adapted code is imported. |
| `ai-dan-factory-core` | No secret value inspected; skeleton/placeholder status makes implementation salvage unlikely. | `UNRESOLVED` but low Gate-0 relevance. | Scan only if future salvage is proposed. |
| `hybrid-micro-offer-factory` | **Tracked `.env` and `.env.production` observed on `main`.** Contents intentionally not read. | `HISTORICAL_RISK_REQUIRING_ROTATION_CHECK` | Owner should confirm credentials represented in those historical files have been revoked/rotated. Treat filenames as exposure indicators, not proof of live secrets. Never copy these files. |

## Canonical repository security baseline

- `.gitignore` rejects real `.env` variants, private-key files, local databases and common build/runtime artifacts.
- `tests/gate0_integrity.py` fails if a forbidden `.env` file, dependency manifest, runtime implementation file in reserved directories, or common credential/private-key pattern is tracked.
- No donor implementation code is present in the canonical repository.
