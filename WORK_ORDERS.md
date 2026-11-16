# Work-Order Register - WORK_ORDERS.md — django-core-micha

**Scope note:** this register starts with the 2026-07 resilience workstream;
older work is in `git log`.

**Repo token: `DCM`.** New IDs are `DCM-<STREAM>-<n>` (e.g. `DCM-NOTIF-10`), per workspace `AGENTS.md` -> "Work-Order Register". The repo token, not the stream, is what makes an ID unique -- `SEC-*`, `DX-*` and `CI-*` recur in other repos, and `NOTIF-*` is mirrored by consumers whose rows point back here. Existing bare `<STREAM>-<n>` IDs are never renamed; when referring to one across repos, name the repo in prose.

## Workstream prefixes

| Prefix | Workstream |
|---|---|
| `RES-*` | Runtime/network resilience (healthz bounding, retry on external calls) |
| `NOTIF-*` | Shared notifications platform (canonical model, router, category×channel prefs, todo channel) |
| `MSG-*` | Shared messaging platform, Phase B (generic scope, policy hooks, encryption-at-rest, ucm chat surfaces; jg-ferien first consumer via MSG-5, spesix deferred to MSG-4 per the 2026-07-31 re-ordering) |
| `SEC-*` | Secrets hygiene (`sync-secrets` destination matrix, `.env`/`.env.local` handling) |
| `DX-*` | Developer/test environment ergonomics (test-DB isolation, local run patterns). **Repo-local:** `ui-core-micha` has its own unrelated `DX-*` series (dev harness), so always qualify the repo when referencing a `DX-n` across repos. |
| `ACT-*` | Shared user-activity domain (generic scope, ping/query endpoints, `ActivityPolicy` hook) — `ui-core-micha` CHART-2 and jg-ferien's own `ACT-*` series consume this. |
| `BILL-*` | Shared subscriptions and AI tokens (Stripe Managed Payments, `billing.yaml`, token ledger, AI cost records); `ui-core-micha` `BILL-*` rows are its UI side |
| `AI-*` | Shared LLM document-extraction capability (spend cap, input preparation, payload salvage, schema contract, provider drivers). `reimbursements` is the first consumer and `jg-ferien` the second; `survey_app`'s CLI assistant is deliberately **not** a consumer. |
| `RCPT-*` | Shared receipt recognition on top of `extraction` (schema, normalisers, consent, accounts hook, REST reading). Consumers: reimbursements, spesix, bigler-consult. |
| `CHAT-*` | Shared AI chat core: persistent chats with a language model, model drivers, read-only tools, transport contract. maana is the template, `cockpit` the first consumer. Distinct from `AI-*` (one-shot document extraction) and `MSG-*` (human-to-human messaging). |
| `DEPS-*` | Third-party dependency compatibility (Django/runtime version pins and the settings-level fallout of moving them). |
| `CI-*` | The repo's own CI/publish workflow (`.github/workflows/publish.yml`): what it installs and runs before publishing. |
| `PERF-*` | Shared-core query/latency fixes surfaced by a consuming app's slow endpoint (mirrors each consumer's own `PERF-*` series; the repo token disambiguates). |
| `REG-*` | Shared signup/registration-context contract (`RegistrationContextSerializer`, the signup-QR token pipeline, and the fields consuming apps embed in it). |
| `AUTH-*` | Shared admin-invite / password-reset link flow (`invitations/mixins.py`, `PasswordResetConfirmView`, invite mail texts) — token lifetimes and the link contract consumed via `ui-core-micha` `PasswordInvitePage`. |

Introduce a new prefix when none fits and add it here. New WOs always get a
prefixed ID; never reuse a bare flat number across workstreams.

The register is `work-orders/register.jsonl` (one JSON object per work order); read and write it only
through `python -I .claude/scripts/wo.py` (`AGENTS.md` -> "Work-Order Register"). Completion details
live in the "Completion" section of each `work-orders/<ID>.md`. The register as it stood before the
migration is archived whole in `work-orders/archive/WORK_ORDERS-until-2026-11-16.md`.

**Repo token:** see the declaration above
