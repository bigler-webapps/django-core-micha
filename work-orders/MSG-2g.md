# MSG-2g — conversation list returns duplicate rows for multi-participant conversations

Status: planned · Tier 1 (small, mechanical, non-sensitive, no schema change) · Target repo: `django-core-micha` (main)

---

## Part A — Envelope (Expertenchat, 2026-08-01)

### Goal

`GET conversations/` must return each conversation at most once, regardless of how many
participants it has.

### Origin

Found 2026-08-01 by `jg-ferien` MSG-5d (sub-step 2.1, re-enabling group/broadcast launchers after
MSG-2f) via a live round-trip test: a group conversation with two participants (one who created it,
one added only through `reconcile_membership`) appeared **twice** in the second participant's
`GET conversations/` results for the exact same conversation id. jg's Orchestrator diagnosed the root
cause directly (read-only, no dcm edit) before writing this WO — see below — rather than routing
around it or guessing.

### Confirmed root cause (read directly, cite line numbers when touching this)

`ConversationListView.get()` (`src/django_core_micha/messaging/views.py:157-161`):

```python
qs = Conversation.objects.select_related("app", "scope").filter(participants__user=request.user, participants__removed_at__isnull=True)
if request.query_params.get("include_archived") != "true":
    qs = qs.filter(participants__archived_at__isnull=True)
```

The `archived_at` check is a **second, separate** `.filter()` call spanning the same multi-valued
`participants` relation as the first. Django does not reuse the join across separate `.filter()`
calls on a to-many relation — it adds a **new JOIN**, so the two conditions are allowed to match
**different** related rows rather than being required to hold for the same one. The query becomes
"conversation has *some* participant with `user=X, removed_at=null`" **AND independently** "has
*some* participant with `archived_at=null`" — for a conversation with 2+ non-archived participants,
the second join matches every one of them, multiplying that conversation's row in the result set by
however many non-archived participants it has.

This is a **general bug, not specific to `group`/`broadcast`** — any conversation kind with 2+
participants and a request that doesn't set `include_archived=true` (the default, i.e. almost every
real call) is affected. It was latent until now because reaching this code path requires an actual
multi-participant conversation to exist and be listed by a participant other than its creator; MSG-2f
(landed as dcm 2.39.0) is what first gave `group`/`broadcast` conversations that shape via live
`reconcile_membership`, exposing a query bug that predates it.

**Confirmed via isolated reproduction** (jg's Orchestrator, read-only Django ORM investigation): a
query using only the first two conditions in one `.filter()` call returned exactly 1 row for a
2-participant conversation; re-adding the separated `archived_at` condition as the real view does is
what produces the duplicate — not independently re-verified with a temporary DB inspection tool, but
directly readable from Django's documented multi-valued-relation `.filter()` chaining semantics, which
this diagnosis relies on.

### Scope

Fix `ConversationListView.get()` so all three participant-row conditions (`user`, `removed_at`, and —
when not `include_archived=true` — `archived_at`) constrain the **same** joined `ConversationParticipant`
row, not independently-joined ones. The straightforward fix is combining them into a single `.filter()`
call:

```python
participant_filters = {"participants__user": request.user, "participants__removed_at__isnull": True}
if request.query_params.get("include_archived") != "true":
    participant_filters["participants__archived_at__isnull"] = True
qs = Conversation.objects.select_related("app", "scope").filter(**participant_filters)
```

(illustrative — the implementer should verify this is actually the cleanest correct expression, not
copy it verbatim if a better pattern fits this file's existing style better). Do **not** paper over
this with `.distinct()` alone — that would suppress the duplicate row but leave the underlying "these
two conditions can match different participants" semantic bug in place, which could still misbehave
in other ways (e.g. combined with future additional participant-scoped filters added to this view).

### Explicitly NOT in scope

Any other endpoint's participant-scoped filtering (audit whether the same chained-`.filter()` pattern
exists elsewhere in `views.py`/`services.py` as a **read-only finding to report back**, not to fix
silently under this WO, unless it's a one-line fix of the exact same shape — use judgement, but do not
expand this WO into a broad refactor). No `ucm`/`jg-ferien` change.

### Required tests to WRITE

- **The exact regression**: a conversation with 2+ non-archived participants, queried by a participant
  who is not its creator, appears exactly once in `GET conversations/` results (not zero, not two).
- **`include_archived=true` still works** and doesn't accidentally start requiring the archived
  condition when the caller explicitly asked to include archived rows.
- **Non-regression**: existing single-participant-relevant list tests (`direct`, any existing
  `managed` list test) stay green unchanged — this fix must not change results for the common case
  that was already correct.

### Risks

Low — this is a query-correctness fix with no schema change, additive test coverage, and a narrow,
well-understood root cause. The main risk is scope creep into "also fix similar patterns elsewhere" —
resist that; report other instances, don't fix them here unless trivial and identical in shape.

### Preconditions

None. Self-contained.

### Cross-repo rule

This WO lives in `django-core-micha` itself — the "do not modify dcm" rule that gated the discovery
session does not apply here.

### Mini-handover (pastable)

Orchestrator: implement `work-orders/MSG-2g.md` in `django-core-micha` (main). `git pull` first, read
the WO, then follow `orchestrate-codex`. Tier 1-eligible (small, mechanical, no schema change) — but
use judgement: if `ConversationListView.get()` turns out to need more than the one-line combine, or
touches auth-adjacent logic beyond simple query correctness, treat as Tier 2 instead.

---

## Part B — Implementation map (Orchestrator)

To be filled by the Orchestrator session on `git pull`, within the envelope above.
