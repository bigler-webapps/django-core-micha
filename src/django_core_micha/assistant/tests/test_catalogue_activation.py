import copy

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import override_settings

from django_core_micha.assistant.catalogue_activation import activate_catalogue
from django_core_micha.assistant.group_grants import (
    catalogue, catalogue_revision, effective_ids, member_revision, save_selection, validate_catalogue,
)
from django_core_micha.assistant.models import AssistantChat
from django_core_micha.assistant.provider_context import context_token

pytestmark = pytest.mark.django_db


def sample():
    return [{'id': 'g', 'label': 'Group', 'kind': 'read', 'members': [
        {'id': 'm', 'label': 'Member', 'available': True, 'tools': ['read'], 'selectors': ['data']},
    ]}]


def test_reintroduction_without_chat_activity_requires_disable_then_enable():
    groups = sample()
    activate_catalogue(groups, deployment='one', expected_revision=0)
    user = get_user_model().objects.create_user(username='lifecycle')
    chat = AssistantChat.objects.create(owner=user)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT='one'):
        first = catalogue(chat, user)
        baseline = {'g': {'m': member_revision(first[0], first[0]['members'][0])}}
        save_selection(chat, first, ['g'])
        before = context_token(chat.pk, user)
    activate_catalogue([], deployment='two', expected_revision=1)
    activate_catalogue(groups, deployment='three', expected_revision=2)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT='three',
                           DCM_ASSISTANT_GROUP_GRANT_BASELINE=baseline):
        current = catalogue(chat, user)
        assert context_token(chat.pk, user) != before
        assert effective_ids(chat, current) == set()
        baseline_chat = AssistantChat.objects.create(owner=user)
        assert effective_ids(baseline_chat, current) == set()
        save_selection(chat, current, ['g'])
        assert effective_ids(chat, current) == set()
        save_selection(chat, current, [])
        save_selection(chat, current, ['g'])
        assert effective_ids(chat, current) == {'m'}


def test_fencing_noop_labels_and_user_unavailability():
    groups = sample()
    assert activate_catalogue(groups, deployment='one', expected_revision=0) == 1
    assert activate_catalogue(groups, deployment='one', expected_revision=1) == 1
    user = get_user_model().objects.create_user(username='fencing')
    chat = AssistantChat.objects.create(owner=user)
    display = copy.deepcopy(groups)
    display[0]['label'] = 'Translated'
    display[0]['members'][0].update(available=False, unavailable_reason='No access')
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: display,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT='one'):
        projection = catalogue(chat, user)
        revision = catalogue_revision(projection)
        assert effective_ids(chat, projection) == set()
        activate_catalogue(groups, deployment='two', expected_revision=1)
        with pytest.raises(PermissionDenied):
            save_selection(chat, projection, ['g'])
        with pytest.raises(PermissionDenied):
            context_token(chat.pk, user)
    with pytest.raises(ValueError, match='stale_activation'):
        activate_catalogue(groups, deployment='three', expected_revision=1)
    with pytest.raises(ValueError, match='deployment_reused'):
        activate_catalogue(groups, deployment='one', expected_revision=2)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: display,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT='two'):
        assert catalogue_revision(catalogue(chat, user)) != revision


def test_initial_baseline_is_frozen_before_unused_chat_retirement():
    groups = sample()
    validated = validate_catalogue(groups)
    identity = member_revision(validated[0], validated[0]['members'][0])
    baseline = {'g': {'m': identity}}
    user = get_user_model().objects.create_user(username='baseline-cohort')
    existing = AssistantChat.objects.create(owner=user)
    with override_settings(DCM_ASSISTANT_GROUP_GRANT_BASELINE=baseline):
        activate_catalogue(groups, deployment='initial', expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT='initial'):
        assert effective_ids(existing, catalogue(existing, user)) == {'m'}
        new = AssistantChat.objects.create(owner=user)
        assert effective_ids(new, catalogue(new, user)) == {'m'}
        unavailable = copy.deepcopy(groups)
        unavailable[0]['members'][0].update(available=False, unavailable_reason='No access')
        with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: unavailable):
            assert effective_ids(existing, catalogue(existing, user)) == set()
        assert effective_ids(existing, catalogue(existing, user)) == {'m'}
    # Neither chat is used while the member is absent.
    activate_catalogue([], deployment='retired', expected_revision=1)
    with override_settings(DCM_ASSISTANT_GROUP_GRANT_BASELINE=baseline):
        activate_catalogue(groups, deployment='returned', expected_revision=2)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT='returned'):
        assert effective_ids(existing, catalogue(existing, user)) == set()
        assert effective_ids(new, catalogue(new, user)) == set()
        later = AssistantChat.objects.create(owner=user)
        assert effective_ids(later, catalogue(later, user)) == set()


def test_first_activation_rejects_unmatched_baseline_atomically():
    from django_core_micha.assistant.models import AssistantCatalogueState
    with override_settings(DCM_ASSISTANT_GROUP_GRANT_BASELINE={'g': {'m': '0' * 64}}):
        with pytest.raises(ValueError, match='invalid_group_baseline'):
            activate_catalogue(sample(), deployment='bad', expected_revision=0)
    assert AssistantCatalogueState.objects.get(pk=1).revision == 0


def test_unconfigured_and_missing_or_withdrawn_state_fail_closed():
    from django_core_micha.assistant.catalogue_activation import groups_enabled
    from django_core_micha.assistant.models import AssistantCatalogueState

    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=None):
        assert groups_enabled() is False
        activate_catalogue(sample(), deployment='initial', expected_revision=0)
        with pytest.raises(PermissionDenied, match='catalogue_unverified'):
            groups_enabled()
        AssistantCatalogueState.objects.filter(pk=1).delete()
        with pytest.raises(PermissionDenied, match='catalogue_unverified'):
            groups_enabled()
        with pytest.raises(AssistantCatalogueState.DoesNotExist):
            activate_catalogue(sample(), deployment='replacement', expected_revision=0)


def test_unchanged_member_keeps_saved_grant_across_activation():
    groups = sample()
    user = get_user_model().objects.create_user(username='stable-grant')
    chat = AssistantChat.objects.create(owner=user)
    activate_catalogue(groups, deployment='one', expected_revision=0)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT='one'):
        save_selection(chat, catalogue(chat, user), ['g'])
        saved = copy.deepcopy(chat.group_grants)
    activate_catalogue(groups, deployment='two', expected_revision=1)
    with override_settings(DCM_ASSISTANT_GROUP_CATALOGUE=lambda c, u: groups,
                           DCM_ASSISTANT_GROUP_AUTHORITATIVE_CATALOGUE=lambda: groups,
                           DCM_ASSISTANT_GROUP_DEPLOYMENT='two'):
        chat.refresh_from_db()
        assert chat.group_grants == saved
        assert effective_ids(chat, catalogue(chat, user)) == {'m'}
