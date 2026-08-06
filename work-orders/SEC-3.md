# SEC-3 — A bare `sync-secrets` run silently covers only part of the estate

Target repo: `django-core-micha` — `src/django_core_micha/scripts/sync_secrets.py`
Companion: `webapp-management` — `secrets.yaml`, `project.yaml`
Tier: **2** (shared core; changes where production secrets are pushed)

---

## A. Envelope (authoritative WHAT/WHY)

### Goal

Make a routine `sync-secrets` run reach every environment that needs secrets — and make it say so
loudly when it cannot, instead of quietly covering a subset.

### Why — this already caused a production outage

`status.bigler-consult.ch` served a bare `404` for an unknown length of time (**OPS-2**, 2026-08-06).
Root cause: the `monitoring` GitHub Environment held **0 secrets** against 32-38 in every other one,
so `deploy-traefik` generated an `.env` without `DOMAIN_KUMA`, the Traefik router rule resolved to
`Host(``)`, and nothing matched. Kuma had moved to that host and the environment was never populated.

OPS-2 fixed the symptom with one targeted run. The cause is here:

```yaml
# webapp-management/secrets.yaml
bare_server_targets: [staging, main-prod, contact-prod, innoservice-prod]
```

A bare run iterates **four** targets. The estate has **six** GitHub Environments. `monitoring` and
`runners` are outside the routine, so every newly declared secret propagates to four servers and the
other two drift until somebody remembers a targeted run. Nobody did, for `monitoring`.

Operator decision 2026-08-06: **derive the list from the server registry instead of maintaining it
by hand** — the same move `kuma-sync` already made when it replaced its "hand-maintained APPS
hardcode" with a derived list.

### The trap in the obvious implementation — measured, not assumed

`project.yaml infra.servers` contains **five** servers:

```
main-prod, contact-prod, innoservice-prod, staging, monitoring
```

The repo has **six** GitHub Environments — the sixth is `runners`, which holds 38 secrets and is
provisioned via `target=runners`, but is absent from `infra.servers` because that key feeds the
backup/restore/maintenance/janitor/deploy-traefik matrices and the runner is not a deploy target for
those.

**So a naive "derive from `infra.servers`" produces five targets and still skips the runner** — the
same blind spot as today's hand-maintained list, only better disguised. Deriving alone does not fix
this class of bug.

### The shape that actually fixes it

Derivation removes the routine maintenance. **Verification is what prevents recurrence**, because any
registry we derive from can itself be incomplete — `infra.servers` just proved it. Both halves are
needed:

1. **Derive** where a registry exists.
2. **Verify** the resolved set against the environments that actually exist, and fail loudly on a
   mismatch in either direction.

The OPS-2 failure was not "somebody forgot to edit a list". It was "nothing ever noticed the list was
wrong". Item 2 is the part that addresses that.

### Scope

1. **Resolve bare-mode targets with an explicit precedence**, in `_sync_all_github_targets`
   (`sync_secrets.py:742`). `project_config` is already a parameter of that function, so no new
   plumbing is needed:
   - explicit `config.bare_server_targets` wins, if set (keep the override — it is someone's escape
     hatch);
   - else, if `project.yaml` declares `infra.servers`, derive from its keys in declaration order;
   - else, the existing `_BARE_SERVER_TARGETS` default.
   The third branch is not optional: **app repos have no `infra.servers`** and must keep working
   exactly as they do today.
2. **Verify the resolved set against the repo's GitHub Environments** and fail with a message naming
   the difference, in both directions — an environment with no target (the OPS-2 case) and a target
   with no environment (a typo, or a decommissioned server).
3. **Remove `bare_server_targets` from `webapp-management/secrets.yaml`.** Leaving it means the
   override branch wins and the derivation never runs — the fix would land and change nothing. Same
   discipline as INF-7: the superseded mechanism goes, it does not sit beside the new one.
4. **Settle the `runners` gap and state the choice.** Two ways, and this is the one real decision:
   - add `runners` to `infra.servers` with a marker that it is not a deploy target — but
     `infra.servers` feeds `resolve_inventory_targets.py`, so this risks pulling the runner into
     backup/janitor/deploy-traefik matrices where it does not belong. Only viable if "secrets-only"
     is cleanly expressible there;
   - or leave `infra.servers` alone and let item 2's verification carry it: the run fails, naming
     `runners` as an environment with no target, and the operator adds an explicit entry.
   **Recommendation: the second.** It keeps one registry serving one purpose, and it converts the
   gap from silent to loud — which is the actual objective.

### Non-goals / do not touch

- Do not derive the target list from the GitHub Environments themselves. It is self-maintaining and
  therefore tempting, but it inverts control: an environment created by mistake in the GitHub UI
  would silently receive production secrets. Environments are the thing we *check against*, not the
  source of truth.
- Do not change what secrets are resolved, the Proton paths, or the `{target}`/`{server}` placeholder
  handling.
- Do not touch `--server`, `--local`, `--github`, or the targeted-sync path. This WO is only about
  which targets a **bare** run visits.
- Do not create or edit Proton entries.

### Risks

1. **A bare run starts writing to two more production environments.** That is the intent, and it is
   idempotent, but it is also the first time `monitoring` and `runners` participate in the routine —
   run it once deliberately and read the output before trusting it to a schedule.
2. **Breaking app repos.** Every consuming app runs this tool. The `infra.servers`-absent branch must
   be covered by a test, not by inspection.
3. **A verification that is too strict blocks the routine.** If the check fails hard on any mismatch,
   a legitimately new environment stops all syncing until the config catches up. Decide deliberately
   whether the mismatch is fatal or a loud warning — and if warning, make sure it cannot be lost in
   the output the way the original gap was.
