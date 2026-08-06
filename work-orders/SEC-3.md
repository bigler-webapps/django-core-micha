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

*(To be filled by the Orchestrator on `git pull` — context package, target working directory,
progress contract, execution directive, mini-handover. Not authored by the Expertenchat.)*
