# WORK ORDER MSG-8 (django-core-micha) — every messaging WebSocket push fails to serialize, silently

**EXECUTION DIRECTIVE.** If you are the implementer reading this as your own spec, this section is not
addressed to you — it tells the Orchestrator how to invoke you; you ARE that invocation, do not shell
out to `codex exec`. Orchestrator: implement through `codex exec` in the background, invoked **directly
via Bash** (never the `debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check`
and `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from this
file. Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

Feature-level WO (Expertenchat envelope). This is the root cause of jg-ferien `work-orders/MSG-9.md`
finding 1 ("live WS push arrives ~10 minutes late"). **Diagnosed 2026-08-03 with a two-user test on a
running local stack — the evidence is below and must not be re-derived.**

## TIER
Tier 2, and the highest-severity item in the MSG-9 family. Shared-core: this breaks realtime messaging
for **every app on the platform**, not just jg-ferien. Independent `reviewer` mandatory.

## THE DEFECT — measured, not inferred

`push_to_users` (`notifications/delivery.py:38-61`) hands the payload straight to the channel layer:

```python
async_to_sync(channel_layer.group_send)(
    f"notifications_user_{user.id}",
    {"type": "message", "payload": payload},
)
```

The configured backend is `channels_redis.pubsub.RedisPubSubChannelLayer`
(`settings/settings_base.py:186-193`), which cannot serialize a `datetime`. And the messaging payloads
carry raw `datetime` objects:

- `serialize_message` — `edited_at`, `deleted_at` (`serializers.py:91`), `created_at` (`:92`),
  `last_reply_at` (`:94`)
- `serialize_conversation_core` — `last_message_at`, `created_at` (`:127-128`), reached via
  `_conversation_upsert_payload` (`services.py:397-400`)

In the REST path DRF's JSON renderer converts these. **On the WS path nothing does.** `group_send`
raises, and `delivery.py:60-61` catches it and logs a **warning**:

```
WARNING  WebSocket notification failed for user 3: can not serialize 'datetime.datetime' object
```

**Measured on a local stack, 2026-08-03: 16 push attempts, 16 failures. A 100 % failure rate.**

Two-user reproduction: user 2 posted to a DM via an independent HTTP session while user 3 had that
exact conversation open in a browser. `HTTP 201`, the row is in the database — and after **five
minutes without a reload the message had still not appeared.** Two failure lines appear in the backend
log per send (the `message` frame at `services.py:182` and the `conversation_upsert` frame at `:183`).

**No messaging realtime frame carrying a serialized message or conversation has ever reached a client.**
What the jg operator observed as "it arrives after ~10 minutes" was not a late push — it was ucm's
reconnect handler firing `refresh()` and pulling the backlog over REST.

**Corrects an earlier hypothesis in MSG-9:** the missing WS heartbeat in `useRealtimeCore` was proposed
as the cause. It is not. The heartbeat gap is real and worth its own consideration, but the push never
leaves the server, so no transport-liveness fix would have changed anything. Do not spend time there.

Frames whose payloads were built with explicit `.isoformat()` — `read_state` (`services.py:293`),
`delivered` (`:302`), `thread_read_state` (`:312`) — do **not** fail. That asymmetry is the tell: the
authors of those three remembered, `serialize_message` did not.

## GOAL
Every realtime frame the platform emits is serializable by the channel layer, and a frame that cannot
be delivered is impossible to miss.

## SCOPE

**A. Make the payload JSON-safe at the single chokepoint.** Convert the payload to JSON-safe primitives
in `push_to_users` before `group_send` — it is the one function every WS push in the platform passes
through, so fixing it here covers messaging, notifications, and anything added later.

**Do this centrally; do not patch `datetime` fields one serializer at a time.** Per-field patching is
what produced the current asymmetry (three payloads remembered `.isoformat()`, two did not) and it
guarantees a sixth payload will get it wrong later.

Use Django's existing JSON encoder (`DjangoJSONEncoder`) or an equivalent already in the codebase
rather than a hand-rolled walker. Datetimes must land as ISO-8601 strings — the same shape the REST
path already produces, so **clients need no change** (ucm parses with `new Date(...)`, which accepts
ISO-8601). Verify that equivalence explicitly; a divergence between the REST and WS shapes is the exact
class of bug this WO family is about.

**B. A failed push must not be a swallowed warning.** Today a 100 % delivery failure produced nothing
but `logger.warning` lines that nobody read for the entire MSG-5 series. Keep the exception isolated —
a delivery failure must still never break the durable write — but raise its visibility: `logger.error`
plus whatever error-reporting the platform already wires up. Include the frame `type` and the payload
keys in the message; the current text names neither, which is why the log line was useless for
diagnosis.

**C. A contract test that would have caught this.** See tests 1 and 2 below. The critical property is
that the assertion runs against the **real** channel layer serialization path, not a mock. A mocked
`group_send` accepts a `datetime` happily — which is precisely why the existing suite is green while
production has never delivered a frame.

## NON-GOALS / DO NOT TOUCH
- Do **not** add a `sender` object here. That is MSG-7, in the same serializer file — **see the conflict
  note below.**
- Do **not** change frame types, payload contents, or recipient resolution. Scope A changes only the
  encoding, never what is in the frame.
- Do **not** touch `useRealtimeCore` or anything in `ui-core-micha`. Once frames actually arrive, ucm's
  handling of `read_state`/`delivered`/`thread_read_state` becomes relevant — that is `ui-core-micha`
  MSG-6e scope C, and it is now unblocked rather than in scope here.
- No new dependencies, no model or migration changes.
- Do not change the channel-layer backend. Swapping to a JSON layer would also "fix" this and is the
  wrong fix — it hides the encoding problem behind configuration.

## SEQUENCING WITH MSG-7 — read this before starting
MSG-7 (the `sender` object) touches `serialize_message` in the same file. **Land MSG-8 first.** Two
reasons: it is the higher-severity defect, and MSG-7's realtime-parity test (its test 3) is meaningless
while no frame is deliverable. If both are already in flight, sequence them — do not run them
concurrently against the same file.

Note MSG-7 currently carries a non-goal saying live WS push is "still undiagnosed, and its home is not
established". That line is now obsolete; this WO is its home.

## RISKS
- **The fix is small and the blast radius is the whole platform.** Every WS consumer in every app
  receives the re-encoded payload. The mitigation is the REST-parity requirement in scope A: if the WS
  shape equals the REST shape, no client can regress.
- A hand-rolled recursive encoder will miss a type (`Decimal`, `UUID`, `date`, `time`). `UUID` is
  particularly likely here — the messaging domain is UUID-keyed, and the current payloads mostly
  stringify ids by hand, which is the same latent bug waiting for the one place that forgot.
- Raising the log level (scope B) may surface a burst of pre-existing failures in other apps on first
  deploy. That is the point, but warn the operator so it is not read as a new regression.
- Changing `push_to_users` affects the notification path too, which currently works. Test 3 guards it.

## REQUIRED TESTS TO WRITE
Narrow and behavioural. Do NOT run the full suite.

1. **The regression test that must fail today:** sending a message publishes a frame that survives the
   **real** channel-layer encoding. Assert against the actual serializer the configured layer uses — a
   test that mocks `group_send` passes against the broken code and is worthless here.
2. Same for the `conversation_upsert` frame, and for `message_edited` — all three carry a serialized
   payload with raw datetimes today.
3. The notification (non-messaging) push path still works unchanged — the regression guard for touching
   the shared chokepoint.
4. WS and REST agree: for the same message, the frame's payload equals the REST body field-for-field
   after both are JSON-encoded. This is the property that keeps clients from needing a change.
5. A push failure is logged at error level and names the frame type — and the durable write still
   commits (delivery failure stays isolated).

**Non-vacuity, mandatory:** tests 1, 2 and 4 must **fail against the current code**. Run them before the
fix and record the failure output in the WO's completion note. A test that passes on unmodified code is
not testing this defect — and this defect existed precisely because the suite was green throughout the
whole MSG-5 series.

## TEST SCOPE FOR THE GATE (orchestrator)
`messaging/` plus the notifications tests covering `push_to_users`. Note the documented pre-existing
baseline failures in the messaging suite (12-13, drifting) — compare before/after via `git stash`,
require delta = 0, and **state the baseline count you measured** rather than quoting a previous WO.

## ORCHESTRATOR NOTE ON SEQUENCING (as actually run)
This WO's own text says "land MSG-8 first." In practice `django-core-micha` MSG-7 was already in
flight (Codex actively editing `serializers.py`/`services.py`) when this was prepared, so the two were
**not** run concurrently against each other as the WO warns against — MSG-8 is prepared here to hand
over **once MSG-7's diff has landed** (reviewed, tested, committed), not before and not in parallel.
Two consequences for whoever implements this:
- **Every `path:line` reference below and in the envelope above was taken against pre-MSG-7 code.**
  MSG-7 adds a `sender` object to `serialize_message`/`serialize_last_message`, removes
  `delivered_count`/`mark_delivered`, and adds `message_id` to `serialize_poll` — all in the same files
  this WO touches. **Re-locate every cited line by content (function name, the exact snippet quoted),
  never by line number** — they will have shifted.
- MSG-7's own test 3 ("a realtime `message` frame carries the same `sender` shape as the REST payload")
  was written and gated **before** MSG-8 landed. Per this WO's `:108-112`, verify after this WO ships that
  MSG-7's test 3 actually exercises real frame delivery correctly (it almost certainly asserts on the
  frame dict `_publish`/`push_to_users` receives, not on a client actually decoding msgpack — check which,
  and if it were ever silently relying on the broken path succeeding, that would itself be a finding).

## IMPLEMENTATION MAP (Orchestrator)

### Context package

**A — the chokepoint fix (`src/django_core_micha/notifications/delivery.py`)**
- `push_to_users` (function body `:38-61` pre-MSG-7/8; re-locate by name). Add, right after `channel_layer`
  is confirmed non-`None` and before the `for user in recipients` loop:
  ```python
  from django.core.serializers.json import DjangoJSONEncoder
  payload = json.loads(json.dumps(payload, cls=DjangoJSONEncoder))
  ```
  `json` is already imported at `:2`. `DjangoJSONEncoder` converts `datetime`/`date`/`time`/`timedelta`/
  `Decimal`/`UUID`/lazy-translation objects to JSON-safe primitives (datetimes → ISO-8601 strings — the
  same shape DRF's renderer already produces for the REST path, so this is the "verify the equivalence
  explicitly" the envelope asks for: assert a REST-serialized and a WS-serialized copy of the same message
  produce identical `created_at`/`edited_at` strings). **No existing `DjangoJSONEncoder` usage exists
  anywhere in this repo** (confirmed by grep) — this is a new import, not a convention to match.
  Round-tripping through `json.dumps`/`json.loads` (rather than a hand-rolled recursive walker) is
  deliberate: it reuses Django's own encoder instead of re-implementing type coverage, directly addressing
  the envelope's "a hand-rolled walker will miss a type" risk.
- Rewrite the `except Exception as exc:` branch (`:60-61`) from `logger.warning` to `logger.error`,
  including the frame's own discriminator and the payload's keys in the message. **The discriminator key
  differs by caller**: messaging frames use `payload["type"]` (`realtime.py`'s `frame = {"envelope":
  "messaging", "type": event_type, ...}`), but plain notification payloads use `payload["kind"]` (see
  `notifications/tests/test_delivery.py:126`, `push_to_users(users, {"kind": "update"})` — no `"type"` key
  at all there). Read whichever discriminator is present generically, e.g.
  `payload.get("type") or payload.get("kind")`, plus `list(payload.keys())`, so the log line is useful for
  both callers, not just messaging's.

**B — test infrastructure: the existing suite cannot see this bug, by construction**
- `messaging/tests/test_services.py`'s `_capture_frames` helper (`:279-285`) monkeypatches
  `django_core_micha.messaging.realtime.push_to_users` itself — every existing messaging realtime test
  bypasses the function this WO fixes entirely. That is precisely why the whole MSG-5 series shipped
  green. **Do not extend `_capture_frames` for the new regression tests** — a test built on it can never
  exercise real encoding, by design.
- `notifications/tests/test_delivery.py:114-131`
  (`test_push_to_users_targets_only_each_users_group`) is the existing pattern for exercising
  `push_to_users` itself: monkeypatch `channels.layers.get_channel_layer` to a fake in-process layer whose
  `group_send` just records `(group, event)`, and monkeypatch `asgiref.sync.async_to_sync` the same way.
  This test's payload (`{"kind": "update"}`) is already JSON-safe, so it must still pass unchanged after
  scope A — that is your regression guard for test 3 (non-messaging path unaffected). **Do not modify this
  test**; write new ones alongside it.
- **The real serializer, with no Redis connection required:** `channels_redis.serializers.registry`
  (`from channels_redis.serializers import registry`) exposes the exact encoder the configured layer uses
  — `registry.get_serializer("msgpack")` returns an instance with a pure, connection-free `.serialize(obj)`
  method (confirmed: `RedisPubSubChannelLayer.__init__` in `channels_redis/pubsub.py` builds
  `self._serializer` via this same registry call with `serializer_format="msgpack"`, the platform's default
  — `settings_base.py:186-193`). Required tests 1, 2 and 4 should call
  `registry.get_serializer("msgpack").serialize(captured_payload)` on the payload captured via the
  fake-`group_send` pattern above (built through the real `send_message`/`edit_message`/
  `resolve_live_recipients` → `_publish` → `publish_messaging_event` → `push_to_users` chain, inside
  `django_capture_on_commit_callbacks(execute=True)`, same as every other `services.py` test) — that
  `.serialize()` call is what raises today (`msgpack` cannot encode a raw `datetime`) and must stop raising
  once scope A lands. This is a real unit-level exercise of the actual production encoder, satisfying the
  envelope's "not a mock" requirement without needing a running Redis instance.
- Test 4 (WS/REST parity): serialize the same message both ways — `serialize_message(message)` (or the
  REST view response) run through DRF's renderer/`json.dumps`, and the captured WS frame's `message` key
  run through the same — compare the resulting JSON-safe dicts for equality on the datetime fields at
  minimum.
- Test 5 (error-level log + isolation): monkeypatch the fake layer's `group_send` to raise, assert
  `caplog`/`logger` records an `ERROR` (not `WARNING`) mentioning the frame type/kind, and that the
  triggering `send_message`/etc. call still returns normally (the durable write is untouched — this is
  already true today for the exception-isolation part; you're only changing the log level and message).

### Do-not-touch reminders (from the envelope, restated)
No `sender` object here (MSG-7's job); no frame-type/payload/recipient changes; no touching
`useRealtimeCore`/`ui-core-micha`; no new dependencies or channel-layer backend swap.

### Target repo working directory
`C:\Users\biglmi\Documents\webapps\django-core-micha` (git root — no `backend/`/`frontend/` split).

### Version bump
Unlike MSG-7 (additive/no-bump this run), this is a genuine behavioural bug fix every consuming app wants
— a real version bump applies here per the repo's normal release flow. Current version before this WO:
`pyproject.toml` `2.39.3` (bump target is the Orchestrator's call at publish time — patch vs minor — per
whether scope B's log-level change counts as a public contract change; state your reasoning).

## TARGET REPO
`C:\Users\biglmi\Documents\webapps\django-core-micha`. Branch `develop` if it exists, else `main`.
Publish + version bump per the repo's release flow. Consuming apps' pin bumps are **not** part of this
WO, but flag in the completion note that this is a fix every consuming app wants promptly.

## PROGRESS CONTRACT
Emit `PLAN: <steps>` up front, then a single-line `PROGRESS: [<n>/<total>] <action>` before every
relevant action and `PROGRESS: [<n>/<total>] done` on completion, spaced so no gap exceeds ~2 min,
stdout unbuffered, and exactly one final `RESULT: DONE|BLOCKED <reason>`.
