# DCM-NOTIF-31 — Do not record push or email as sent when the recipient cannot receive it

**Target** `django-core-micha` on `main`

**Tier** 3 (shared core, notification delivery behaviour; patch release 3.41.1).

**Status** Follow-up of DCM-NOTIF-30, found by the JG-NOTIF-29 PREF-2 test (a missing push subscription). The operator chose "fix in DCM first" on 2026-11-21.

## Part A — Envelope

### Goal and outcome

`router.resolve_channels` keeps an enabled email or push channel only when the recipient can technically receive on it: push needs at least one `PushSubscription`, email needs an address. Today a user who opted into push but has no subscription still gets the push channel, `PushDispatcher` returns `ok=True` after the transport sends nothing, and the delivery is recorded as sent. After the change such a channel is not resolved, so no delivery row claims a send that never happened. Critical types keep their existing rule (they were already availability-checked). In-app channels (chip, popup, todo) are unchanged.

### Scope

- `src/django_core_micha/notifications/router.py` (one condition), changelog and version 3.41.1.
- Tests that opted a user into push without a subscription now create one (`notifications/tests/test_dispatch.py`, `messaging/tests/test_notifications.py`); one new regression test in `test_notif26_reach_and_subscriptions.py`.

### Exclusions

No change to preferences, the channel matrix, dispatchers, retries or the digest. No retroactive correction of historical delivery rows. Consuming apps adopt the patch through their normal pin bump; apps whose tests rely on push without a subscription must add one.

### Required tests

| ID | Test and expected result |
| --- | --- |
| ROUTE-1 | Push opt-in without subscription and email opt-in without address resolve to no channel; adding a subscription resolves push. |
| ROUTE-2 | Existing dispatch, messaging and notification suites stay green with realistic subscriptions. |

## Completion

Implemented as specified; version 3.41.1. Tests: notification, messaging and activity suites 314 passed; two attachment tests fail only because PIL is not installed locally (environment). Review: round 1 raised 1 finding (tests lens, defect: new test did not assert that an available email channel is kept), accepted and fixed; closing round 2 raised 0 (the regression lens had to be restarted once and then reported no findings). Carried: apps with tests that opt a user into push without a subscription must add one after the pin bump (documented in the changelog).

## Gate record

Gate #1 approved by the operator 2026-11-21 ("Fix in DCM first" for the push-without-subscription defect found in JG-NOTIF-29, patch release of DCM-NOTIF-30's contract).
