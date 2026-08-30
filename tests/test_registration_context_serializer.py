"""Tests for RegistrationContextSerializer's field coverage (DCM-REG-1).

`RegistrationContextSerializer` is a plain `serializers.Serializer` that
`SignupQrCreateSerializer` nests to validate the admin-supplied
`registration_context` when a QR signup token is created via the
`signup-qr` endpoint. DRF silently drops any key not declared as a field --
so every consumer's own context key (today: `event_ref`, `course_ref`,
`group_ref`, `organization_ref`) must be declared here, or a token created
through that endpoint loses it with no error surfaced anywhere.
"""
from __future__ import annotations

from django_core_micha.auth.policy import create_signup_context_token, decode_signup_context_token
from django_core_micha.auth.serializers import RegistrationContextSerializer, SignupQrCreateSerializer


def test_registration_context_serializer_preserves_organization_ref():
    serializer = RegistrationContextSerializer(data={"organization_ref": "10"})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data.get("organization_ref") == "10"


def test_signup_qr_create_serializer_and_token_round_trip_preserve_organization_ref():
    """The failure this WO fixes: the admin-facing signup-qr endpoint nests
    RegistrationContextSerializer inside SignupQrCreateSerializer, and a
    dropped field there means every token created through that HTTP endpoint
    silently loses it -- this exercises that exact nesting, then the signed
    token round-trip on top of it."""
    context = {"organization_ref": "10"}
    serializer = SignupQrCreateSerializer(data={"registration_context": context})
    assert serializer.is_valid(), serializer.errors
    validated_context = serializer.validated_data["registration_context"]
    assert validated_context["organization_ref"] == "10"

    token, _expires_at = create_signup_context_token(registration_context=validated_context)
    assert decode_signup_context_token(token)["registration_context"]["organization_ref"] == "10"


def test_registration_context_serializer_preserves_all_known_ref_keys_together():
    payload = {
        "event_ref": "1",
        "course_ref": "2",
        "group_ref": "3",
        "organization_ref": "4",
        "labels": ["a", "b"],
        "metadata": {"note": "x"},
    }
    serializer = RegistrationContextSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {**payload, "schema_version": "1"}
