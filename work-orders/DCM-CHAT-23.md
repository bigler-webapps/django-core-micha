# DCM-CHAT-23 — Chat permission groups: shared backend

## A. Envelope

This repository-local companion records the django-core-micha portion of HRAM-CHT-10, the canonical feature work order and acceptance target. The operator requested these companion entries on 2026-11-25. The scope, exclusions and approval below remain those of HRAM-CHT-10; this entry authorizes no additional feature or review round.

**Tier 3.** Changes both shared cores and the server-side authorization of app tools and data. The feature has one work order across django-core-micha (DCM), ui-core-micha (UCM) and HRAM. HRAM hosts the work order because it is the first consumer and the executable acceptance target.

### Goal and outcome

An HRAM user can open a small gear in the chat header and narrow what that chat may read by switching a few meaningful groups. The same shared UCM surface renders any opted-in app catalogue, including write groups with confirmation requirements, without per-app frontend controls or mappings.

### Product contract

- **App catalogue.** The app backend supplies stable group and capability IDs, localized user-facing labels, read/write kind, ordering, the functions in each group and the write confirmation policy per action. Group by what the user recognizes, such as planning foundations, options and results, or rounds and submissions. Keep the default list short. The disclosure for a group lists its functions without individual switches. Treat catalogue content as plain data. UCM supplies the common strings and presentation.
- **One popup, one save.** Separate read and write sections, with a single switch per group and details collapsed on open. Group changes are staged until one Save. Cancel, close or Escape discards staged changes and returns focus to the gear. Group state survives reload and is isolated to the chat. Loading, failure and stale writes are explicit. A save failure keeps the draft available for retry. Never apply a late read or save result to a different chat.
- **Effective access.** For opted-in apps the server intersects current app, object and field access, the app's admin and user restrictions, the chat's allowed groups and the current model/data-disclosure policy. Apply the same constraints to tool discovery, invocation and app-supplied context. Recheck at execution and before returning results to the provider. Preserve existing per-object and provider checks. The browser can submit group choices only. It cannot grant capabilities, change confirmation requirements or supply an authoritative scope or actor.
- **Availability.** Present only groups and functions the app declares appropriate to disclose to that user. A visible unavailable function explains its state. A group with zero available functions cannot be enabled. Enabling a group containing an unavailable function only permits eligible members. A no-write catalogue renders the read controls and a short empty write state. Apps without an opted-in catalogue retain their existing chat behavior.
- **Lifecycle.** Persist restrictions on the existing private-chat boundary. Cross-user, inaccessible, archived or stale edits are refused. Save atomically against the current catalogue and grant revision. Deleting capabilities takes effect immediately. A newly registered capability in a saved group needs a newly authorized grant. A rename or label translation never grants access. After a restriction narrows, stop any affected active work and prevent automatic provider reuse of context, history, summaries and resumed sessions that can carry the removed data. Restart model context if safe reuse cannot be established. Keep stored transcripts under existing access and retention rules. Previously disclosed data follows the existing provider retention policy.
- **Write metadata.** Support groups containing direct-on-explicit-request and confirmation-required action descriptors. Show the applicable policy in the collapsed group and expanded details, with a mixed policy label when needed. An enabled write group expresses permission to request its actions. Per-action confirmation remains an independent requirement. HRAM exposes zero write groups. This delivery exercises write rendering with synthetic catalogue fixtures.

### Approved lifecycle extension — 2026-11-25

The operator approved this Tier 3 extension for DCM-CHAT-23 on 2026-11-25. Each capability has a durable authorization generation tied to its active lifetime. Retiring and reintroducing the same public ID, bindings and configuration ends the old generation and requires fresh group approval. Record transitions when the authoritative app catalogue is activated, independently of chat reads or saves. A per-user catalogue view, temporary authorization failure, label change or translation change is not retirement. Stale workers and lifecycle-aware rollback must not reactivate a retired generation.

