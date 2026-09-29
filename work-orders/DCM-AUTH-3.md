# DCM-AUTH-3 — The DCM-AUTH-2 gate also hits a non-admin's writes on OTHER users

# A. Envelope — authored by the Expertenchat

## Goal & expected outcome

- Goal: the self-edit restriction from `DCM-AUTH-2` applies to exactly what it was written for, a
  non-admin writing **their own** record through the generic routes. It must not touch writes on
  other users that a consuming app deliberately lets a non-admin make.
- Expected outcome:
  - `PATCH`/`PUT /api/users/<id>/` by a non-admin: the allowlist and the forced partial semantics
    apply **only when `<id>` is the requesting user**. For any other user that the consumer's
    `get_queryset()` exposes, behaviour is exactly as before 2.44.1.
  - `DELETE /api/users/<id>/` by a non-admin: refused (403) **only when `<id>` is the requesting
    user**. For another exposed user, behaviour is exactly as before 2.44.1.
  - All `DCM-AUTH-2` guarantees for the self-target stay, including the pre-existing 404 on ids
    outside the queryset.
  - Released as a patch (2.44.2).

## Context — measured 2026-09-29 against `main` at `f4c6a9e` (2.44.1)

- `src/django_core_micha/auth/views.py` `BaseUserViewSet.update()` (~l.324) and `destroy()`
  (~l.349), both added in `c9a4516`:
  ```python
  self.get_object()
  if not can_view_users_admin(request.user, request=request):
      self._enforce_safe_profile_fields(request)
      kwargs["partial"] = True
  ```
  and in `destroy()` `raise PermissionDenied("Cannot delete your own account through this endpoint.")`.
  Neither compares the resolved object with `request.user`.
- The code comment states the assumption the gate relies on: "a non-admin's queryset only ever
  contains their own row". That holds for the **base** `get_queryset()`, not for consumers that
  override it.
- **jg-ferien breaks the assumption on purpose.** Its `UserViewSet(BaseUserViewSet)`
  (`jg-ferien/backend/users/views.py`, `get_queryset` ~l.189) returns, quoted in shape:
  ```python
  if is_admin_user(user): return all users
  if has_organization_admin_role(user): return all users        # S93: org admins see all
  if user has active department memberships with role "team":
      return users in those departments / organizations
  return queryset                                                # base: own row
  ```
  Org admins and department-team members in jg carry the **global** role `none` (measured locally
  2026-09-29 on accounts created through the app's own flows: `has_invite_admin_rights` False for
  both). So on 2.44.1 a jg org admin editing a user of their organization is cut down to the self-edit
  allowlist, and deleting one gets 403 with the message "Cannot delete your own account" for someone
  else's account. jg's `JG-SEC-2` (landed on jg `develop` as `0972afe`) deliberately grants org
  admins these writes on users of their own organizations.
- jg's local tests mount this repo's checkout, so they already run against 2.44.1. jg's test
  `backend/users/test_user_viewset_object_permissions.py` (~l.70) had to set `profile.role = "admin"`
  on its org admin to get past this gate. That workaround is the symptom.
- No consumer runs 2.44.1 today: every app pins `django-core-micha==` exactly, all on 2.44.0 or older.
- Other overrides checked: `reimbursements` narrows a non-admin to their own row (unaffected);
  `innoservice` does not widen the queryset (unaffected); the other consumers do not override
  `get_queryset`.

## Scope + non-goals

- In scope:
  - Gate both checks on "the resolved object is the requesting user" (compare primary keys of the
    object `get_object()` returns and `request.user`), keeping the object-first ordering.
  - Correct the code comments so they no longer claim the queryset invariant.
  - CHANGELOG entry and the patch release 2.44.2.
- Explicit non-goals / do-not-touch:
  - `current()` unchanged; admin behaviour unchanged; the allowlist contents unchanged.
  - Whether an **admin** may delete their own account through the generic route is a separate item
    (`DCM-AUTH-4`), not this WO.
  - No consumer pin bumps.

## Tier · gates

