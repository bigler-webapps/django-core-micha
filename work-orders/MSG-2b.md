# MSG-2b — scoped first-contact DM must be possible

Status: planned · Tier 1 (bounded change, no schema) · Target repo: `django-core-micha` (main)
**Binding spec:** `docs/design/messaging-platform.md` §"Tenant resolution and deletion semantics",
including the 2026-07-31 MSG-2b clarification. On any conflict the design doc wins.

Extends the landed MSG-2 (published 2.36.0) the way `NOTIF-8b`/`8c` extended `NOTIF-8`: an unplanned
correction to shipped behaviour, not a new feature.

---

## Part A — Envelope (Expertenchat, 2026-07-31)

### Goal

Make it possible to **start** a scope-anchored direct conversation with a user who has no prior
conversation in that tenant. Today it is impossible: `DirectConversationView` requires the target to
already hold a `ConversationParticipant` row in the resolved app, so a DM can only ever be continued.

### Background — why this is wrong on both sides

`src/django_core_micha/messaging/views.py:133-134` rejects the request when
`scope is not None` and the target has no participant row anywhere in the resolved app. Two problems:

1. **It forecloses first contact.** Every conversation has to start somewhere; the check makes the
   scoped case unreachable. (The global case is unaffected — the guard is scope-conditional.)
2. **It is not a security boundary.** Any user acquires a participant row after a single
   conversation, so the check constrains an attacker barely at all while blocking every legitimate
   first message. It is a weak proxy for a decision the design already assigns elsewhere.

The real authorization already exists and already runs: `services.py:78-85` `open_direct()` calls
`policy.can_open_direct(actor=…, target=…, scope=…)` before any row is created, and raises
`MessagingPermissionDenied` when the app says no. The view-level check sits *in front of* that hook
and pre-empts it, so an app cannot even express "yes, these two may talk".