Include the generation in saved grants, the bounded initial HRAM read baseline and live authorization for discovery, tool execution, data selectors and provider context. An unverified or retired generation grants nothing; later additions do not extend the baseline. Preserve group-level UI and transcript visibility. Provider reuse of stored history remains conservative until safe reuse, including changes during a turn, is proven.

Required evidence: persist manually approved and baseline-derived grants, retire and reintroduce the identical capability with no intervening chat access, and prove discovery, invocation and app data stay denied until the group is approved again. Cover translations, temporary per-user unavailability, stale saves/views/workers and affected in-flight provider continuation. Run affected DCM backend and security tests and HRAM catalogue, grant and acceptance evidence through the workspace runners. The approved Envelope change authorizes the required independent review round under the existing IDs. Coordinate the DCM version with DCM-BILL-15, which owns the local 3.43.0 change.

### Approved authorization synchronization — 2026-11-26

The operator approved extending this Tier 3 work to synchronize chat-grant saves with HRAM authorization changes. Add a shared transactional lock or durable authorization-version protocol covering assistant administrator and user category settings, planner, curator and review-authority assignments, relevant course membership and user/profile role changes. All supported mutation paths and the DCM grant-save integration must participate; preserve existing app authorization policy and fail closed where synchronization cannot be established. Prevent both a concurrent revocation being captured as a new grant and a later restoration from silently reactivating a grant invalidated by that change. Preserve temporary catalogue visibility semantics without permitting a retired generation to return.

Add deterministic PostgreSQL concurrency tests for saves racing each relevant authorization mutation, activation versus grant save, and revocation followed by restoration. Prove no authorization widening through discovery, invocation, selectors or provider continuation. Update the DCM and HRAM contracts, run affected backend and security inventories, and repeat the independent review under the existing work orders. The operator also approved retrying the blocked UCM test write using the required absolute target path.

### HRAM first adopter

- Map existing `ToolSpec` registrations and HRAM's data categories into groups such as **Planning foundations**, **Options and results** and **Rounds and submissions**. Cover the existing tool/category inventory for planning, research, teaching and methodology. Only display groups appropriate to the current authorized context. Assign each function to a meaningful group and preserve its underlying category predicates, including tools with multiple category requirements. HRAM-specific catalogue labels and mapping live in the backend.
- Continue to honor HRAM's account-wide `My Data` and admin controls as upper bounds. An existing or new HRAM chat with no per-chat choice keeps its currently eligible legacy read capabilities from the initial catalogue at rollout. A chat cannot override a disabled category. Subsequent additions to a group do not expand an existing grant or this legacy baseline. Preserve the separate hosted-analysis consent and model policy while applying chat restrictions to any app data it receives.
- Use HRAM's existing `ChatWorkspace` mount and DCM assistant URLs with the shared built-in UI. A catalogue-only configuration must be sufficient to show the new control. No HRAM-specific JSX, per-group translations in HRAM frontend, custom permission endpoint in HRAM, or extra frontend setup for a second app.
- Release additive, compatible DCM and UCM versions before updating HRAM's exact pins. Check the version diffs and preserve existing clients' contracts. The completion evidence includes the running HRAM application on both pins.

### Scope limits and risks

- **Preserve** the existing HRAM `My Data` screen and account settings. It controls a different scope from this chat popup. No UI removals are authorized. Existing chat history, model picker, chat management and provider/billing settings keep their current UI and access policy.
- App write execution, action confirmation endpoints, independent changes to app policies, Voice, MCP, other app rollouts and shared-chat grant semantics are outside this envelope. The prototype's maana and Cockpit catalogues demonstrate the UI contract only.
- Main risks: scope fallback accidentally making a forbidden tool callable, catalogue growth silently widening grants, stale saves overwriting newer settings, archive/revocation races, an app-specific UI dependency, and provider replay of withdrawn data.

### Prototype and adopted criteria

Prototype: `work-orders/assets/app-prototype.html@652d15f6`, scene `?scene=chat-grants`, checklist `chat-grants-checklist`. Shows the closed gear, open grouped popup and expanded group in the existing chat. The prototype defines UI/UX parity. This Envelope defines behavior, authorization, data contracts and coverage.