- **Tier 3** — auth inside shared-core.
- Precondition: none.

## Risks

- A test that only uses the base `get_queryset()` cannot see this defect, because there a non-admin
  never reaches another user's row. The new tests need a viewset whose `get_queryset()` exposes other
  users to a non-admin, the shape jg uses.
- The comparison must use the object the viewset resolved, not the raw URL kwarg: a lookup field
  other than `pk` would otherwise compare the wrong thing.

## Required tests to WRITE (you write them and run YOUR OWN new ones; the orchestrator's run is the gate)

In `tests/test_self_edit_delete_restriction.py`, with an additional test viewset whose
`get_queryset()` returns all users to a non-admin:

1. Non-admin `PATCH` of **another** user with a field outside the allowlist (e.g. `is_active`) → not
   rejected by this gate (behaves as on 2.44.0).
2. Non-admin `DELETE` of **another** user → not refused by this gate (behaves as on 2.44.0).
3. Same viewset, **self**-target: `PATCH is_active` → 400; allowlisted field → 200; `PUT` partial;
   `DELETE` → 403.
4. The existing `DCM-AUTH-2` tests stay green unchanged, including the 404 cases.
5. **Mutation proof:** drop the self comparison → tests 1 and 2 fail.

---

# B. Implementation map — filled by the Orchestrator — ADDRESSED TO THE IMPLEMENTER

## Target repo working directory (absolute)

`C:\Users\biglmi\Documents\webapps\django-core-micha` (branch `main`).

## Context package

### File to change: `src/django_core_micha/auth/views.py`

`BaseUserViewSet.update()` (currently lines 324-347) and `.destroy()` (currently lines 349-361,
immediately below `update()`) — both added by `DCM-AUTH-2` (commit `c9a4516`). Current code:

```python
    def update(self, request, *args, **kwargs):
        # S-DCM-AUTH-2: get_queryset() already scopes a non-admin to their own
        # row, so the generic update/partial_update route on this row is a
        # second, unguarded path to the same account `current()` protects.
        # Without this, a non-admin can PATCH e.g. `is_active` on themselves.
        # Mirror `current()`'s allowlist rather than refusing outright: at
        # least one consumer (jg-ferien) PATCHes a participant's own
        # first_name/last_name through this generic route, and those fields
        # are already in the allowlist.
        #
        # get_object() first, before the admin check: a non-admin's queryset
        # only ever contains their own row, so this 404s exactly as before
        # for any other id and only reaches the new logic for their own.
        # Without this ordering, the allowlist check fired unconditionally
        # and turned a 404 (foreign/nonexistent id) into a 400.
        self.get_object()
        if not can_view_users_admin(request.user, request=request):
            self._enforce_safe_profile_fields(request)
            # A non-admin PUT would otherwise require every serializer field
            # and fully replace the row. Force partial semantics so it
            # behaves like `current()`: only the (already allowlisted)
            # submitted fields change, nothing is reset to a default.
            kwargs["partial"] = True
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        # S-DCM-AUTH-2: same generic-route exposure as `update()` above, but
        # for DELETE there is no safe subset — account deletion is a separate
        # explicit flow, not a side effect of this viewset (no consumer in
        # the estate relies on generic self-delete; survey in DCM-AUTH-2).
        # get_object() first for the same reason as in update(): preserve the
        # pre-existing 404 for a non-admin targeting an id that isn't theirs.
        self.get_object()
        if not can_view_users_admin(request.user, request=request):
            raise PermissionDenied(
                "Cannot delete your own account through this endpoint."
            )
        return super().destroy(request, *args, **kwargs)
```