This traces to an ambiguous sentence in the MSG-2 design addendum ("the target user is validated
against the resolved tenant"), corrected in the design doc as part of this WO. The core owns tenant
resolution and self-DM rejection; **who may be addressed inside a resolved tenant is an app
decision.**

### Expected outcome

- The participant-existence precondition in `DirectConversationView` is removed. Tenant safety for
  the target rests solely on `MessagingPolicy.can_open_direct`, which `open_direct()` already
  enforces.
- Server-side tenant resolution is **unchanged**: scope given → `scope.app`; scope omitted →
  the single active `MessagingApp`, fail-closed on 0 or N. `app_key` is still never read from any
  request. Do not weaken, reorder or "simplify" `resolve_messaging_app`.
- Self-DM rejection stays core-owned (`open_direct`, unchanged).
- A policy returning `False` from `can_open_direct` still yields the same denial as today.
- The stale inline comment at `views.py:135-136` is corrected to match the resulting behaviour.

### Non-goals / do-not-touch

Tenant resolution logic; the `app_key` boundary; encryption/keyrings; the soft-delete redaction from
MSG-2 chunk 3; any other endpoint's viewer-vs-participant rules; ucm (MSG-3); any app-side code;
schema (this WO has **no migration** — if one appears to be needed, stop and return to the operator);
no dependency changes.

### Required tests to WRITE

Security-relevant surface, so the permission set is mandatory, not optional:

- **First contact succeeds:** a scoped DM opens against a target with **zero** participant rows in
  the app, when the registered policy permits it. This is the regression this WO exists for.
- **Policy denial still denies:** the same call with a policy returning `False` from
  `can_open_direct` yields the established denial response, proving authorization moved rather than
  disappeared.
- **The hook is actually consulted** for the first-contact case (it must not be short-circuited).
- **Tenant resolution regression set stays green and is re-asserted:** a supplied `app_key` in the
  request body has no effect; scope-given resolves via `scope.app`; scope-omitted with 0 or N active
  registrations still fails closed.
- **Self-DM still rejected.**

Narrow per AGENTS.md "Test scope": the messaging suite's affected area (views/services/policy), not
the full dcm suite. The existing MSG-2 messaging + notifications tests must stay green.

### Risks

- The removed line reads like a security control; a reviewer unfamiliar with `open_direct` may flag
  its removal as a regression. The WO's answer is the call chain above — authorization is not being
  dropped, it is being left to the layer the design assigns it to. Make this explicit in the commit
  message.
- An app whose policy is permissive (`can_open_direct` returning `True` unconditionally) now has no
  second net. That is the design's stated contract ("each app registers one deterministic,
  tenant-safe provider") and is accepted; it is also already true for every other messaging
  operation.

### Release

One patch version bump + PyPI publish at WO end (`pyproject.toml` + a `CHANGELOG.md` entry in the
established prose style). No consumer pins are bumped here; MSG-3/MSG-5 pick the release up via
their own registry live-check before pinning.

### Preconditions

MSG-2 done and published (met, 2.36.0). Operator decision recorded 2026-07-31: scoped first contact
**must** be possible. Approval Gate #1 for this WO = that decision.

### Execution directive

Implement through `codex exec` in the background — invoked **directly via Bash** (never the
`debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check` and
`--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from a file;
fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

### Mini-handover (pastable)

Orchestrator: implement `work-orders/MSG-2b.md` in `django-core-micha` (main). `git pull` first, read
the WO + `docs/design/messaging-platform.md` §"Tenant resolution and deletion semantics", then follow
`orchestrate-codex` (Codex-first, own independent review, commit on green, one publish at WO end).

---

## Part B — Implementation map (Orchestrator)

### Target repo / working directory

`C:\Users\biglmi\Documents\webapps\django-core-micha` (repo root; current published version 2.36.0).

### Context package

**Named file to change:**
- `src/django_core_micha/messaging/views.py` — `DirectConversationView.post`, currently (as of MSG-2
  chunk 3, commit a6a4cf5):
  ```python
  class DirectConversationView(MessagingView):
      def post(self, request):
          target = get_object_or_404(get_user_model(), pk=request.data.get("target_user_id"))
          scope_id = request.data.get("scope")
          scope = get_object_or_404(MessagingScope.objects.select_related("app"), pk=scope_id) if scope_id else None
          try:
              app = resolve_messaging_app(scope=scope)
          except MessagingTenantResolutionError as exc:
              raise ValidationError({"detail": str(exc)}) from exc
          if scope is not None and not ConversationParticipant.objects.filter(conversation__app=app, user=target).exists():
              raise ValidationError({"target_user_id": "Target user is not established in the resolved tenant."})
          # Global DMs have no User->MessagingApp relation in this schema.  The
          # singleton registry resolution above is the accepted v1 boundary.
          conversation = self._service(lambda: open_direct(actor=request.user, target=target, app=app, scope=scope))
          participant = ConversationParticipant.objects.get(conversation=conversation, user=request.user)
          return Response(serialize_conversation(conversation, participant), status=status.HTTP_201_CREATED)
  ```
  Delete the `if scope is not None and not ConversationParticipant.objects.filter(...)` block (the
  four lines: the `if`, the `raise`, and the stale comment above `conversation = self._service(...)`).
  Replace the comment with one reflecting the corrected model: tenant resolution is core-owned
  (`resolve_messaging_app`, unchanged); who may be addressed inside that tenant is
  `MessagingPolicy.can_open_direct`'s decision, already enforced inside `open_direct()`
  (`services.py`) before any row is created. Nothing else in this method changes — `resolve_messaging_app`
  call, the `try/except MessagingTenantResolutionError`, and the rest of the flow stay exactly as is.

**Do not touch (unrelated to this bug, explicitly out of scope per envelope):**
- `src/django_core_micha/messaging/models.py`'s `resolve_messaging_app` — tenant resolution logic is unchanged.
- `src/django_core_micha/messaging/services.py`'s `open_direct` — already calls `can_open_direct`
  correctly (services.py:78-85 as of a6a4cf5); this WO does not change it, only removes the
  view-level check that pre-empted it.
- Any other view class, `soft_delete_message`'s content redaction, encryption/keyrings, `urls.py`.

**Source material for context (read-only, already correct — do not modify):**
- `src/django_core_micha/messaging/services.py`'s `open_direct(*, actor, target, app, scope=None)` —
  confirms `policy.can_open_direct(actor=actor, target=target, scope=scope)` is called and
  `MessagingPermissionDenied` is raised on `False`, before any `Conversation`/`ConversationParticipant`
  row is created. This is the authorization path that now runs unobstructed.
- `src/django_core_micha/messaging/tests/test_views.py`'s existing
  `test_direct_scope_resolves_tenant_without_reading_app_key` test (added in MSG-2 chunk 3) — the
  tenant-resolution regression test that must stay green; extend or add alongside it, do not weaken it.
- `src/django_core_micha/messaging/tests/test_services.py` — has `open_direct`/self-DM test precedent
  if one exists; check before writing a new self-DM test to avoid duplication.

**Invariants:**
- No migration. If implementing this change appears to need one, stop and return to the operator —
  the envelope is explicit this WO has no schema change.
- `resolve_messaging_app`'s behavior (scope→scope.app; no scope→single active app else fail-closed
  400/409; `app_key` never read from any request) must be byte-identical after this change — the
  required test set re-asserts this explicitly, not just as a side effect.
- Self-DM rejection (`actor.pk == target.pk` in `open_direct`) is unchanged and must still be covered.

### Required tests

Per envelope's "Required tests to WRITE" — scoped to `views.py`/`services.py`/`policy.py`
(the messaging suite's affected area), not the full dcm suite:
1. First-contact scoped DM succeeds against a target with zero participant rows, when policy permits.
2. Same call with a policy returning `False` from `can_open_direct` → same denial as today.
3. The hook is actually consulted (not short-circuited) for the first-contact case — e.g. assert it
   was called, or that changing the policy's return value changes the outcome.
4. Tenant-resolution regression set stays green: `app_key` in the request body has no effect;
   scope-given resolves via `scope.app`; scope-omitted with 0 or N active registrations fails closed.
5. Self-DM still rejected.

### Progress contract

Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
`PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (file opened, file
edited, command/test run) and `PROGRESS: [<n>/<total>] done` on step completion, spaced so no gap
exceeds ~2 min, stdout unbuffered, and exactly one final `RESULT: DONE|BLOCKED <reason>`.

### Preamble (must be appended verbatim to the Codex prompt)

The text above is the COMPLETE spec — read nearest `AGENTS.md`, `.codex/skills/<role>/SKILL.md` (if
present), and this repo's `MEMORY.md` only for conventions; stay in scope; do not touch anything
outside `views.py`'s `DirectConversationView.post` plus the required test additions; do not touch
auth/CI/dependencies/schema; do not update `MEMORY.md`; do NOT `git add`/`commit`/`push` — leave the
change uncommitted in the working tree for the orchestrator's independent review. WRITE the required
tests AND RUN the messaging test suite (`src/django_core_micha/messaging/tests/`) to confirm they
pass — that is your only test run: do NOT run the full dcm suite and do NOT run any review; the
orchestrator does both after you finish.

### Mini-handover

(Already given verbatim by the operator — see the message that handed this WO over.)
