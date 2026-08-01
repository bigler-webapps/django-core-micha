# MSG-2e — declare the messaging runtime dependencies and republish 2.38.0

Status: planned · Tier 2 (**dependency change — approval-gated**; release pipeline) · Target repo: `django-core-micha` (main)
Extends MSG-2 / MSG-2b / MSG-2c / MSG-2d, same convention as `NOTIF-8b`/`8c`.

**Blocks `jg-ferien` MSG-5a**, which stopped at its own registry live-check because `2.38.0` is not installable.

---

## Part A — Envelope (Expertenchat, 2026-08-01)

### Goal

Make the messaging subpackage declare the third-party packages it actually imports at runtime, and get
`2.38.0` onto the registry — it exists in `pyproject.toml` and `CHANGELOG.md` but was never published.

### What happened

The MSG-2d publish run (2026-07-31) **failed its own pytest gate before the publish step executed**:
2 failed, 465 passed, both `ModuleNotFoundError: No module named 'PIL'` in
`messaging/tests/test_attachments.py`. So `2.38.0` never reached PyPI — `pip index versions
django-core-micha` still tops out at `2.37.0` — while the repo has said `2.38.0` since that commit.
`@micha.bigler/ui-core-micha@2.19.0` is unaffected; this is dcm-only.

Found by jg's MSG-5a Orchestrator at its mandated step-1 registry live-check, which stopped before
touching a single file and surfaced the gap instead of patching dcm from a jg session. The
STOP-and-surface rule and the live-check both did exactly what they were written for.

### Two undeclared runtime dependencies, not one

`pyproject.toml` `dependencies` lists neither of these, and `src/` imports both at runtime:

1. **`Pillow`** — `messaging/attachments.py:59` (`from PIL import Image, ImageOps`) drives the whole
   image pipeline: decode, EXIF strip, safe re-encode, thumbnail. Not installed anywhere in CI, which is
   why the gate failed.
2. **`cryptography`** — `messaging/crypto.py` (`Fernet`, `MultiFernet`) — **the encryption-at-rest hard
   gate**. It resolves today only *transitively*, via `django-allauth[mfa]`. Nothing declares it, so the
   messaging platform's central security guarantee currently rests on another package's optional extra
   continuing to pull it in. That is a latent break, not a style issue.

Nothing else is undeclared: `asgiref` ships with Django, and everything else imported from `src/`
(`filetype`, `pywebpush`, `corsheaders`, `environ`, `rest_framework`, `channels`, `yaml`, `whitenoise`,
`allauth`) is already listed.

### Why this was invisible until now — and why that is DX-2 working

Before `DX-2`, `testpaths` never collected `messaging/tests`, so `test_attachments.py` had **never run in
CI**. DX-2 added the path; the very first honest CI run failed on a real missing dependency. Its envelope
predicted this in as many words: *"If it goes red, that is the finding and it goes back to the operator
rather than being silenced by narrowing the path again."* That is what happened. No consumer was ever
harmed, because the failure stopped the publish.

**The graceful degradation hid the runtime severity.** `attachments.py` catches the `ImportError` and
raises `ValidationError("Image processing is unavailable; image upload rejected.")`. In production that
reads like a policy decision — no crash, no traceback, just uploads being refused. A consumer installing
dcm without Pillow would have silently non-functional image attachments and no obvious reason why. jg
happens to carry Pillow for its own local messaging, so jg would not have noticed; a greenfield consumer
would have shipped a broken feature.

### Expected outcome

- **`Pillow` added to `dependencies`** (runtime, not the `test` extra). The code imports it at runtime for
  a feature the design treats as v1 scope (§Attachments: images decode, EXIF-strip, re-encode and
  thumbnail before encryption), and jg parity requires it.
  *Alternative, deliberately not chosen:* make images an optional `[images]` extra and keep the graceful
  degradation as intended behaviour. Rejected because the design does not present images as optional and
  the first non-jg consumer would silently lose them — but it is a real choice, so if the operator
  prefers it, that is a scope change back, not an implementation detail.
