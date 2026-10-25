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

**Target working directory:** `C:/Users/Micha Bigler/Documents/webapps/django-core-micha` (git root, `main`).
Use absolute paths. The checkout is shared: `src/django_core_micha/scripts/sync_secrets.py` and
`tests/test_sync_secrets.py` carry another session's uncommitted edits — do not open, format or revert them.

### Verified facts (Orchestrator, 2026-10-25)

- `src/django_core_micha/assistant/cli_bridge.py:26-42` `_call`: connects (unix or tcp, `timeout=30`), sends one JSON
  request line, then a single `conn.recv(1_000_001)` and `json.loads` of it; any exception → `tool_unavailable`.
- The driver side (`src/django_core_micha/assistant/drivers/claude_cli.py`, `handle()` inside `stream`, around l.89-111)
  writes exactly one JSON answer line (`json.dumps(answer) + "\n"`), drains and closes the connection.
- `cli_bridge.py` is a stdlib-only script run as its own process (`sys.executable cli_bridge.py`); keep it stdlib-only
  and free of Django imports.
- The existing bridge test (`src/django_core_micha/assistant/tests/test_tools_for_chat.py:225-246`) patches `_call`;
  it must stay green unchanged.

### Decisions (implement exactly)

1. `_call` reads in a loop until the received bytes contain `b"\n"` or the peer closes (EOF), then parses the bytes up
   to the first newline (or all bytes at EOF). Parsing failure (for example a truncated answer at EOF) stays
   `tool_unavailable`.
2. A module constant `MAX_ANSWER_BYTES = 1_000_000` (today's cap made explicit). When more than that many bytes arrive
   before the newline, stop reading and return `{"status": "error", "text": "tool_answer_too_large"}`.
3. The 30 s socket timeout and everything else in the bridge stay unchanged; a timeout stays `tool_unavailable`.

### Files

- `src/django_core_micha/assistant/cli_bridge.py` (`_call` only, plus the constant).
- New `src/django_core_micha/assistant/tests/test_cli_bridge_answer.py`: a real local TCP server
  (`socket` + a thread, `127.0.0.1`, port 0) as the fake driver endpoint, the bridge pointed at it through a
  `tmp_path` config file and `DCM_ASSISTANT_BRIDGE_CONFIG` (monkeypatch the environment). Cases from Part A:
  an answer line of several hundred KB written in several `sendall` chunks with short pauses → the complete parsed
  answer; an answer above `MAX_ANSWER_BYTES` → `tool_answer_too_large`; a partial JSON followed by close without a
  newline → `tool_unavailable`.

Run only the tests you wrote, from the target working directory:
`python -m pytest src/django_core_micha/assistant/tests/test_cli_bridge_answer.py -q -p no:cacheprovider`

Do not edit `CHANGELOG.md`, the version, docs or `WORK_ORDERS.md`. Files are UTF-8: open them with explicit
`encoding="utf-8"`.

### Preamble

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the
> app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI
> unless the spec says so; do not update `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register
> row and the review verdicts are the orchestrator's alone.** **Your tools are for editing source
> and test files and for running the tests you wrote — nothing else.** Do NOT install dependencies,
> touch a lockfile, run a package manager, or tidy up stray files; if something in the repo state
> blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT
> `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review).
> The orchestrator re-runs the authoritative set + does the independent review after you finish —
> those are the gate; your own run does not count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.

## Part C — Orchestrator only

> **If you are the implementer reading this work order as your own specification: STOP at this line.
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.** You ARE the implementation
> instance described below; do NOT launch or dispatch another implementer.

Execution: `codex/gpt-6-luna` CLI, reasoning high, WO on stdin, `-C` the target directory. Launch evidence: (filled
after dispatch).

Gate #1 approved by the operator on 2026-10-25 in the Maana Expertenchat (session
local_cbfc9d51-5217-42a4-99ba-27a315c854ce). Tier 3: all reviewer lenses; no `ui_review` (no frontend); no
`sec_review` (no auth surface; the bridge keeps its nonce and local endpoint unchanged). Publish a patch release and
report the version; Maana bumps its pin.
