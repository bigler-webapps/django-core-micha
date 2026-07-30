# WORK ORDER NOTIF-20 (jg-ferien + cockpit) — turn on notification retention

> **STATUS 2026-07-30: BLOCKED — paused by operator decision, returned for re-authoring.**
> Not implemented. Nothing was committed for this WO. See "ORCHESTRATOR FINDING" below: the
> envelope's core premise — that adding `prune_notifications` to `project.yaml` makes the janitor
> run where the data grows — does not hold for production. Re-author with the role constraint in
> view before handing this back for implementation.

---

## ORCHESTRATOR FINDING (2026-07-30) — why this was paused

**The `scheduled_commands` role is granted to `staging` only, so scope A/B cannot bound production
growth — the exact thing this WO exists to fix.**

Verified chain:
1. `webapp-management/.github/workflows/scheduled-commands.yml` (daily `0 4 * * *`) resolves its
   targets via `resolve_inventory_targets.py --role scheduled_commands` (`:76`), and explicitly
   tolerates zero carriers: *"No target currently carries the 'scheduled_commands' role — nothing
   to run."*
2. `webapp-management/project.yaml:82` grants `roles: [traefik, restore, scheduled_commands]` to
   **`staging`**.
3. `main-prod`, `contact-prod` and `innoservice-prod` all carry
   `[traefik, backup, maintenance, janitor, ssh_sync, restore]` — **no `scheduled_commands`**
   (`project.yaml:51,59,64`).

So adding `prune_notifications` to jg's `infra.scheduled_commands` schedules it on **staging only**.
Production jg — the environment actually writing one `Notification` + one `NotificationRecipient`
per chat message since NOTIF-14 (`37ea0a7`), invisible by design via `feed_visible=False` — keeps
growing exactly as today. This is precisely the WO's own named risk ("if the scheduled command is
registered but never actually fires, nothing fails visibly"), and the WO's Expected Outcome ("row
growth is bounded") would be false for prod while appearing done.