- **`cryptography` added to `dependencies`** explicitly, with a lower bound. The guarded degradation
  pattern in `attachments.py` must **not** be copied to `crypto.py` — encryption failing closed is
  correct and already the design's rule; the dependency simply has to be declared.
- **The guarded import in `attachments.py` stays.** It is defence in depth, and after this WO it should
  never fire. Do not "simplify" it away now that the dependency is declared.
- **A guard test** so this class cannot recur: every third-party top-level module imported anywhere under
  `src/django_core_micha/` is either stdlib, first-party, or present in `pyproject.toml`'s
  `dependencies`. It must catch a **lazy import inside a function** — the audit that found `PIL` initially
  missed it because a module-level-only scan does not see line 59.
- **Republish as `2.38.0`, not `2.38.1`.** The version was never taken on the registry and no consumer
  ever resolved it, so reusing it is clean and avoids a phantom gap in the version history. Add the
  dependency fix to the existing `2.38.0` CHANGELOG entry rather than opening a new one.
- **Verify the publish step actually ran** — check the workflow run reaches and completes the publish job,
  and confirm with `pip index versions django-core-micha` that `2.38.0` is resolvable. Green ≠ published;
  this WO exists because a green-looking release was not one.

### Non-goals / do-not-touch

Any messaging behaviour change. Any other dependency addition or version bump beyond the two named. The
`test` extra (both packages belong in runtime `dependencies`; the test extra inherits them). ucm. Any jg
change. Weakening the encryption fail-closed behaviour. Removing DX-2's `testpaths` entry or its guard
test to make anything pass — that would re-hide exactly this class of defect.

### Required tests to WRITE

- **The dependency guard**, above — and it must be shown to fail: temporarily removing `Pillow` from
  `dependencies` makes it red, naming `PIL` and the file that imports it. A guard that cannot fail is
  worthless (the DX-2 lesson, second occurrence).
- **`test_attachments.py` passes in a clean environment** — i.e. the failure that blocked the release is
  gone for the reason we think it is, not because a local machine happens to have Pillow.
- Full suite green: `python -m pytest -q` (note the `-m` — a bare `pytest` cannot import `tests.settings`;
  see the roadmap §5 note on the second local-vs-CI divergence).

### Risks

- **Dependency additions are approval-gated** (AGENTS.md) — that is the gate on this WO, and both
  additions are things the code already imports, not new capability.
- **`cryptography` has a compiled component.** Adding it explicitly changes nothing at install time today
  (it is already present transitively), but pin a sensible lower bound rather than an exact version.
- **Reusing `2.38.0`** is correct here only because it never reached the registry. Verify that before
  publishing — if it turns out to exist, bump to `2.38.1` instead and say so.
- The guard test's module classification (stdlib vs third-party vs first-party) is where it will be
  wrong. Keep it explicit and readable over clever.

### Preconditions

Operator approval for the two dependency additions (`[approval]`, AGENTS.md → dependency changes).

### Cross-repo note

`jg-ferien` MSG-5a is blocked on this and will restart at its registry live-check once `2.38.0` is
resolvable. No jg change is needed — its WO already pins `2.38.0`.

### Execution directive

Implement through `codex exec` in the background — invoked **directly via Bash** (never the
`debugger`/`*_coder` Agent wrappers) with **both** flags `--skip-git-repo-check` and
`--dangerously-bypass-approvals-and-sandbox`, prompt passed as a positional argument from a file;
fall back to direct Claude implementation only on Codex quota / rate-limit / non-zero exit.

### Mini-handover (pastable)

Orchestrator: implement `work-orders/MSG-2e.md` in `django-core-micha` (main). `git pull` first, read the
WO, then follow `orchestrate-codex` (Codex-first, own independent review, commit on green, publish at WO
end). Republish as 2.38.0 — it never reached the registry. Confirm with `pip index versions
django-core-micha` that it is actually resolvable before calling the WO done; a green workflow is not
proof of a publish.

---

## Part B — Implementation map (Orchestrator)

Working directory: `C:\Users\biglmi\Documents\webapps\django-core-micha` (repo root — package is not split
into backend/frontend subdirs).