| ID | Required outcome |
| --- | --- |
| CG-V1 | An accessible gear is visible and reachable with the popup closed at 375 and 1280 px. |
| CG-V2 | The open popup presents collapsed groups, one switch per group, the HRAM no-write state and reachable actions without horizontal overflow at both widths. |
| CG-V3 | Expanding Options and results reveals its actual functions with the original single switch and reachable actions at both widths. |
| CG-F1 | A saved change affects only the selected chat, survives reopening and reload, and leaves the second chat's settings intact. |
| CG-F2 | Cancel discards edits. An app-disabled function remains unavailable when its group is enabled. |

Token deltas: use the existing UCM tokens for surface colors, accent, typography, radius, shadows and density in place of the prototype's illustrative colors and system font. Match the visible hierarchy and interaction.

Copy budget for new explanatory/status text (German source prototype, localized in implementation): `Gilt für diesen Chat`, `Keine Schreibaktionen im Chat`, `Keine Leseaktionen im Chat`, `App-Recht fehlt`, `Direkt bei ausdrücklichem Auftrag`, `Mit Bestätigung`, `Bestätigung je nach Aktion`, `Chat-Freigaben gespeichert.`, `Chat-Freigaben konnten nicht geladen werden.`, `Chat-Freigaben konnten nicht gespeichert werden.`, `Die Freigaben wurden geändert. Neu laden.` Function counts and group availability counts are localized state labels.

### Required tests to write

- **DCM:** catalogue schema and duplicate/invalid IDs, opt-in/no-hook compatibility, group save and revision conflicts, private owner and scope authorization, foreign chat and archived chat rejection, server rejection of forged group IDs or action grants, permission intersection including unavailable group members, and removal/addition/translation lifecycle. Prove that tool calls or app context after narrowing cannot recover denied data, including a running turn and a provider continuation. Add affected permission and WebSocket inventory coverage where the surfaces change, and document the shared API contract and error shapes.
- **UCM:** use two unrelated catalogue fixtures with the same component and an empty write section. Assert grouped switch count and translation, effective/disabled state, collapsed details, direct/confirmation/mixed write metadata, keyboard focus and dismissal, save/cancel/retry, and stale responses after switching chats. Check the shared ChatWorkspace without catalogue support.
- **HRAM:** cover category mapping, existing account/admin restrictions, planning workspace and user boundaries, legacy no-choice reads, and category changes between opening the popup and tool execution. Verify app-supplied context and existing hosted-analysis dataset exposure. Update the HRAM API-contract description. In the isolated synthetic fixture `assistant_grouped_grants`, prepare two private chats titled `Planungsoptionen vergleichen` and `Zweiter Planungschat` for the same German-language run-owned actor. Allow planning foundations and options, disable rounds by the chat default in the fixture, and disable one review function at the app level. Exercise the HRAM click path in the acceptance protocol using this fixture. Use deterministic provider stubs for any necessary backend model calls.

## B. Implementation map

Prepared as a repository-local companion to HRAM-CHT-10. Only this repository's portion is in scope.

- Target: django-core-micha, branch main.
- Assistant catalogue, chat grants, authorization, provider drivers, migration, tests and assistant contract documentation.
- Preserve unrelated changes and select only HRAM-CHT-10 hunks for review and landing.
- The canonical acceptance criteria and cross-repository release sequence remain in HRAM-CHT-10.

## Preamble

> Implement only the assigned repository portion of the approved HRAM-CHT-10 envelope. Preserve foreign changes. Do not alter the work order, register, dependencies or Git state. Do not run tests; the orchestrator owns evidence runs. Report progress and leave an unstaged diff.

## C. Orchestrator only

Track this repository's independent review, tests and release evidence here and in HRAM-CHT-10. Do not claim completion until all applicable gates pass.

## Gate record