Granting `main-prod` the role is a change to prod-affecting deploy config in a **platform repo**,
which this WO explicitly forbids ("If it needs platform-side work, do NOT build that here — report
it"). Hence: reported, not built.

### Second-order finding — larger than the janitor, outside this WO
`send_todo_digests` (jg's only other scheduled command, and the live todo digest that NOTIF-8/9/10
cut over to) runs through this **same** role-gated mechanism. On the evidence above it therefore
also runs on staging only, i.e. jg's production todo digest may not be running at all. This is
pre-existing and unrelated to retention — worth its own investigation, not a fold-in here.

### Scope B answer (cockpit) — mechanism is fine, no platform work needed
`resolve_app_inventory.py` is fully generic: `register_scheduled_commands()` (`:188-205`) runs for
every app's server and, when distinct, its staging server, gated only on a non-empty
`infra.scheduled_commands`; a missing `infra:` block simply yields `[]` (`:131-133`). cockpit is in
the inventory with `production: main-prod` / `staging: staging`. So **adding an `infra:` block to
cockpit's `project.yaml` is mechanically sufficient** — it inherits the same staging-only ceiling.

### What a re-authored WO needs to decide
- Whether to grant `main-prod` (and the other prod boxes) the `scheduled_commands` role — a
  webapp-management WO, prod-affecting, approval-gated.
- Whether the first production run stays dry-run-gated once the role exists (scope C should survive
  re-authoring; the unpruned backlog argument is unchanged and still correct).
- Whether staging-only scheduling is worth landing on its own in the meantime.

*(Scope C was not exercised: no `--dry-run` was run, since the WO was paused before implementation.)*

---

**EXECUTION DIRECTIVE.** Implement through `codex exec` in the background — invoked **directly via
Bash** (never the `debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check`
and `--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from this
file. Fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

Feature-level WO (Expertenchat envelope). Verified against dcm 2.35.0, jg `develop` @ `5148677`,
cockpit `develop`, on 2026-07-30.

## TIER
Tier 2 — this schedules a **recurring bulk DELETE against production data** and touches deployment
config. Independent `reviewer` mandatory. **The first non-dry run in production requires explicit
operator confirmation** (see scope C); do not let a scheduler perform it unattended before the
dry-run numbers have been seen.

## WHY THIS EXISTS — a live, growing problem
`prune_notifications` shipped in NOTIF-4 (dcm 2.27.0) and is **scheduled in no repository at all**.
Verified 2026-07-30 across jg-ferien, cockpit, hram, spesix and survey_app: none lists it under
`infra.scheduled_commands`. The retention janitor has never run anywhere.

Two consumers are actively accumulating:
- **jg-ferien (new, NOTIF-14 `37ea0a7`)** — every chat message now writes one `Notification` plus one
  `NotificationRecipient` row. These rows are **invisible by design** (`feed_visible=False` from
  NOTIF-19), so nothing surfaces the growth and no user will ever report it. On an active chat this is
  the fastest-growing table in the app.
- **cockpit (since NOTIF-7, 2026-07-27)** — canonical notifications live in production, unpruned.

## GOAL
Make the retention janitor actually run wherever canonical notifications are live, without a surprise
first deletion.

## EXPECTED OUTCOME
- `prune_notifications` runs on a schedule in jg-ferien and (if the mechanism supports it) cockpit.
- The first execution in each environment is a **reviewed dry run**, not a blind delete.
- Row growth is bounded by `NOTIFICATIONS_RETENTION_DAYS` (default 90).

## CONTEXT PACKAGE — verified current state
- **The command** — `django_core_micha/notifications/management/commands/prune_notifications.py`:
  deletes `Notification.objects.filter(Q(expires_at__lt=now) | Q(created_at__lt=cutoff))` where
  `cutoff = now - NOTIFICATIONS_RETENTION_DAYS` (default 90, `settings`-overridable). It supports
  **`--dry-run`**, which prints the count and returns without deleting. No category or per-type filter.
  The delete cascades to `NotificationRecipient` and `NotificationDelivery`.
- **jg's scheduling mechanism** — `project.yaml` → `infra.scheduled_commands`, currently a one-item
  list containing `send_todo_digests`. Adding the janitor is a one-line change **there**, not in cron
  files or compose.
- **cockpit** — `project.yaml` has **no `infra:` block at all**, and cockpit has no `WORK_ORDERS.md`.
  Whether the platform's scheduled-command mechanism is wired for cockpit is **unverified** and is part
  of this WO's investigation, not an assumption.

## SCOPE

**A. jg-ferien — schedule it.** Add `prune_notifications` to `project.yaml` →
`infra.scheduled_commands`. Keep the existing `send_todo_digests` entry untouched.

**B. cockpit — determine whether the same mechanism applies.** cockpit has no `infra:` block. Check
how `webapp-management` consumes `infra.scheduled_commands` and whether adding the block to cockpit is
sufficient. If it is, add it the same way. **If it needs platform-side work, do NOT build that here** —
report it and leave cockpit out; it becomes its own WO.

**C. Dry run before any real deletion — this is the gate, not a nicety.** In each environment, run
`prune_notifications --dry-run` first and record the count. Because the janitor has never run, the
first real execution deletes the entire backlog older than 90 days in **one** `QuerySet.delete()`,
cascading across three tables. If a dry-run count is large enough that a single transaction is a
concern, **stop and report the number** rather than running it — batching would be a dcm change and is
out of scope here.

## DO NOT TOUCH
- `prune_notifications` itself, `NOTIFICATIONS_RETENTION_DAYS`, or anything in dcm. This WO only
  schedules an existing command.
- **Per-type / per-category retention** — chat notifications arguably deserve days, not 90. That needs
  `expires_at` exposed on `notify()`, which the API does **not** currently support (verified: the
  signature ends at `transient`). Registered separately as **NOTIF-21**. Out of scope here.
- Any `notify()` call site, notification type registration, or `feed_visible` setting.
- hram / spesix / survey_app — they have no canonical notification producers yet; scheduling the
  janitor there is pointless until they do (NOTIF-16/17).
- Schema, migrations, dependencies.

## RISKS
- **The first run is the dangerous one.** A never-pruned table means the initial delete is unbounded in
  size and cascades. Scope C exists solely for this.
- **Silent misconfiguration:** if the scheduled command is registered but never actually fires, nothing
  fails visibly — the table simply keeps growing exactly as today. Verify it ran, do not just verify it
  was configured.
- **Deleting more than intended:** the filter is global — `expires_at < now` OR older than 90 days,
  across every category including todo-channel overlay rows. Confirm on staging that the dry-run count
  matches expectations before running for real.
- Deployment config is adjacent to CI/CD; treat the change as approval-relevant per AGENTS.md.

## REQUIRED TESTS / ACCEPTANCE
This is configuration plus an operational verification, not new application logic:
- `prune_notifications --dry-run` executed in jg staging, count recorded in the WO/register note;
- after the first scheduled execution, evidence that it **actually ran** (log/output), plus a
  before/after row count for `Notification`;
- `send_todo_digests` still scheduled and still running — the existing entry must not regress;
- cockpit: either the same evidence, or a written finding that the mechanism is not available there.

## TARGET REPO / WORKING DIRECTORY
- Primary: `C:\Users\biglmi\Documents\webapps\jg-ferien` (app repo, commit to `develop`)
- Secondary (scope B): `C:\Users\biglmi\Documents\webapps\cockpit` (app repo, commit to `develop`)
- Never the workspace root. No feature branches.

## PROGRESS CONTRACT
Emit a `PLAN: <step1> | <step2> | …` line up front, then a single-line
`PROGRESS: [<n>/<total>] <present-tense action>` **before every relevant action** (file opened, file
edited, command/test run) and `PROGRESS: [<n>/<total>] done` on step completion, spaced so no gap
exceeds ~2 min. stdout unbuffered. Exactly one final `RESULT: DONE|BLOCKED <reason>`.

## MINI-HANDOVER (paste into a fresh Orchestrator session)
```
Orchestrator: implement django-core-micha/work-orders/NOTIF-20.md — the edits land in jg-ferien
(develop) and possibly cockpit (develop). git pull first, read the WO, mind the dry-run gate in
scope C, then follow orchestrate-codex (Codex-first, own independent review, commit on green).
```