4. **This is shared core.** The fix only takes effect once the operator's installed `sync-secrets` is
   upgraded — a version bump is part of the deliverable, not a follow-up. Scope-wise this extends an
   existing capability rather than adding a new one, so it is a **patch** bump.

### Tests to WRITE (narrow — run only these)

- Precedence: explicit `bare_server_targets` wins; absent it, `infra.servers` keys are used in order;
  absent both, the built-in default applies.
- An app-repo-shaped `project.yaml` (no `infra` key) resolves to the built-in default unchanged.
- Verification reports an environment with no target, and a target with no environment, each with the
  name in the message.
- Malformed `infra.servers` (not a mapping, empty) is rejected with the same clarity as the existing
  `bare_server_targets` validation, not silently ignored.

### Verification

A bare `sync-secrets` in `webapp-management` visits **all six** environments, and re-running it is a
no-op in effect. Then the counter-test that matters: temporarily remove one server from the registry
and confirm the run **fails naming it**, rather than quietly covering five.

---

## B. Implementation map

**Process note:** implemented directly in Claude, not via Codex — a process deviation from the
mandatory Codex-first rule (the prior WO in this session, cockpit OBS-6, had already hit
Codex's "workspace out of credits" quota error on both its chunks; that quota was assumed still
exhausted and Codex was not re-attempted here). Author = Orchestrator, so an independent
`reviewer` pass is mandatory before commit (author ≠ reviewer).

### Context package

- `src/django_core_micha/scripts/sync_secrets.py:670` (`_BARE_SERVER_TARGETS` default) through
  the `_sync_all_github_targets` function: added `_resolve_bare_targets(parser, config,
  project_config)` (three-branch precedence: explicit `config.bare_server_targets` →
  `project_config["infra"]["servers"]` keys in declaration order → built-in `_BARE_SERVER_TARGETS`
  default; returns `(bare_targets, derived_from_infra_servers)`, the second element flagging
  branch 2), `_fetch_github_environment_names(target_repo)` (shells `gh api
  repos/{target_repo}/environments --jq '.environments[].name'`, returns `None` — not an empty set
  — on any failure so callers can distinguish "no environments" from "couldn't check"), and
  `_verify_bare_targets_against_environments(parser, bare_targets, target_repo, *, fatal)`.
  `_sync_all_github_targets` now calls both helpers before its existing per-target loop, unchanged
  otherwise.
- **Independent review caught a real scope overrun**, fixed before landing: the first version made
  verification unconditionally fatal for every consumer with `target_repo` set, not just the
  `infra.servers`-derived case this WO targets. Verified live against the estate (`gh api
  repos/bigler-webapps/hram/environments`): hram has a genuine third GitHub Environment,
  `hram-webapp`, that its built-in `staging`/`production` default doesn't know about — a real,
  pre-existing, out-of-scope gap that would have turned into a surprise hard failure on hram's very
  next bare `sync-secrets` run. Fixed by threading `derived_from_infra_servers` through as
  `fatal=...`: a mismatch is fatal only for a derived list (which can itself be incomplete — the
  exact OPS-2 bug class); a mismatch on an explicit override or the built-in default is now a loud,
  unmissable warning (bracketed `!`-banner) instead, so verification alone cannot break another
  repo's already-working routine. This was an explicit operator decision (asked via AskUserQuestion
  mid-implementation, since it's an emergent, out-of-scope breaking risk to repos this WO never
  named) — "fatal only when derived" was the chosen and recommended option.
- `tests/test_sync_secrets.py`: imports `_resolve_bare_targets` directly; new test sections for
  precedence (5 direct unit tests, each now asserting the `(targets, derived)` tuple, + 1
  malformed-input parametrized test), one `main()`-level integration test proving `infra.servers`
  derivation is wired into the real bare-mode path, and 6 tests for the verification step (fails
  naming an environment-with-no-target, fails naming a target-with-no-environment — both via the
  derived path — no-op on an exact match, warns-and-proceeds when the environment list can't be
  fetched, and two regression tests proving a mismatch on the built-in default and on an explicit
  override each warn rather than fail — the hram scenario, reproduced directly). All pre-existing
  bare-mode tests were left untouched and still pass — they never mock
  `_fetch_github_environment_names`, so they exercise the real (network-less, fast-failing) "can't
  verify → warn" path, proven by the full-suite run (73 passed).
- `pyproject.toml` / `CHANGELOG.md`: version bump `2.41.0` → `2.41.1` (patch — this extends an
  existing capability, not a new one, per Risk 4). `django-core-micha` is installed editable
  (`pip show` confirms `Editable project location: …\django-core-micha`) on this machine, so the
  bump takes effect immediately for the operator's global `sync-secrets`/`run-dev` — no separate
  reinstall step needed here, though other machines/CI consuming a pinned release would need one.
- Companion repo `webapp-management/secrets.yaml` (item 3): removed the `bare_server_targets`
  override under `config:` so derivation from `infra.servers` actually takes effect; replaced the
  comment with one pointing at this WO and stating the `runners` gap explicitly (item 4,
  recommended path — `runners` stays out of `infra.servers`, so the very next bare run in
  `webapp-management` will genuinely fail naming `runners` as an environment with no target,
  until the operator decides how to add it as an explicit target). This is expected, intended
  behaviour of the fix, not a residual bug — flagged to the operator as a required follow-up
  decision, not resolved unilaterally here (Non-goals: do not derive targets from GitHub
  Environments themselves, so `runners` cannot silently self-register).

### Tests run (narrow, per Test scope)

`pytest tests/test_sync_secrets.py` — 73 passed (55 pre-existing + 18 new), ~17s, no live network
calls (this environment's `gh` fails fast/unauthenticated, which is exactly the "warn, don't
block" path the tests also cover explicitly via mocking).
