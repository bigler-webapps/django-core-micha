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

To be filled by the Orchestrator session on `git pull`, within the envelope above.
