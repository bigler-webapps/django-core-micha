# DCM-CHAT-21 — Resolve stable AI model families per source

## Part A — Envelope

### Goal and outcome

Give the owner one version-independent model catalogue for [BCO-UI-10](../../bigler-consult/work-orders/BCO-UI-10.md). The Backoffice can offer different providers, sources and model families per app and save one default source/family pair. Chat and other managed calls use the model that DCM resolves for that pair. The same selection remains valid when an eligible provider model version is changed centrally, without a consumer redeploy.

**Tier 3.** Shared-core contract, provider dispatch and code-execution eligibility. Builds on the managed policy from `DCM-CHAT-20`, shared receipt routing from `DCM-RCPT-3`, and provider-side execution from `DCM-CHAT-19`.

### Scope and rules

- The managed catalogue exposes exactly these family names: **Astra**, **Sol**, **Terra**, **Luna** for OpenAI, and **Fable**, **Opus**, **Sonnet**, **Haiku** for Claude. Claude families can be offered by an Anthropic API account or the Claude subscription where supported. Display labels contain only the family name, with source identity shown separately where needed. An app sees only the eligible subset. A family may be unavailable for a given source or workload.
- Keep source IDs distinct even when two API accounts use the same provider or when an Anthropic account and the subscription offer the same family. The owner controls the offered providers and sources, the offered families within each source, and one eligible default pair for each app/environment. Policies, explicit selections and defaults use stable source/family identities. A family label alone does not identify a billable source. The existing Backoffice permission and app/environment isolation apply.
- DCM is the central authority for resolving the chosen family and source to an actual provider model ID and determining that pair's capabilities. Keep concrete IDs behind this contract. Apps may impose stricter workload, readiness, cost and data-processing constraints. Managed apps do not maintain their own concrete version pins, replacement lists or model-specific allowlists for provider execution. Centrally changing the version does not require a new consumer package pin, deployment or change to saved selections once the family contract is adopted.
- Resolve at the provider boundary for chats, one-shot completion, shared extraction and opted-in direct workloads. Queued work checks the current applied selection before dispatch. Work already running keeps the model and source it started with. Missing, ambiguous, unavailable or incompatible mappings make the requested capability unavailable. They do not allow another source, family or legacy credential to be selected implicitly. Retain the app's last valid applied state during a delivery outage.
- `HRAM-AI-6` uses the shared capability decision for provider-side code execution. Verify eligibility against the actual resolved model and driver, including the capabilities of the selected API source. An eligible family can remain chat-capable when it cannot use code execution. Ineligible execution must be rejected before an analysis dataset is sent. Preserve the existing role, consent, scope and data limits. No local shell or provider fallback is added.
- Preserve existing unmanaged clients and stored chats. On adoption, reconcile known legacy source/model pairs with stable families using the authoritative mapping. Keep selections and defaults for recognized pairs, preserve historical provider model IDs and usage attribution, and surface unresolved legacy choices through the existing unavailable or invalid-selection behavior. Maintain field and rollout compatibility with existing clients until they adopt the new contract. UCM receives the authoritative family label and its existing source identity. A separate UCM change is needed only if the released picker cannot consume the shared contract with the required behavior.

### Release and consumer boundary

Release the family-aware DCM contract before `BCO-UI-10` or `HRAM-AI-6` pins it. Document the public catalogue, policy transition and capability contract. Bigler Consult owns the Backoffice UI and central policy authoring. The parent WO owns consumer integration, initial per-app version pins and the end-to-end runtime switch on staging. HRAM consumes the released DCM capability and the central catalogue. Provider account provisioning, production promotion and any narrowly required UCM follow-up are outside this DCM order.

### Required tests to write

- Catalogue tests cover the exact eight family labels, independent accounts with the same family, separate Claude API and subscription sources, eligibility by source and workload, and a valid versus disallowed default. Reject unknown families and cross-provider family mappings.
- Persist a policy and explicit chat selection. Change a family's eligible concrete model centrally and verify the next request uses the new model through the same source with the same saved selection and no app restart. Preserve an already-running request. Cover queued work and a stale update arriving after a newer revision.
- Exercise chat, completion and a direct workload through the shared resolution path. Verify missing mappings, unsupported features, unavailable configured sources and no implicit fallback or unintended provider calls. Retain last valid policy through disconnection and restart.
- Test current and legacy stored selections and catalogue clients, including exact source attribution and removal or unavailability for unresolved legacy choices. Preserve accounting against the concrete model actually used.
- Verify provider-side execution for an eligible resolved pair and denial before dataset upload for an ineligible pair. The same family can be chat-only on another source. Preserve HRAM's existing authorization and consent boundaries. Use fake provider transports and synthetic data.

## Part B — Implementation map

The Orchestrator fills the target paths, shared contract map and configured implementer preamble after approval. This order is not dispatchable while this placeholder is present.

## Part C — Orchestrator only

> Implementers stop at this line. The review, release and consumer coordination below belong to the Orchestrator.

Verify the existing policy payload, catalogue selection, receipt dispatch and execution contracts before mapping a compatible transition. Confirm whether UCM's published picker handles family-only labels and opaque source/family identities. Record the resulting DCM release for BCO-UI-10 and HRAM-AI-6. Provider capability evidence must refer to the actual resolved model and source.

## Gate record

The operator requested this follow-up WO on 2026-11-17. Gate #1 for implementation is pending.
