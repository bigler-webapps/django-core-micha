# DCM-CHAT-21 — Resolve stable AI model families per source

## Part A — Envelope

### Goal and outcome

Give the owner one version-independent model catalogue for [BCO-UI-10](../../bigler-consult/work-orders/BCO-UI-10.md). The owner configures which provider/family choices each app offers, which source serves each choice, and the default. End users select a provider and family. DCM resolves the private provisioning route and model version centrally and attributes consumption to the initiating user regardless of the source. The saved choice remains valid when an eligible source or model version is changed centrally without a consumer redeploy.

**Tier 3.** Shared-core contract, provider dispatch and code-execution eligibility. Builds on the managed policy from `DCM-CHAT-20`, shared receipt routing from `DCM-RCPT-3`, and provider-side execution from `DCM-CHAT-19`.

### Scope and rules

- The managed end-user catalogue shows exactly these family names: **Astra**, **Sol**, **Terra**, **Luna** for OpenAI, and **Fable**, **Opus**, **Sonnet**, **Haiku** for Claude. Offer one entry per enabled provider/family. Anthropic API accounts and the Claude subscription can supply the same Claude family. Display the provider and family to the end user. A family may be unavailable for a given source or workload.
- Keep source IDs distinct even when the same family exists on multiple sources. The BCO owner controls the offered providers, the offered families and the eligible source assigned to each offered provider/family in each app/environment. The owner selects an eligible default provider/family. Saved end-user selections use stable provider/family identities or opaque IDs independent of the route. Source identity stays in the owner Backoffice and trusted server-to-server policy, routing and accounting records. Managed user-facing catalogues, responses, WebSocket events, errors and selectors never expose source IDs or labels, account information, subscription indicators, the provider's billing mode or the concrete version. The existing Backoffice permission and app/environment isolation apply.
- DCM is the central authority for resolving the chosen family and source to an actual provider model ID and determining that pair's capabilities. Keep concrete IDs behind this contract. Apps may impose stricter workload, readiness, cost and data-processing constraints. Managed apps do not maintain their own concrete version pins, replacement lists or model-specific allowlists for provider execution. Centrally changing the version does not require a new consumer package pin, deployment or change to saved selections once the family contract is adopted.
- Resolve at the provider boundary for chats, one-shot completion, shared extraction and opted-in direct workloads. Queued work checks the current applied selection before dispatch. Work already running keeps the model and source it started with. Missing, ambiguous, unavailable or incompatible mappings make the requested capability unavailable. They do not allow another source, family or legacy credential to be selected implicitly. Retain the app's last valid applied state during a delivery outage.
- Record the actual reported token usage against the initiating user through the app's existing per-user metering and token ledger, including for API accounts and subscription routes. Normalize reported input, output and cache categories consistently. Apply the app's existing user tariff and exemptions independently of the upstream payment route. The same normalized usage under the same tariff yields the same internal debit, even when the provider reports no monetary spend for the subscription. Record provider USD charges and source attribution separately for Owner reporting and reconciliation. Preserve errors and refunds under the app's existing charging rules. Make settlement idempotent, and keep missing usage unresolved for reconciliation instead of treating it as zero or an estimate. Keep app-initiated work without a user separate from user charges.
- `HRAM-AI-6` uses the shared capability decision for provider-side code execution. Verify eligibility against the actual resolved model and driver, including the capabilities of the selected API source. An eligible family can remain chat-capable when it cannot use code execution. Ineligible execution must be rejected before an analysis dataset is sent. Preserve the existing role, consent, scope and data limits. No local shell or provider fallback is added.
- Preserve existing unmanaged clients and stored chats. On adoption, reconcile known legacy source/model pairs with stable families using the authoritative mapping. Keep selections and defaults for recognized pairs, preserve historical provider model IDs and usage attribution on the server, and surface unresolved legacy choices through the existing unavailable or invalid-selection behavior. Maintain compatibility for unmanaged clients during rollout. Managed users receive only the public catalogue contract. UCM consumes the provider and family from this contract. The managed picker must group the families by provider and omit the subscription or account cues rendered by the previous UCM release.

### Release and consumer boundary

Release the family-aware DCM contract before `BCO-UI-10` or `HRAM-AI-6` pins it. Document the public catalogue, policy transition and capability contract. Bigler Consult owns the Backoffice UI and central policy authoring. The parent WO owns consumer integration, initial per-app version pins and the end-to-end runtime switch on staging. HRAM consumes the released DCM capability and the central catalogue. Provider account provisioning, production promotion and any narrowly required UCM follow-up are outside this DCM order.

### Required tests to write

- Catalogue tests cover the eight family labels, one public entry when several sources can serve the same family, separate owner-visible Claude API and subscription sources, eligibility, and a valid versus disallowed default. Assert that source, account and billing metadata stay out of managed user responses and events. Reject unknown families and cross-provider family mappings.
- Persist a policy and explicit chat selection. Change the selected provider/family's eligible source or concrete model centrally and verify the next request uses the new route with the same saved selection and no app restart. Preserve an already-running request. Cover queued work and a stale update arriving after a newer revision.
- Exercise chat, completion and a direct workload through the shared resolution path. Verify missing mappings, unsupported features, unavailable configured sources and no implicit fallback or unintended provider calls. Retain last valid policy through disconnection and restart.
- Test current and legacy stored selections and catalogue clients, including exact source attribution and removal or unavailability for unresolved legacy choices. Preserve accounting against the concrete model actually used.
- With identical reported usage and the same app tariff, dispatch over an API account and a Claude subscription. Assert identical internal token debits against the correct initiating user, one debit per chargeable operation, and independent upstream cost attribution. Cover app-specific billing adapters, shared chat senders, missing usage, late usage, retries and an existing refund path.
- Verify provider-side execution for an eligible resolved pair and denial before dataset upload for an ineligible pair. The same family can be chat-only on another source. Preserve HRAM's existing authorization and consent boundaries. Use fake provider transports and synthetic data.

## Part B — Implementation map

The Orchestrator fills the target paths, shared contract map and configured implementer preamble after approval. This order is not dispatchable while this placeholder is present.

## Part C — Orchestrator only

> Implementers stop at this line. The review, release and consumer coordination below belong to the Orchestrator.

Verify the existing policy payload, catalogue selection, receipt dispatch, user charging and execution contracts before mapping a compatible transition. Coordinate the managed provider/family-only picker with UCM. Record the resulting DCM release for BCO-UI-10 and HRAM-AI-6. Provider capability evidence must refer to the actual resolved model and source.

## Gate record

The operator requested this follow-up WO on 2026-11-17. Gate #1 for implementation is pending.
