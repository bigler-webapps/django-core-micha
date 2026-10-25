# DCM-CHAT-17 — Claude CLI tool bridge: read the whole tool answer

## Part A — Envelope (everyone)

**Tier:** 3 — a change inside shared core (`src/django_core_micha/assistant`).
**Repository:** `bigler-webapps/django-core-micha`, `main`.
**Consumer that found it:** maana (assistant `lookup_interview`); fixes every app whose tools return large answers.

### Goal and expected outcome

On the `claude_cli` path every tool call goes through `cli_bridge.py`, which forwards the call to the driver's
local socket and reads the answer with one `conn.recv(1_000_001)` (l.39). A large answer (Maana's whole-interview
lookup, about 100-200 KB of JSON) arrives in several chunks, the bridge parses only the first, the JSON is incomplete,
and the call ends as `tool_unavailable` (l.42). The app has already computed, recorded and budgeted the result; the
model never sees it. Observed on Maana staging 2026-10-25: `lookup_interview` for one interview failed three times in
a row while small lookups worked. Expected outcome: the bridge returns every answer the driver writes, whatever its
size up to an explicit bound, and fails clearly above it.

### Settled decisions (operator, 2026-10-25, Maana Expertenchat)

1. The bridge reads until the answer's terminating newline (the driver writes one JSON line per answer,
   `drivers/claude_cli.py` l.109 and the `answer` write) or EOF, not one `recv`.
2. An explicit upper bound stays (today's 1 MB cap made explicit); an answer above it returns a distinct error code
   (for example `tool_answer_too_large`), not `tool_unavailable`, so the model and the logs can tell the two apart.
3. The socket timeout per call stays as today (30 s).

### Scope and acceptance

`cli_bridge.py` (`_call`) and its tests; CHANGELOG and a patch version. Accepted when a tool answer of several hundred
KB reaches the model intact on the `claude_cli` path and an answer above the bound yields the distinct code.

### Non-goals and do-not-touch

The driver's tool loop, tool limits, the other drivers, the MCP framing towards the CLI.

### Required tests to write

A fake driver endpoint that writes a single answer line larger than one socket read (well above 64 KB, sent in
several writes with a pause between them): the bridge returns the complete parsed answer. An answer above the bound:
the distinct error code. A closed connection without a newline: `tool_unavailable` as today.

## Part B — Implementation map (implementer)

Placeholder — dispatch-blocking. To be filled by the Orchestrator.

## Part C — Orchestrator only

STOP — this part is not an instruction to the implementer.

Gate #1 approved by the operator on 2026-10-25 in the Maana Expertenchat (session
local_cbfc9d51-5217-42a4-99ba-27a315c854ce). Tier 3: all reviewer lenses; no `ui_review` (no frontend); no
`sec_review` (no auth surface; the bridge keeps its nonce and local endpoint unchanged). Publish a patch release and
report the version; Maana bumps its pin.
