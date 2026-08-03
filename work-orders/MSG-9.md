# WORK ORDER MSG-9 (django-core-micha) — expose a read COUNT, not just a boolean, and batch the lookup

**EXECUTION DIRECTIVE.** If you are the implementer reading this as your own spec, this section is not
addressed to you — it tells the Orchestrator how to invoke you; you ARE that invocation, do not shell
out to `codex exec`. Orchestrator: implement through `codex exec` in the background, invoked **directly
via Bash** (never the `debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check`
and `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from this
file. Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

Feature-level WO (Expertenchat envelope). **Prerequisite for `ui-core-micha` MSG-6f scope B**, which is
blocked until this lands.

> **ID note:** this is dcm's own `MSG-9`. `jg-ferien` has an unrelated `MSG-9` (its staging-findings WO)
> — different repo, different namespace. Always say which when referencing one across repos.

## TIER
Tier 2 — shared-core API addition consumed by every app. Independent `reviewer` mandatory.
`sec_reviewer` for scope A (it decides who may see an aggregate read count).

## WHY

Operator requirement, 2026-08-03: in a camp, a leader posting "bus leaves at 14:00" to forty
participants needs to know **whether a relevant share has read it**. That is a proportion, not a
boolean.

`read_status` (`services.py:342-352`) today returns `all_read` plus a manager-gated `recipient_detail`.
`all_read` is computed from precisely the queryset that would yield the number:

```python
participants = conversation.participants.filter(removed_at__isnull=True).exclude(user=message.sender)
all_read = not participants.exclude(last_read_at__gte=message.created_at).exists()
```

In any group `all_read` is essentially never true, so the indicator is pinned to its first state exactly
where the information matters most. **The data is already there and is discarded.** Note the irony
worth not repeating: `delivered_count` — a counter with no writer at all — was built and shipped, while
the count that does have a writer was reduced to a boolean.

## SCOPE

**A. Add `read_count` and `recipient_count` to the `read-status` response.**

- `recipient_count` = `participants.count()` (the existing queryset — non-removed participants excluding
  the sender, so it is the denominator a sender actually cares about).
- `read_count` = `participants.filter(last_read_at__gte=message.created_at).count()`.
- **Keep `all_read`** — ucm's DM branch still uses it, and removing it is a needless break.
- Compute both in the **same** round trip as `all_read`; do not add two extra queries. An aggregate over
  the one queryset is the target.

**Visibility (operator decision, 2026-08-03): the counts are TEAM-ONLY — put them inside the existing
gated block.** `services.py:340` already reads:

```python
if conversation.kind != Conversation.Kind.DIRECT and "read_receipt_detail" in rights:
    result["recipient_detail"] = ...
```

Add `read_count` and `recipient_count` **in that same block**. Consequences, all intended:

- A team member (in jg: `read_receipt_detail` ∈ `MANAGER_RIGHTS`) sees the ratio and the per-person
  detail — one right, one privacy line, no new vocabulary.
- An ordinary participant posting in a group gets **no counts at all**. That is the decision: the
  operational need is a leader knowing whether the group is informed, not a participant measuring their
  own reach.
- DMs get no counts, which is correct — `all_read` already carries everything a two-party chat needs,
  and a 1-of-1 ratio is noise.

**This is deliberately narrower than an earlier draft of this WO**, which proposed exposing the
aggregate to every sender. Do not re-widen it. It also means **no new permission logic and no
`sec_reviewer` question to resolve** — the counts inherit a gate that already exists and is already
tested.

Do not change `recipient_detail`'s gating or the DM carve-out.

**B. A batch read-status endpoint.**

ucm's `Thread` mounts a `ReadTicks` per own message, and each fires its own `read-status` request —
O(n) for exactly the broadcast-heavy leader this feature serves. Add an endpoint taking a list of
message ids and returning the same per-message aggregate, in a bounded number of queries (not one per
id).

Constraints:
- Enforce the same per-message view permission as the single endpoint — a batch must not become a way to
  read status for a message the caller could not fetch singly. **This is the security-relevant part of
  scope B.**
- Cap the id list (follow the existing `_limit` convention, `views.py:93-100`) and reject oversized
  requests rather than silently truncating.
- Keep the single-message endpoint; ucm will migrate, other consumers may not have.

**Do NOT fold the counts into `serialize_message` instead.** `views.py:50-54` documents why
viewer-specific read state is deliberately kept out of it: it would leak into the `message` /
`message_edited` realtime frames, the exact trap `thread_last_read_at` was kept out of, and
`voted_option_ids` before it. That comment is a standing decision, not an oversight.

## NON-GOALS / DO NOT TOUCH
- Do not reintroduce `delivered_count` or `mark_delivered`. MSG-7 removed them deliberately; a browser
  app cannot honestly report delivery (argued in ucm MSG-6f).
- Do not change `mark_read`, `last_read_at`, or `unread_counts` — the write path and the unread badge are
  correct and load-bearing.
- No model or migration changes. Both counts derive from existing columns.
- Do not change `MessageThreadReceipt` / `thread_last_read_at`.

## RISKS
- **Query cost.** `read_status` is called per own message today; a naive `read_count` doubles its
  queries. Scope B is the mitigation, but scope A must not regress the single endpoint in the meantime.
- **The denominator is a moving target.** `recipient_count` excludes removed participants, so a
  historical message's ratio changes as people leave. That is the correct behaviour (the sender cares
  about who is *currently* meant to read it), but it must be a stated decision, not an accident —
  document it next to the field.
- A batch endpoint that skips the per-message permission check is an IDOR. Test 4 exists for this.
- `read_count` counts `last_read_at >= created_at` — a participant who read the conversation *before*
  this message was posted does not count, which is right. Verify the boundary rather than assuming it.

## REQUIRED TESTS TO WRITE
Narrow and behavioural. Do NOT run the full suite.

1. `read_status` returns `read_count` and `recipient_count`; with 3 non-sender participants of whom 1 has
   read, the values are `1` and `3`, and `all_read` is still `False`.
2. When every non-sender participant has read, `read_count == recipient_count` **and** `all_read` is
   `True` — the two must not be able to disagree.
3. An ordinary sender **without** `read_receipt_detail` gets **neither** `read_count`/`recipient_count`
   **nor** `recipient_detail` for a group message — assert the counts are absent, not zero. A team
   member on the same message gets both. This is the privacy line; assert both sides.
4. **The batch endpoint enforces per-message permission**: a caller who cannot view message X singly
   gets no data for X in a batch that includes it. Assert that X is absent/denied, not merely that the
   call succeeded.
5. The batch endpoint's query count does not scale with the number of ids (follow the existing pattern
   in `test_platform_launchers_query_count_does_not_scale_with_event_count`).

**Non-vacuity:** test 3 must fail if the gating is removed, and test 4 must fail if the permission check
is dropped. Prove both by reverting the guard, not by inspection — this repo has shipped a permission
test that passed with its guard deleted.

## TEST SCOPE FOR THE GATE (orchestrator)
`messaging/` only. Note the documented pre-existing baseline failures in this suite — compare
before/after via `git stash`, require delta = 0, and **state the baseline count you measured** rather
than quoting a previous WO's number.

## TARGET REPO
`C:\Users\biglmi\Documents\webapps\django-core-micha`. Branch `develop` if it exists, else `main`.
Publish + version bump per the repo's release flow. `ui-core-micha` MSG-6f scope B consumes this and is
blocked until it is published.

## MINI-HANDOVER (pastable)

> Repo: `C:\Users\biglmi\Documents\webapps\django-core-micha` (branch `develop` if it exists, else
> `main`). Work order: `work-orders/MSG-9.md` — read it fully, then follow the `orchestrate-codex`
> skill. This is **dcm's own MSG-9**; jg-ferien has an unrelated MSG-9, do not confuse them.
>
> Tier 2, shared-core. No open decisions — the operator settled the visibility question on 2026-08-03:
> the counts are **team-only** and go **inside** the existing `read_receipt_detail` block at
> `services.py:340`, so there is no new permission logic and no privacy question to resolve. Do not
> re-widen it.
>
> Two things that are easy to get wrong and are called out in the WO: the counts must not cost extra
> queries on the single-message endpoint, and the batch endpoint must enforce the **same per-message
> view permission** as the single one (test 4 — an IDOR otherwise). Do not fold the counts into
> `serialize_message`; `views.py:50-54` documents why.
>
> Blocks `ui-core-micha` MSG-6f scope B, which cannot start until this is published.

## IMPLEMENTATION MAP (Orchestrator)

### Context package (re-verified against the current tree, post-MSG-7/MSG-8 — line numbers below are
current, not the envelope's original estimates)

- **`src/django_core_micha/messaging/services.py`**
  - `read_status` (`:334-342`):
    ```python
    def read_status(*, actor, message):
        conversation = message.conversation; _require_view(actor, conversation)
        participants = conversation.participants.filter(removed_at__isnull=True).exclude(user=message.sender)
        all_read = not participants.exclude(last_read_at__gte=message.created_at).exists()
        result = {"all_read": all_read}
        rights = _policy(conversation).moderation_rights(actor=actor, conversation=conversation, message=message)
        if conversation.kind != Conversation.Kind.DIRECT and "read_receipt_detail" in rights:
            result["recipient_detail"] = list(participants.values("user_id", "last_read_at", "last_delivered_at"))
        return result
    ```
    Add `recipient_count = participants.count()` and `read_count =
    participants.filter(last_read_at__gte=message.created_at).count()` **inside** the existing `if` block
    (the `read_receipt_detail`-gated one), alongside `recipient_detail`. Two more `.count()` calls on the
    same `participants` queryset — each is its own query (Django doesn't fuse separate `.count()` calls),
    so this is 2 extra queries for the single-message endpoint, not 0; the WO's "same round trip" ask is
    satisfied by computing them from the one already-built `participants` queryset (no new filters, no
    re-derivation), not by a literal single SQL statement. If you want zero extra queries, use
    `participants.aggregate(recipient_count=Count("pk"), read_count=Count("pk", filter=Q(last_read_at__gte=message.created_at)))`
    instead of two `.count()` calls — prefer this form, it is one query for both numbers.
  - `mark_read` (`:288-294`) — read-only reference, do not change.

- **`src/django_core_micha/messaging/views.py`**
  - `ReadStatusView` (`:294-297`) — unchanged; it already calls `read_status`, which now returns the two
    new keys inside the same gated block. No view-level change needed for scope A.
  - `_limit` (`:93-100`) — the existing query-param cap convention (1-100, `ValidationError` on
    out-of-range). The batch endpoint takes its id list in the POST body, not a query param, so this
    exact function does not apply directly, but mirror its shape: validate the list length explicitly
    and raise `ValidationError` for an oversized list rather than silently truncating (same convention,
    different input shape).
  - `MessagingView._viewer_conversation` (`:133-138`) — the exact per-conversation permission check the
    batch endpoint must replicate: `get_messaging_policy(app_key).can_view_conversation(actor, conversation)`,
    a denied view raising `NotFound` (indistinguishable from a missing object) for the single-message
    case. For the batch case: **group the requested message ids by conversation first** (one query:
    `Message.objects.filter(pk__in=ids).select_related("conversation__app")`), then run
    `can_view_conversation` **once per distinct conversation** among the results (dedup — if the caller
    requests 20 message ids all from the same thread, that is one permission check, not twenty). Drop
    every message whose conversation fails the check from the response entirely — do not include a
    `null`/`denied` placeholder keyed by that message's id, since revealing that the id exists at all
    (even minus its content) is more than the single-endpoint's `NotFound` reveals. Message ids not found
    at all, and message ids whose conversation is denied, must be indistinguishable in the response
    (both simply absent) — this is what test 4 checks.
  - **Batching the counts themselves** (the actual N+1 fix): after permission-filtering, group the
    surviving messages by `conversation_id` again. For each distinct conversation among them, fetch its
    `participants.filter(removed_at__isnull=True)` **once** (excluding removed rows only — do NOT exclude
    by sender yet, since different messages in the same conversation can have different senders) via
    `.values("user_id", "last_read_at")` into a plain Python list. Then, for each message in that
    conversation, compute `read_count`/`recipient_count`/`all_read` in Python from the already-fetched
    list (exclude that specific message's `sender_id`, compare each `last_read_at` against that specific
    message's `created_at`) — no further DB query per message. This turns "N queries for N messages" into
    "1 query per distinct conversation among the requested ids", which is the bound test 5 checks
    (ucm's actual call pattern is "every one of my own messages in this one open thread", i.e. usually
    exactly one conversation — verify the test exercises **multiple** messages in the **same**
    conversation to prove the collapse, not just multiple conversations).
  - Add a new view, e.g. `BatchReadStatusView(MessagingView)`, `POST` with body `{"message_ids": [...]}`,
    registered in `messaging/urls.py` — pick a name in the existing `messaging-*` convention (e.g.
    `messaging-read-status-batch`) at a path like `messages/read-status/batch/` (do not collide with the
    existing `messages/<uuid:message_id>/read-status/` single-message path). Apply the recipient-detail
    gating (team-only) the same way `read_status` does — same rights check, same conversation-kind
    exclusion for DIRECT.
  - Query-count regression test pattern to follow: `test_platform_launchers_query_count_does_not_scale_with_event_count`
    (grep for it in `messaging/tests/` or `notifications/tests/` — same repo, same pattern: assert query
    count is flat across an increasing input size using `django.test.utils.CaptureQueriesContext`).

### Do-not-touch reminders (from the envelope, restated)
No `delivered_count`/`mark_delivered` reintroduction; no change to `mark_read`/`last_read_at`/
`unread_counts`; no model/migration changes; no folding into `serialize_message` (`views.py:57-60`
documents why viewer-specific read state must stay out of the realtime frame).

### Target repo working directory
`C:\Users\biglmi\Documents\webapps\django-core-micha` (git root — no `backend/`/`frontend/` split).

## PROGRESS CONTRACT
Emit `PLAN: <steps>` up front, then a single-line `PROGRESS: [<n>/<total>] <action>` before every
relevant action and `PROGRESS: [<n>/<total>] done` on completion, spaced so no gap exceeds ~2 min,
stdout unbuffered, and exactly one final `RESULT: DONE|BLOCKED <reason>`.
