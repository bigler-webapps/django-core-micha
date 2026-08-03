# WORK ORDER MSG-10 (django-core-micha) — attachment uploads are unconditionally rejected, and `all_read` is vacuously true with no recipients

**EXECUTION DIRECTIVE.** If you are the implementer reading this as your own spec, this section is not
addressed to you — it tells the Orchestrator how to invoke you; you ARE that invocation, do not shell
out to `codex exec`. Orchestrator: implement through `codex exec` in the background, invoked **directly
via Bash** (never the `debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check`
and `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from this
file. Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

Feature-level WO (Expertenchat envelope). Operator report 2026-08-03 from the live messaging surface.
Both defects verified the same day: A from code plus a Python check in the running container, B
reproduced live against a running stack.

## TIER
Tier 2 — shared-core, consumed by every app. **A is a functional outage of a user-facing feature.**
Independent `reviewer` mandatory.

## SCOPE

**A. Attachment uploads fail with HTTP 400, always.**

Operator error, verbatim:

```json
{"errors":[{"field":"client_request_id","code":"invalid",
            "message":"Must match Idempotency-Key."}]}
```

`_idempotency_request_id` (`views.py:118-128`) compares the body value against the parsed header:

```python
header_id = uuid.UUID(header)
if supplied and supplied != header_id:
    raise ValidationError({"client_request_id": "Must match Idempotency-Key."})
```

Its three call sites are **not** equivalent:

| Line | Passes | `client_request_id` type |
|---|---|---|
| `:206` (send message) | `data.validated_data` | `UUID` (serializer-coerced) |
| `:339` (create poll) | `data.validated_data` | `UUID` (serializer-coerced) |
| **`:217` (attachments)** | `{"client_request_id": request.data.get("client_request_id")}` | **`str`** — raw multipart, never validated |

In Python `str != UUID` is always `True` (`UUID.__eq__` returns `NotImplemented` for a non-UUID, so it
falls back to identity). **Confirmed in the running container.** Therefore every attachment upload that
sends both the `Idempotency-Key` header and a body `client_request_id` — which is exactly what
`ui-core-micha`'s `sendAttachments` does — is rejected unconditionally. Attachments are not
intermittently broken; they have never worked on this path.

**Fix: coerce before comparing.** Parse the supplied value to a `UUID` the same way the header is
parsed, and reject with a clear message if it is not a valid UUID. Do this **inside
`_idempotency_request_id`** so all three call sites are covered and no future caller can reintroduce the
divergence — do not fix it at `:217` alone. A caller passing an already-parsed `UUID` must keep working.

Note `:217` also bypasses serializer validation entirely for this field. Consider routing it through the
same input serializer as the other two; if you judge that too invasive for this WO, say so and leave the
coercion in the helper.

**B. `all_read` is `True` when there are no recipients.**

```python
participants = conversation.participants.filter(removed_at__isnull=True).exclude(user=message.sender)
all_read = not participants.exclude(last_read_at__gte=message.created_at).exists()
```

With `participants` empty, `.exclude(...).exists()` is `False`, so `all_read` is `True`. A message nobody
can receive reports "read by everyone".

**Reproduced live, 2026-08-03**, on a group conversation with no other provisioned participants:

```json
{"kind":"group","all_read":true,"read_count":0,"recipient_count":0}
```

The user-visible result is the operator's report: the ticks go to the "read" state **immediately on
send** and never mean anything. This is the same failure class as the vacuous permission tests this
codebase has shipped before — an assertion that is trivially satisfied by an empty set.

**Fix: an empty recipient set is not "all read".** When `recipient_count == 0`, `all_read` must be
`False`. Decide and state whether the correct answer is instead "no receipt at all" for such a
conversation — a message with no recipients arguably has no read state — and if so, say how the response
signals that so ucm can render nothing rather than a misleading first-state tick. Either answer is
defensible; an unstated one is not.

**Do not fix this only in `ui-core-micha` by checking `recipient_count`.** The server currently asserts
something false; a client-side workaround leaves the next consumer to rediscover it.

## NON-GOALS / DO NOT TOUCH
- Do not change `mark_read`, `last_read_at`, `unread_counts`, or the `read_receipt_detail` gating.
- Do not change `read_count` / `recipient_count` semantics from MSG-9 — B only corrects `all_read`.
- Do not reintroduce `delivered_count`.
- No model or migration changes.
- Do not touch the batch endpoint's permission logic.

## RISKS
- **A is the reason attachments do not work at all**; get it out fast, but the coercion must reject a
  malformed value rather than silently swallowing it — a lenient parse that accepts anything would trade
  a visible outage for a silent idempotency hole.
- B changes a value clients render. `ui-core-micha` currently maps `all_read` to the double check; after
  this, a zero-recipient conversation stops showing it. Confirm that is the intended visible outcome
  with the ucm side rather than assuming.
- Why did neither surface in tests? Both are empty/type-edge cases. Whatever tests exist for these paths
  construct a populated conversation and a coerced UUID. **Say so in the completion note** — the gap is
  the lesson, not the two lines of fix.

## REQUIRED TESTS TO WRITE
Narrow and behavioural. Do NOT run the full suite.

1. **A:** an attachment upload with an `Idempotency-Key` header **and** a matching string
   `client_request_id` in the multipart body succeeds. This must **fail against current code** — prove
   it, it is the operator's exact request.
2. **A:** a *mismatched* string body value is still rejected (the guard must not be defeated, only
   fixed), and a malformed non-UUID body value is rejected with a clear error.
3. **A:** the send-message and create-poll paths, which pass a real `UUID`, are unaffected.
4. **B:** `read_status` on a message in a conversation with **zero** non-sender participants returns
   `all_read: False` (or whatever scope B decides), together with `recipient_count: 0`.
5. **B:** the populated cases are unchanged — partial read is `False`, fully read is `True`.

**Non-vacuity:** tests 1 and 4 must fail on current code; run them before the fix and record the
failure output. Test 4 in particular must use a genuinely empty participant set — if the fixture
provisions a participant, the test proves nothing, which is precisely how this defect survived.

## TEST SCOPE FOR THE GATE (orchestrator)
`messaging/` only. Compare before/after via `git stash` and state the measured baseline.

## TARGET REPO
`C:\Users\biglmi\Documents\webapps\django-core-micha`. Branch `develop` if it exists, else `main`.
Publish + version bump (patch on 2.40.0) per the repo's release flow.

## MINI-HANDOVER (pastable)

> Repo: `C:\Users\biglmi\Documents\webapps\django-core-micha` (branch `develop` if it exists, else
> `main`). Work order: `work-orders/MSG-10.md` — read it fully, then follow the `orchestrate-codex`
> skill. This is dcm's own MSG-10; jg-ferien has an unrelated MSG-10.
>
> **Scope A is a live outage — attachment uploads have never worked on the platform path** and fail
> with a 400 on every attempt, because `views.py:217` passes an unvalidated `str` where the other two
> call sites pass a serializer-coerced `UUID`, and `str != UUID` is always true in Python. Fix inside
> `_idempotency_request_id`, not at the call site.
>
> Scope B: `all_read` is vacuously `True` when the recipient set is empty — reproduced live on a group
> with `recipient_count: 0`. Fix on the server; do not work around it in ucm.
>
> Both are edge cases no existing test covers. Tests 1 and 4 must be shown failing before the fix.
> **jg-ferien's promotion PR #93 is open and green but should not merge until scope A ships** — see
> that PR's discussion.

## PROGRESS CONTRACT
Emit `PLAN: <steps>` up front, then a single-line `PROGRESS: [<n>/<total>] <action>` before every
relevant action and `PROGRESS: [<n>/<total>] done` on completion, spaced so no gap exceeds ~2 min,
stdout unbuffered, and exactly one final `RESULT: DONE|BLOCKED <reason>`.
