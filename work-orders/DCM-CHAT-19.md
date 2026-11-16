# DCM-CHAT-19 — Provider-side code execution for Assistant analyses

## Part A — Envelope

**Repository:** `django-core-micha`. **Tier:** 3, shared-core change covering off-host execution, dataset transfer and cost accounting. **First consumer:** `HRAM-AI-6`. This order delivers and releases the shared capability. The consumer's pin and activation belong to HRAM.

### Expected outcome

An Assistant consumer can enable a bounded analysis in a private chat on a supported OpenAI or Anthropic API catalogue entry. The provider executes the analysis over files supplied by the app for that analysis. The consumer receives normalized execution records and results under the existing chat access boundary. Execution charges count towards the sender's existing period limit. Existing consumers gain the capability only by opting in.

### Availability and data boundary

- The server enables execution only when the consumer's policy permits it for the app, current chat and requester, and the selected provider and model support it. The absence of a policy or capability decision disables execution. Shared chats are outside the first release. The Claude Code CLI reports the capability unavailable and its shell remains disabled.
- The consuming app owns the role, dataset and data classification decisions. DCM accepts bounded, typed dataset content from that trusted app path. A prompt, client payload, model output, arbitrary URL, path or provider file ID cannot authorize transfer. Validate ownership and current access before upload and recheck before publishing results under the existing chat access rules.
- Anthropic execution requires explicit consumer approval of the provider's retention policy for the app **and each supplied data class**. Unknown or disallowed classes cannot be uploaded to Anthropic. Document the provider's retention separately from the app's cleanup. Anthropic reports container data retention of up to 30 days and no Zero Data Retention eligibility for this tool. Files retained through the provider's Files API must be explicitly cleaned up where supported. See [Anthropic Code Execution](https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool#data-retention).
- Each analysis starts in a fresh provider container. The same container may serve provider continuation calls inside that one analysis. A later analysis, including a retry after a terminal failure, starts fresh. Follow-up analyses may receive freshly authorized datasets associated with earlier answers in the same chat. The app provides those datasets anew and DCM never promotes an old container, file reference or prior execution state into a new analysis.
- Use the provider's hosted execution tool only. Do not make app credentials or direct app access available to the container. Do not enable web search, web fetch, programmatic app-tool access or other onward transfer as part of execution. Preserve the ordinary app-controlled Assistant tool boundary. Confirm the supported provider execution configuration has no general network egress before advertising it. See [OpenAI Code Interpreter](https://developers.openai.com/api/docs/guides/tools-code-interpreter) and [Anthropic networking and security](https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool#networking-and-security).

### Turn results and lifecycle

- Return the actual executed code, normalized status, bounded output text and structured data through the existing Assistant turn contract or its compatible extension. Return bounded generated data files using allowlisted data formats such as CSV and JSON. The consumer can validate table and chart data before rendering. Code and output are inert data. Do not render markup, execute returned content or expose raw provider IDs or provider download URLs to clients.
- Accept generated files only from the current analysis. Retrieve them before expiry, enforce count, size and content constraints, and treat missing, unsafe or oversized outputs explicitly. Results intended to survive the turn must remain available independently of the provider container and under the existing private chat access rules. Use the scoped turn contract to report cancellation, timeouts, provider failures and partial output. Expired output never falls back to another container.
- Apply bounded input and output file counts and sizes, total data volume, execution time and provider continuation limits. Distinguish denial before provider contact from a failure after a possible billable start. Clean up temporary provider files and containers where supported on completion, cancellation and failure. Cleanup is bounded and observable without logging dataset content, executed code or credentials. A provider-controlled expiration is not treated as proof of immediate deletion.

### Usage and cost

- Add hosted execution usage to the existing Assistant cost accounting and period total, with attribution to the initiating sender. Account for provider-specific billing units and avoid counting the same container charge twice on continuations, streaming replay or finalization. Use reported usage when available, with a documented conservative estimate when the provider can have charged but final usage is missing. Preserve the distinction between reported and estimated cost. A rejected operation that never reached a billable boundary contributes no execution charge.
- Enforce the existing period-limit entry check before any provider execution resource is opened and extend the existing cost guard to reserve the bounded execution allowance where applicable. Do not start execution on an unpriced configuration. Keep the existing period limit semantics: a turn already running may finish above the threshold, at which point further turns are blocked by the existing check. Validate rates and billing units against the supported tool version and account configuration. See [OpenAI tool pricing](https://platform.openai.com/pricing/) and [Anthropic usage and pricing](https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool#usage-and-pricing).

### Non-goals and risks

No HRAM app changes, UI, local sandbox, new network tool, generic file upload or sharing feature. Keep current Assistant consumers and ordinary turns compatible. Treat provider retention, changing billing units, access revocation between turns and provider file-reference leakage as explicit risks of this integration. The feature must fail closed for unknown support or authorization instead of falling back to a different provider or local shell.

### Required tests to write

1. Opt-in matrix for app/chat authorization, private chat scope, model support and CLI, including Anthropic per-app/per-class approval. Assert denial before any provider resource creation or upload. Ordinary chat turns retain their existing request and output paths.
2. Mock both provider protocols. Exercise dataset upload, fresh container per analysis, continuation within one analysis and a same-chat follow-up that receives reauthorized files only. Reject foreign, prior-analysis or revoked inputs and verify no result publication after loss of chat access.
3. Verify actual executed-code capture, normalized text and structured result, bounded CSV/JSON handling, rejection of unrelated or invalid artifacts, and stable private result retrieval after provider expiry. Cover malformed events and stream replay.
4. Cover cancellation, timeout, continuation limit, partial failure, transient provider errors and cleanup failure. Verify ambiguous starts are not retried blindly and logs omit dataset contents and code.
5. Verify provider-specific execution cost, continuation deduplication, sender attribution, usage hooks and period totals. Cover denied reservations, failed and cancelled operations with missing final usage, exhausted period limits and ordinary token-only regression cases.

## Part B — Implementation map

Placeholder. The Orchestrator completes the context package, absolute target paths, progress contract and implementer preamble after Gate #1. No dispatch with this placeholder.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

At mapping, check `assistant/drivers/{openai_api,anthropic_api,claude_cli,base}.py`, the catalogue and turn-access hooks, `assistant/limit.py`, turn usage and persistence, and `docs/design/assistant-chat.md`. Follow the existing cost and access contracts before deciding on extensions. Record the supported provider/model and cost evidence. Use synthetic inputs for provider checks. Plan Tier 3 independent and security reviews. Release the DCM consumer contract before HRAM pins it for AI-6. Release and app rollout follow their own repository procedures.

## Gate record

On 2026-11-17 the operator selected explicit Anthropic retention approval per app and data class, and a fresh container for each analysis. Gate #1 for code implementation is pending.
