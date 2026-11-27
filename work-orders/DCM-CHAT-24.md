# DCM-CHAT-24 — Chat permission groups: live intersection, remove lifecycle machinery (part of HRAM-CHT-12)

Pointer stub. The authoritative Envelope, Part B and Gate record live in `hram/work-orders/HRAM-CHT-12.md`.

This repository's part:
- reduce the published 3.44.0 group grants to the live rule: each private chat stores its enabled group IDs (nothing = all read groups); every tool offer, invocation and data selector intersects them with the members the app currently declares available;
- remove capability generations, catalogue activation, the frozen baseline and cohort, the authorization snapshot hook, reauthorization and the feature's provider-continuation, upload and Claude-CLI revocation, with their tests and tables;
- keep the read and save API compatible with ui-core-micha 4.41.0; release as 3.45.0.

Reviewed per its own WO ID.

## A. Envelope

**Tier 3** (removes authorization code and migrations in shared core). Copy of the DCM-relevant parts of `hram/work-orders/HRAM-CHT-12.md` Part A; that file stays authoritative.

- **Stored state.** Each private chat stores only the set of enabled group IDs, or nothing. Nothing means every read group is enabled. Group IDs not in the current catalogue are ignored.
- **Effective access.** At every tool offer, tool invocation and app-supplied data or context access: allowed = members of the chat's enabled groups that the app currently declares available to this user, computed live on every check. A chat can only narrow what the user may already do.
- **Timing.** A saved change applies from the next tool offer or call onward; running answers are not interrupted.
- **Editing.** Only the owner of a private, non-archived chat can save; a save replaces the enabled set; a stale save (grant revision mismatch) is refused with the existing conflict response. Shared chats and apps without a registered catalogue keep their current behavior.
- **Wire contract.** Read and save API stay compatible with ui-core-micha 4.41.0 (routes, response fields, error shapes); request fields without meaning are accepted and ignored.
- **Removals (released as additive-compatible 3.45.0):** capability generations and catalogue activation (`DCM_ASSISTANT_GROUP_DEPLOYMENT`), the frozen baseline and cohort, the authorization snapshot/lock hook, per-member revision snapshots, reauthorization, provider-continuation, upload and Claude-CLI revocation added for this feature, and the tests covering only these. Migrations reverse the activation and baseline tables; the saved-grant field converts to the enabled-group set. Pre-feature assistant behavior, private-chat access checks and billing changes of 3.43.0 are untouched.
- **Required tests:** no saved choice = all read groups; saved set narrows offer, invocation and selectors; unavailable members stay denied in an enabled group; a member added to an enabled group is allowed; revoked-then-restored availability allowed again; unknown group IDs ignored; owner-only, archived and shared-chat refusal; stale-revision conflict; unregistered app unchanged; migration forward from 3.44.0 state and from pre-feature state.

## B. Implementation map

- Target: DCM `C:/Users/Micha Bigler/Documents/webapps/django-core-micha` (main, published 3.44.0 at `5aea9dd`). Spec: `C:/Users/Micha Bigler/Documents/webapps/hram/work-orders/HRAM-CHT-12.md` (Part A rule, Removals, Required tests).
- Grant core: `src/django_core_micha/assistant/group_grants.py`.
- Removal candidates: `catalogue_activation.py`, `authorization_snapshot.py`, `provider_context.py`, feature hunks in `drivers/base.py`, `drivers/claude_cli.py`, `turns.py`, `hooks.py`, `views.py`; migrations `0010`-`0012` (stay; add a reversing migration); tests of `5aea9dd` covering only removed behavior; `docs/design/assistant-chat.md`, `CHANGELOG.md`, `pyproject.toml` version.
- Kept seams: `group_grant_views.py`, `urls.py`, `models.py` grant fields, `hooks.py` `GROUP_CATALOGUE` hook, `access.py` private-chat access.

## Preamble

> You are the dispatched implementer for the assigned repository portion of this work order. The whole committed work order is the complete spec. Part C is for the Orchestrator. You never delegate, review, run `codex` or `codex exec`, write to Git or alter the register or `.claude/codex-status.md`.
>
> Your first tool action is the shell command `codex-hook-canary-ws-gov-60` exactly. The expected result is a denial, `WS-GOV-60 canary: workspace hooks are active`. If the expected denial is absent, report hook coverage as unverified once and continue within the active controls.
>
> Read the nearest `AGENTS.md`, matching engineering skill and app memory for conventions. Work from the named seams and use the absolute target path. Change only source, tests, migrations and the contract documentation named in your assigned portion. Leave dependencies, lockfiles, package releases, staging and review to the Orchestrator. Do not alter the WO, prototype, acceptance protocol or memory. Preserve foreign changes.
>
> Write and run only your own new tests once. If only the test run is unavailable in the sandbox, give `TESTS NOT RUN: <reason>` and leave the authoritative run to the Orchestrator.
>
> Report `PLAN: <steps>`, then `PROGRESS: [<n>/<total>] <action>` before material steps with no more than about two minutes of silence. Leave an unstaged diff. End with exactly one `RESULT: DONE` or `RESULT: BLOCKED <reason>`.

## C. Orchestrator only

Authoritative Part C and Gate record: `hram/work-orders/HRAM-CHT-12.md`.

## Completion

2026-11-27. Implementer `codex/gpt-6.1-terra` (medium) wrote the core and migration 0013; two dispatches ended `RESULT: BLOCKED` on unfinished test adaptation, which the Orchestrator completed (legacy tests adapted or deleted, catalogue-validation test, cancel-during-start hunk of `turns.py` kept as a general fix, `build_turn_context` always authorizes against the live chat). Backend gate: `tests.py --repo django-core-micha --scope backend --wo DCM-CHAT-24` 2191 listed, all green; the backend scope needs the repo test venv on PATH, because the global Python313 `django_core_micha` 3.44.0 shadows the checkout (`ImportPathMismatchError`).

Review: 2 rounds, envelope/regression/duplication/tests/sec_review, 13 raised, 5 accepted and fixed (archive PATCH row lock restored, unknown group IDs dropped on save, stale-actor test, boundary test, reversible migration fails closed). Rejected as outside the approved rule or as the intended removals: redisclosure of earlier turns and provider-boundary `authorize` checks (Envelope: earlier turns stay as they are, revocation machinery removed), handler/actor identity pinning at invocation (removed snapshot machinery), conversion of stale write grants (nothing widens beyond pre-feature behaviour). Carried: no replacement for the archive-versus-save concurrency test.

Pre-existing red: `tests/test_test_management_disposal_proof.py` (Docker proof, a leftover volume from an earlier proof run; environment state, not code): open, needs a Docker volume removal. Part B: accurate.