Gate #1 approved by the operator for HRAM-CHT-10; repository-local companion requested by the operator on 2026-11-25.
Gate #1 approved by the operator 2026-11-25 for the capability lifecycle extension (requirements-chat handover).
Gate #1 approved by the operator 2026-11-26 for authorization synchronization and retrying the blocked UCM test write.

## Completion

On 2026-11-26 the operator approved a single 3.43.0 release containing DCM-CHAT-23, DCM-BILL-15 and DCM-DX-21. The independent review and tests must cover the released candidate; HRAM rollout acceptance remains separate.

The explicit joint-release decision authorizes review round 6. Independent configured Codex reviewers (`gpt-6.1-terra`, duplication `gpt-6-luna`) raised the same new-chat baseline objection in four lenses. All four were rejected: the operator decision recorded below explicitly allows a new chat to use the current eligible baseline after revocation and restoration, while existing chats remain denied. The review envelope payload omitted that decision because it was recorded only in Completion. No code change follows from those findings; the HRAM rollout gate remains open.

Open. Review and release evidence pending. Part B: mapping prepared from HRAM-CHT-10; verify against the final diff.

2026-11-25: Round 1 reviewed by codex/gpt-6.1-terra (envelope, regression, tests, security) and codex/gpt-6-luna (duplication). After correcting the dispatch ownership mapping, a codex/gpt-6.1-terra fix worker implemented inactive-actor and stale-handler checks, shared-chat compatibility, and a partial observed-removal fix; added tests for baselines, grants, live revocation and API drivers. Selected grant regression tests passed 6/6 and provider continuation tests passed 3/3 after correcting the Anthropic stream test double. A parallel test attempt collided on the same PostgreSQL test database and was rerun serially; it is invalid evidence. Removal and reintroduction between observed saves can still revive a grant; private chat history is still conservatively omitted, and strict post-handler/pre-continuation coverage remains unproven. Closing review and release are blocked until resolved. The foreign DCM-BILL-15 work owns the uncommitted 3.43.0 version and changelog entry. Part B: repository map accurate for named seams, current evidence recorded here.

2026-11-25: The full backend scope passed after these fixes: 2,233 tests reported, four skipped, no failures or errors. PyPI reported version 3.42.0; uncommitted version 3.43.0 belongs to DCM-BILL-15. No closing review, commit, push or publish has taken place.

2026-11-25 later: Closing review by codex/gpt-6.1-terra (envelope, regression, tests, security) and codex/gpt-6-luna (duplication) raised one lifecycle blocker and five defects. The codex/gpt-6.1-terra high-effort fix worker addressed dynamic handler comparison and provider authorization test gaps; the lifecycle blocker and safe history remain unresolved. Three selected test files passed serially: 6, 4 and 7 tests. Selection is not scope evidence. These post-closing fixes have not received another review. No publish is authorized while the blocker survives.

After those fixes, the complete backend scope passed: 2,240 tests reported, four skipped, no failures or errors. The passing tests do not resolve the lifecycle blocker or prove safe history reuse. No release, push or commit was made.

Release order confirmed by the operator: DCM-CHAT-23 targets 3.43.0; DCM-BILL-15 is expected to follow at 3.44.0 after the chat release and a fresh version check. The existing uncommitted billing changelog hunk remains owned by DCM-BILL-15 and must not be staged as chat work.

The operator-approved lifecycle Envelope extension is the reason for review round 3. A durable activation and baseline implementation and focused lifecycle tests are present. The selected assistant suite reported 297 tests, one skipped, zero failures; selection is not full scope evidence. HRAM integration and deployment activation remain to be verified against the released core.

Round 3 used codex/gpt-6.1-terra for envelope, regression, tests and security and codex/gpt-6-luna for duplication. Six defects and one nit were raised; the security race, temporarily omitted members, dynamic handlers and coverage remain open. One optional reauthorization wire field was added after review; UCM integration and security verification remain incomplete. The full backend scope after this change reported 2,249 tests, four skipped, zero failures. This green run does not resolve the outstanding review findings. The configured codex/gpt-6.1-terra fix worker reported the app authorization race requires a shared transactional lock or durable authorization-version protocol that includes HRAM's authorization mutation paths.

