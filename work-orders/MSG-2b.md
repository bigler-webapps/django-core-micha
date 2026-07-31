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

To be filled by the Orchestrator session on `git pull`, within the envelope above.