**The bug:** neither method compares the object `get_object()` resolved with `request.user`. The
comment's invariant ("a non-admin's queryset only ever contains their own row") is only true for
`BaseUserViewSet.get_queryset()` itself — a consumer that overrides `get_queryset()` to expose other
rows to a non-admin (jg-ferien's `UserViewSet`, see Part A Context) breaks it, and this gate then
misfires on writes to those other rows.

**The fix:** add a self-target check — `get_object()`'s resolved instance's `pk` equals
`request.user.pk` — as a THIRD condition alongside the existing `can_view_users_admin` check. Only
when BOTH "not admin" AND "this is my own row" hold does the allowlist/partial-forcing (`update`) or
the refusal (`destroy`) apply. When the resolved object is some OTHER row (non-admin, but the
consumer's queryset let them reach it), skip straight to `super().update()`/`super().destroy()` —
exactly as on 2.44.0, before `DCM-AUTH-2`.

Suggested shape (adapt names/comments as needed, keep the `get_object()`-first ordering from
`DCM-AUTH-2` — that part is correct and must stay, it is what keeps the pre-existing 404 for ids
outside the queryset):

```python
    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        is_self_edit = instance.pk == request.user.pk
        if is_self_edit and not can_view_users_admin(request.user, request=request):
            self._enforce_safe_profile_fields(request)
            kwargs["partial"] = True
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        is_self_edit = instance.pk == request.user.pk
        if is_self_edit and not can_view_users_admin(request.user, request=request):
            raise PermissionDenied(
                "Cannot delete your own account through this endpoint."
            )
        return super().destroy(request, *args, **kwargs)
```

Rewrite both methods' comments to state the CORRECT invariant (the gate applies only when the
resolved object is the requester's own row, checked explicitly — not "the queryset only ever
contains their own row", which is false for an overridden `get_queryset()`). Do not just delete the
old comments' reasoning about WHY the allowlist/partial/refusal exist (still true) — only the false
"queryset invariant" claim needs correcting.

### File to change: `tests/test_self_edit_delete_restriction.py`

Existing fixtures/helpers to reuse as-is: `_MinimalUserSerializer`, `_authed()`,
`_security_level_settings` (autouse), `non_admin`, `other_non_admin`, `admin`.

Add a SECOND test viewset whose `get_queryset()` exposes ALL users to a non-admin (the shape the
Risks section calls for — the existing `_UserViewSet`/`_view` use the base `get_queryset()`, which
can never reach this defect since a non-admin never sees another row through it):

```python
class _WideningUserViewSet(BaseUserViewSet):
    serializer_class = _MinimalUserSerializer

    def get_queryset(self):
        # Simulates a consumer like jg-ferien: a non-admin (by
        # can_view_users_admin's definition) can still see every user here.
        return get_user_model().objects.all()


_wide_view = _WideningUserViewSet.as_view(
    {"patch": "partial_update", "put": "update", "delete": "destroy"}
)
```

Add tests (append near the end of the file) covering Required tests 1-3 from Part A. Test 4 (existing
`DCM-AUTH-2` tests stay green) is satisfied by not touching the existing tests. For test 5 (mutation
proof): after writing the fix, temporarily revert ONLY the self-comparison (e.g. hardcode
`is_self_edit = True`) in your own local checkout, re-run tests 1 and 2, confirm they fail, then
restore the real fix — do NOT leave the reverted version committed. Report the mutation check's
pass/fail in your `RESULT:` line; do not rely on this alone, the Orchestrator will also do a targeted
review-time check.

```python
@pytest.mark.django_db
def test_non_admin_widened_queryset_patch_on_other_user_is_unaffected(non_admin, other_non_admin):
    # jg-ferien shape: get_queryset() lets a non-admin reach another user's
    # row. The DCM-AUTH-2 gate must not misfire here -- is_active on someone
    # else stays whatever the consumer's own serializer/permissions allow
    # (this minimal serializer has no extra guard, so it succeeds, same as
    # on 2.44.0 before DCM-AUTH-2 existed).
    request = _authed(
        "patch", f"/users/{other_non_admin.pk}/", non_admin, {"is_active": False}
    )
    response = _wide_view(request, pk=other_non_admin.pk)

    assert response.status_code == 200
    other_non_admin.refresh_from_db()
    assert other_non_admin.is_active is False


@pytest.mark.django_db
def test_non_admin_widened_queryset_delete_on_other_user_is_unaffected(non_admin, other_non_admin):
    request = _authed("delete", f"/users/{other_non_admin.pk}/", non_admin)
    response = _wide_view(request, pk=other_non_admin.pk)

    assert response.status_code == 204
    assert not get_user_model().objects.filter(pk=other_non_admin.pk).exists()


@pytest.mark.django_db
def test_non_admin_widened_queryset_self_target_still_gated(non_admin):
    # Same widened viewset, but the target IS the requester -- DCM-AUTH-2's
    # protections must still apply exactly as under the base queryset.
    request = _authed("patch", f"/users/{non_admin.pk}/", non_admin, {"is_active": False})
    response = _wide_view(request, pk=non_admin.pk)
    assert response.status_code == 400

    request = _authed("patch", f"/users/{non_admin.pk}/", non_admin, {"first_name": "Wide Self"})
    response = _wide_view(request, pk=non_admin.pk)
    assert response.status_code == 200
    non_admin.refresh_from_db()
    assert non_admin.first_name == "Wide Self"

    request = _authed("put", f"/users/{non_admin.pk}/", non_admin, {"first_name": "Wide Put"})
    response = _wide_view(request, pk=non_admin.pk)
    assert response.status_code == 200

    request = _authed("delete", f"/users/{non_admin.pk}/", non_admin)
    response = _wide_view(request, pk=non_admin.pk)
    assert response.status_code == 403
    assert get_user_model().objects.filter(pk=non_admin.pk).exists()
```

Adapt these to fit your exact fix; the point is coverage of the three Required-tests scenarios, not
these exact assertions.

## Preamble — a REQUIRED block IN this file, not something appended at invocation

> The text above is the COMPLETE spec — the committed WO file's content, not a plan to refine; there
> is no separate plan file. Read the nearest `AGENTS.md`, the relevant `.codex/skills/<role>/SKILL.md`, and the
> app `MEMORY.md` ONLY for conventions. Stay in scope; do not touch auth/permissions/deps/schema/CI
> unless the spec says so; do not update `MEMORY.md`. **Do NOT edit `WORK_ORDERS.md` — the register
> row and the review verdicts are the orchestrator's alone.** **Your tools are for editing source
> and test files and for running the tests you wrote — nothing else.** Do NOT install dependencies,
> touch a lockfile, run a package manager, or tidy up stray files; if something in the repo state
> blocks you, stop and report it as `RESULT: BLOCKED <reason>` instead of fixing it. Do NOT
> `git add`/`commit`/`push` — leave every
> change uncommitted in the working tree for the orchestrator's independent review. WRITE the tests
> the `Required tests` section calls for AND **RUN the tests you just wrote** to confirm they execute
> and pass — that is the ONLY test run you do (NOT the app's affected/full suite, NOT any review).
> The orchestrator re-runs the authoritative set + does the independent review after you finish —
> those are the gate; your own run does not count as the gate.
>
> Narrate continuously: a `PLAN: <step1> | <step2> | …` line up front, then a single-line
> `PROGRESS: [<n>/<total>] <present-tense action>` before every relevant action (and `… done` on
> completion), spaced so no gap exceeds ~2 min, stdout unbuffered, plus exactly one final
> `RESULT: DONE|BLOCKED <reason>`.

---

# C. Orchestrator only — NOT ADDRESSED TO THE IMPLEMENTER

> **If you are the implementer reading this work order as your own specification: STOP at this line.
> Everything below describes what the Orchestrator does AFTER you finish. You do none of it — no
> reviewers, no verification run, no register edit, no commit.**

## Execution record

Dispatched to `codex exec`/`gpt-5.6-luna` (per `.claude/models.local.json`; `.claude/codex-status.md`:
2026-09-29 `available`, no probe needed) with the Part B context package above. One dispatch, no
findings to fix afterward — see below.

**Implementer's own run:** applied exactly the mapped fix (`instance = self.get_object(); is_self_edit
= instance.pk == request.user.pk`, gating both `update()`/`destroy()` on `is_self_edit and not
can_view_users_admin(...)`), added the three widened-queryset tests, and — beyond its preamble's
source/test-only scope — also bumped `CHANGELOG.md`/`pyproject.toml` to 2.44.2. Content reviewed and
kept (see below); the process deviation is noted, not repeated as a defect. It hit and correctly
diagnosed a real environment pitfall mid-run: its first focused test run silently imported the
globally-installed `django-core-micha` package instead of this checkout (no `PYTHONPATH=src`),
producing a misleading self-target failure; it identified this itself, re-ran with the checkout on
`PYTHONPATH`, and got 3/3 green. It then ran the required mutation proof itself (hardcoded
`is_self_edit = True`, confirmed both widened-other-user tests fail, restored the real comparison,
reconfirmed 3/3 green) before reporting `RESULT: DONE`.

**Orchestrator's own verification (independent of the implementer's claims):**
- Read the full diff against the Part B map — matches exactly, no scope creep in the fix itself.
- Re-ran `PYTHONUTF8=1 PYTHONPATH=src pytest tests/test_self_edit_delete_restriction.py` myself: 13/13
  passed, confirmed against the local checkout (`python -c "import django_core_micha; print(...)"` →
  the repo's own `src/`, not site-packages).
- Independently repeated the mutation check myself: hardcoded both `is_self_edit = True` sites,
  re-ran the two widened-other-user tests → both failed (403/400 instead of 204/200) as expected;
  restored the real comparison from a backup, reconfirmed 13/13 green and a clean `git diff`.
- Ran the affected-areas set: `pytest tests/ -k "auth or permission or invite or reset or throttle or
  role or user"` → 113 passed.

**Review — Tier 3, all 4 `reviewer` lenses + `sec_reviewer`** (`codex`/`gpt-5.6-luna`, concurrent, diff
pasted inline, same as DCM-AUTH-2's pattern): **0 real findings across all 5 lenses** — clean on the
first dispatch, no fix round needed. `envelope`: PASS, matches Part A exactly, version/changelog
content correct. `regression`: PASS — confirmed the "other exposed user" path now runs fully
unmodified `super().update()`/`super().destroy()`, `partial=True` stays scoped to the true self-edit
path, all DCM-AUTH-2 guarantees intact. `duplication`: PASS — checked `SHARED_CAPABILITIES.md`,
`ws_permissions.IsObjectOwnerWs` (Channels-only, not reusable here), ui-core-micha's UI-level
`canDeleteUser`/`canEditUser` hooks (not a backend guard) — no existing ownership predicate to reuse.
`tests`: PASS — the two "other user unaffected" tests fail under a DCM-AUTH-2-only revert (real pins);
the third ("self-target still gated") is a regression guard for the pre-existing protection under a
widened queryset, not a pin on the new comparison itself — expected, not a gap. `sec_reviewer`: PASS
— `instance.pk == request.user.pk` is robust under Django's standard PK identity regardless of a
consumer's `lookup_field`/`get_queryset()`; no new enumeration/timing signal beyond the pre-existing
404 boundary; PUT full-replace semantics for non-self rows are untouched.

Two non-blocking process/test-strength notes, not acted on: (1) the implementer bumping
CHANGELOG/version before review is a preamble deviation, content verified correct so kept as-is
rather than redone; (2) the widened-queryset PUT assertion checks status only, not field
preservation — the existing DCM-AUTH-2 PUT test already covers field preservation, so the required
behaviour is covered elsewhere.

**Release:** django-core-micha **2.44.2** (patch — narrows an over-broad restriction back to spec, no
new capability), `CHANGELOG.md` entry as written by the implementer (verified correct), `publish.yml`
triggers on this push to `main`.

**Consumer pin bumps:** none, per Part A non-goals — no consumer runs 2.44.1 today (all exact-pinned
at 2.44.0 or older), so this is not urgent; jg-ferien's eventual bump (to pick up both DCM-AUTH-2's
intended protection and this correction) is covered by the same DCM-AUTH-2 follow-up already tracked
in that WO's register row, not a new item here.
