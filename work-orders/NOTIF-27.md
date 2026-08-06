# WORK ORDER NOTIF-27 — SUPERSEDED, merged into NOTIF-26

**Status: `dropped` (2026-08-06, before any implementation started). Not cancelled work — merged
work.** Everything specified here is now scope blocks D–H of
[`work-orders/NOTIF-26.md`](NOTIF-26.md).

## Why it was merged

NOTIF-27 (subscription-based recipient resolution + category-driven `NotificationSettings`) and
NOTIF-26 (active/passive reach model) were authored as two independent WOs, each stating the other
could land in either order. A plan review on 2026-08-06 found that claim to be false: both reshape
the **same** `preferences/` response (`notifications/urls.py:17`, `NotificationPreferenceView`) and
both rebuild the **same** component (`ui-core-micha/src/notifications/NotificationSettings.jsx`),
with neither WO acknowledging the other's change to those surfaces. Whichever implementer ran second
would have inherited an already-changed payload and component with no instructions to account for
them.

Operator decision 2026-08-06: merge into one WO rather than serialise two. The two axes stay
conceptually distinct and are kept as separate scope blocks inside NOTIF-26; they are implemented
and reviewed as one diff because they share two surfaces.

**Do not implement from this file.** Go to [`NOTIF-26.md`](NOTIF-26.md).