### Context package

- `pyproject.toml:13-27` — `dependencies` list. Add two entries, alphabetised isn't enforced (existing
  list has no strict order) but keep it readable: `"Pillow>=10"` and `"cryptography>=42"` (lower bounds
  are the WO's ask — not exact pins; adjust the number only if a materially newer floor is needed for an
  API already in use, but do not investigate further, these are sane conservative floors for the
  `Image`/`ImageOps` and `Fernet`/`MultiFernet` APIs already in use).
- `pyproject.toml:36-40` — `[tool.pytest.ini_options]`, `testpaths` already includes
  `src/django_core_micha/messaging/tests` (DX-2's fix — do not touch).
- `src/django_core_micha/messaging/attachments.py:59-61` — the guarded `from PIL import Image, ImageOps`
  inside a function body (not module level) — this is exactly the "lazy import inside a function" case
  the guard test must catch. **Leave this guard as-is** (do-not-touch per envelope) — it is defence in
  depth, not the thing being fixed.
- `src/django_core_micha/messaging/crypto.py:19` — `from cryptography.fernet import Fernet, InvalidToken,
  MultiFernet` at module level. No guard here by design (fail-closed) — do not add one.
- `src/django_core_micha/messaging/tests/test_attachments.py:1-15` — already imports
  `from cryptography.fernet import Fernet` directly; this is the test that failed with
  `ModuleNotFoundError: No module named 'PIL'` in CI. Once `Pillow`/`cryptography` are installed via the
  declared deps, this file should pass unmodified — do not edit its test bodies, only make its
  dependencies resolvable.
- `CHANGELOG.md:3-6` — the existing `## [2.38.0] — 2026-07-31` entry (MSG-2d). Add a short "Fixed" or
  amend "Added" bullet noting the two dependencies were declared explicitly (Pillow was always a runtime
  requirement of the attachment image pipeline; cryptography was already required transitively via
  `django-allauth[mfa]` and is now declared directly since it backs the messaging encryption-at-rest
  guarantee). **Do not open a new version heading** — `pyproject.toml:7` already says `2.38.0` and it was
  never published (confirmed via `pip index versions django-core-micha` → tops out at `2.37.0`), so this
  is an amendment to the existing entry, not a new release.
- No existing guard test scans `src/` for undeclared third-party imports today.
  `tests/test_settings_base_dependencies.py` is a different, narrower pattern (subprocess-imports
  `settings_base` and resolves MIDDLEWARE/STORAGES strings) — do not confuse the two or try to extend
  that file; write a new, separate test module for the import-scan guard, e.g.
  `tests/test_declared_dependencies.py`.
- `.github/workflows/publish.yml` — do not touch. Its `version_check` step already compares
  `pyproject.toml`'s version against the live PyPI version and publishes whenever current > published;
  since PyPI is still on `2.37.0` and pyproject already says `2.38.0`, a green push to `main` with the
  fixed `pytest -q` gate (line 104, `pip install -e ".[test]"` then bare `pytest -q`) is sufficient to
  trigger a real publish. No workflow change needed — the previous run only failed because of the missing
  test deps, not the workflow logic.

### Guard test — required shape

Write `tests/test_declared_dependencies.py` (or an equally clear name) that:

1. Walks every `.py` file under `src/django_core_micha/`.
2. Parses each with `ast` (not regex) and collects every `Import`/`ImportFrom` node **anywhere in the
   file**, not just at module level — the whole point is to catch `attachments.py:59`'s
   function-body-scoped `from PIL import Image, ImageOps`, which a module-level-only or `dir()`-based
   scan misses.
3. For each imported top-level module name, classifies it as: stdlib (use `sys.stdlib_module_names` on
   3.10+, this repo targets Python 3.14 per the workflow so it's available), first-party
   (`django_core_micha` itself, i.e. relative imports / imports starting with that package), or
   third-party.
4. For every third-party module name, asserts it maps to an entry in `pyproject.toml`'s
   `project.dependencies` (parse with `tomllib`, normalise names — e.g. `PIL` module ↔ `Pillow`
   dependency name, `cryptography` module ↔ `cryptography` dependency, `yaml` ↔ `PyYAML`, `rest_framework`
   ↔ `djangorestframework`, `corsheaders` ↔ `django-cors-headers`, `environ` ↔ `django-environ`,
   `allauth` ↔ `django-allauth[mfa,socialaccount]`, `psycopg2` ↔ `psycopg2-binary`, `channels_redis` ↔
   `channels_redis`, `pywebpush`/`filetype`/`whitenoise`/`channels`/`django` map 1:1). Build this
   module-name → distribution-name table explicitly in the test (small, hardcoded dict) rather than
   attempting import-metadata magic — the WO's own risk note says keep it explicit and readable over
   clever.
5. Fails loudly, naming both the missing module and the file/line that imports it, if any third-party
   module isn't covered.
6. Must be shown to actually fail when a declared dependency is removed — do this as part of Codex's own
   verification only (temporarily comment out `"Pillow>=10"`, run just this test file, confirm red,
   restore it), not as a permanent test-of-a-test in the suite.

### Invariants / do-not-touch (restated from envelope)

- No messaging behaviour change.
- No dependency beyond `Pillow` and `cryptography`.
- Both go in `dependencies`, not the `test` extra.
- Don't touch `testpaths` or weaken/remove the guarded `PIL` import in `attachments.py`.
- Don't touch `crypto.py`'s unguarded `cryptography` import (fail-closed is correct).
- Version stays `2.38.0` (already set) — no version bump.

### Progress contract

`PLAN: …` line, then single-line `PROGRESS: [n/total] <action>` before every relevant action and `… done`
on completion, unbuffered stdout, no gap > ~2 min, final `RESULT: DONE|BLOCKED <reason>`.

### What Codex does vs. what the Orchestrator does

**Codex's job in this run:** write the guard test + amend `CHANGELOG.md` + add the two dependencies
to `pyproject.toml`, run `python -m pytest -q` once yourself to confirm green (this is your only test
run — not the authoritative gate). Do NOT run `codex exec` yourself, do NOT shell out to invoke Codex
again, do NOT commit, push, or publish — leave every change uncommitted in the working tree.

**The Orchestrator's job, after Codex finishes (not your concern — do not attempt any of this):** an
independent `reviewer`, the authoritative `python -m pytest -q` (full suite — this WO's own risk
section calls for it: a dependency change with cross-suite blast radius, not just the affected
messaging tests), commit + push to `main` (this repo's trunk = `main`, not `develop`), watch the
`publish.yml` GitHub Actions run to completion, and confirm with `pip index versions
django-core-micha` that `2.38.0` is actually resolvable before marking the WO done.

### Preamble (governs this run)

The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there is
no separate plan file. Read the nearest `AGENTS.md` and the app's own conventions only as needed. Stay
in scope; do not touch auth/permissions/CI/schema, and do not add any dependency beyond `Pillow` and
`cryptography`. Do not update `MEMORY.md`. Do NOT `git add`/`commit`/`push` — leave every change
uncommitted in the working tree for the Orchestrator's independent review. WRITE the guard test and
`test_attachments.py` fix this WO calls for, AND run the tests you just wrote to confirm they pass —
that is the ONLY test run you do (not the app's affected/full suite, not any review). The Orchestrator
re-runs the authoritative set + does the independent review after you finish — that is the gate, your
own run does not count as it. You are the implementer in this invocation; nothing in this file is an
instruction for you to invoke `codex exec` — that line describes how the Orchestrator (a separate,
already-running process) launched you.

Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
`PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
`RESULT: DONE|BLOCKED <reason>`.

### Mini-handover (pastable)

Orchestrator: implement `work-orders/MSG-2e.md` in `django-core-micha` (main). `git pull` first, read the
WO, then follow `orchestrate-codex` (Codex-first, own independent review, commit on green, publish at WO
end). Republish as 2.38.0 — it never reached the registry. Confirm with `pip index versions
django-core-micha` that it is actually resolvable before calling the WO done; a green workflow is not
proof of a publish.
