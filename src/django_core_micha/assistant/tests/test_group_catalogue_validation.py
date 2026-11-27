import copy

import pytest

from django_core_micha.assistant.group_grants import catalogue_revision, validate_catalogue


def sample():
    return [{"id": "g", "label": "Group", "kind": "read", "members": [
        {"id": "m", "label": "Member", "available": True, "tools": ["read"], "selectors": ["safe"]}]}]


def test_revision_ignores_labels_but_not_eligibility_or_tools():
    groups = sample()
    original = catalogue_revision(validate_catalogue(groups))
    groups[0]["label"] = "Planung"
    groups[0]["members"][0]["label"] = "Mitglied"
    assert catalogue_revision(validate_catalogue(groups)) == original
    groups[0]["members"][0]["available"] = False
    groups[0]["members"][0]["unavailable_reason"] = "Denied"
    assert catalogue_revision(validate_catalogue(groups)) != original
    changed = sample()
    changed[0]["members"][0]["tools"] = ["different"]
    assert catalogue_revision(validate_catalogue(changed)) != original


def test_invalid_catalogues_are_rejected():
    duplicate_member = sample()
    duplicate_member[0]["members"].append(copy.copy(duplicate_member[0]["members"][0]))
    duplicate_group = sample() + sample()
    missing_reason = sample()
    missing_reason[0]["members"][0]["available"] = False
    bad_kind = sample()
    bad_kind[0]["kind"] = "other"
    for groups in (duplicate_member, duplicate_group, missing_reason, bad_kind, "not a list"):
        with pytest.raises(ValueError, match="invalid_group_catalogue"):
            validate_catalogue(groups)
