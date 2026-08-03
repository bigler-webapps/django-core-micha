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

## PROGRESS CONTRACT
Emit `PLAN: <steps>` up front, then a single-line `PROGRESS: [<n>/<total>] <action>` before every
relevant action and `PROGRESS: [<n>/<total>] done` on completion, spaced so no gap exceeds ~2 min,
stdout unbuffered, and exactly one final `RESULT: DONE|BLOCKED <reason>`.
