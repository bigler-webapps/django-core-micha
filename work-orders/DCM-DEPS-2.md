# DCM-DEPS-2 — All open Renovate updates in one change on main, one CI run

Prepared: 2026-10-15. Target repository: `django-core-micha` (`main`).

# A. Envelope — addressed to everyone

## Goal and expected outcome

django-core-micha's nine open Renovate PRs land as one change on `main`, verified by one CI run, followed by a release through the repository's normal publish procedure. The consuming apps pick the release up through their own Renovate PRs later.

## Tier

Tier 3: dependency changes, majors included.

## Operator decisions this Envelope records (2026-10-15)

1. The bundle contains these open Renovate PRs' updates, in one change on `main`:

| PR | Update |
|---|---|
| #28 | python non-major |
| #29 | github-actions |
| #30 | actions/checkout v7 |
| #31 | actions/setup-python v7 |
| #32 | cryptography v50 |
| #33 | django-anymail v15 |
| #34 | openai v3 |
| #35 | pillow v12 |
| #36 | pytest-asyncio v1 (test-only) |

2. A major that needs more than small adaptations (configuration, a renamed import, a test helper) is taken out of the bundle, left as its PR, and reported with the reason. The bundle does not grow into a migration.
3. Verification is one CI run of the repository's own CI on the candidate (for an app: a `ci-test/<ID>` ref with the full job set). A red job is fixed within decision 2 and the run repeated; not more than that.
4. After the push Renovate closes the PRs whose update landed; a PR it rebases instead is an update that did not land and is reported.
5. A runtime dependency whose declared range in the package metadata changes is checked against the apps' current pins before it goes in: if an app's pinned version of that dependency would fall outside the new range, the update is taken out (decision 2), because the release would otherwise force a change in every app.

## Scope

1. Dependency manifests and lockfiles, workflow `uses:` pins, image pins the updates touch.
2. The small adaptations decision 2 allows, with tests where behaviour is touched.
3. Each update's changelog read for breaking changes before it goes in; a breaking change the code meets is adapted (decision 2) or the update is taken out.
4. The version bump and release of `django-core-micha` per its own publish procedure (`publish-from-main`).

## Non-goals

- No new features, no refactoring beyond decision 2.
- No change to the Renovate configuration.
- No pin bump in any consuming app.

## Risks

- A changed runtime dependency (cryptography, anymail, openai, pillow) reaches every app with its next core bump. Mitigation: decision 5, and each app's own CI on its core-bump PR.
- The full dcm suite is the right test set here (shared-core ripple, the named exception in `AGENTS.md` → Test scope).

## Required tests to write

None beyond the adaptations of decision 2. The evidence is the CI run of decision 3.

# B. Implementation map — addressed to the implementer

Not dispatched: the change is manifest floors and workflow pins, which are Orchestrator repo-state work. Decision-5 pin check read the apps' requirements files; the excluded updates are named in the register row.

# C. Orchestrator only — not addressed to the implementer

> **If this WO was dispatched to you as an implementer: STOP here.**

Gate #1 open until recorded in the register row. Part of the PR close-out round of 2026-10-15; stagger the CI run with the other bundles of that round (one heavy run at a time on the shared runner). Full dcm suite justified: shared-core ripple.