2026-11-26: The approved authorization-synchronization extension justified round 4. Independent review used codex/gpt-6.1-terra for envelope, regression, tests and security, and codex/gpt-6-luna for duplication: three defects raised, concerning closure-bound model handlers, API reauthorization coverage and provider upload wiring. Fixes are in progress. The parallel full backend run reported 2,261 passed and four skipped, but source changed during the run, so it is not final evidence. HRAM integration remains blocked on an installable candidate and safe provisioning enrollment. Review payload context included an author email address; it should have been excluded from the review transfer. No release is claimed.

Follow-up: The three round-4 findings were addressed, with 8 selected regression tests passing. On the corrected, unchanged source, the complete DCM backend scope passed 2,267 tests with four skipped. A locally built unpublished 3.43.0 candidate then passed 18 HRAM/DCM PostgreSQL save-versus-revocation cases and 6 HRAM cutover cases; selections are not full HRAM scope evidence. Safe legacy-baseline initialization for newly provisioned accounts is still unresolved, so the integrated feature and release remain open. Part B: final verification pending.

Added scope within the approved envelope after round 4: new private-chat creation must bind the fixed legacy baseline at creation under the authorization and catalogue locks. This additional creation path, needed to preserve default reads for newly provisioned accounts without refreshing old chats, is the reason for review round 5. The focused new-chat suite passed 16 tests; complete DCM and HRAM evidence on the final candidate remains pending.

Round 5 used codex/gpt-6.1-terra for envelope, regression, tests and security and codex/gpt-6-luna for duplication. Deduplicated findings concerned automatic new-chat reauthorization after a changed cohort, blocking chat creation when no cohort exists, and closure handler compatibility. The fixes retain immutable cohort generations, allow an empty grant when enrollment is absent, and support bounded JSON captures; 26 selected tests passed afterward. The full DCM scope immediately before these fixes passed 2,283 tests with four skipped; the selected checks cover the subsequent edits. A complete default-read solution for newly provisioned accounts remains open, and the HRAM acceptance has not passed. The local package metadata and database test configuration were omitted from round 5 due to personal-data and credential exposure in round 4; the exposed test credential requires rotation.

Operator decision 2026-11-26: new chats start with the fixed, currently eligible standard read baseline even after HRAM rights are revoked and restored; existing chats keep their prior authorization generation and remain denied until explicitly reauthorized. This settles the round-5 objection to initializing a new chat from the current locked generation; creation with no verified cohort must instead start with empty grants.

After this decision, the affected DCM selection passed 23 tests. The unpublished local candidate passed the HRAM new-account test (3 tests) and the authorization synchronization selection (21 passed, one skipped). HRAM's closing review remains blocked on released shared-core pins and a safe rollout activation. The committed DCM publish workflow has no PostgreSQL service; the local PostgreSQL settings and corresponding workflow changes belong to the separate uncommitted DCM-DX-21 work order. A clean, approved release path must be established before publishing 3.43.0; no foreign DX-21 or billing changes were staged.

2026-11-26 release update: The operator assigned DCM-CHAT-23 version 3.44.0 after DCM-BILL-15 published 3.43.0 and DCM-DX-21 landed PostgreSQL-only test and publish configuration. The HRAM dependency bump and rollout acceptance follow under a separate work order. Round 6 remains the independent review of the scoped assistant changes; its new-chat baseline objections were rejected under the recorded operator decision. A complete backend run on the final unchanged candidate is required before landing. Part B: accurate.

Final backend gate: PostgreSQL `tests.py --repo django-core-micha --scope backend --wo DCM-CHAT-23` passed on one unchanged tree, 2,264 tests, zero failures and three skipped. The round-6 review payload was verified against the complete current assistant and design diff; its four duplicate findings were rejected against the operator's explicit new-chat decision. Release publication and HRAM rollout remain to be verified.
