"""Deployment-owned, serialized catalogue lifecycle and worker fencing."""
import hashlib
import json
import re

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction

from .group_grants import member_revision, validate_baseline, validate_catalogue
from .hooks import _hook
from .models import AssistantCatalogueDeployment, AssistantCatalogueState


class ActiveCatalogue(list):
    def __init__(self, groups, revision, baseline):
        super().__init__(groups)
        self.activation_revision = revision
        self.baseline = baseline


def identities(groups):
    return {member['id']: member_revision(group, member)
            for group in groups for member in group['members']}


def fingerprint(items):
    return hashlib.sha256(json.dumps(items, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@transaction.atomic
def activate_catalogue(groups, *, deployment, expected_revision):
    """Activate a complete catalogue with compare-and-swap deployment fencing.

    Deployment identifiers are single use. Retrying the currently active identifier
    is idempotent only when the caller supplies the current revision and contents.
    """
    if not isinstance(deployment, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', deployment):
        raise ValueError('invalid_deployment')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('invalid_revision')
    groups = validate_catalogue(groups)
    items = identities(groups)
    digest = fingerprint(items)
    state = AssistantCatalogueState.objects.select_for_update().get(pk=1)
    if state.revision != expected_revision:
        raise ValueError('stale_activation')
    if state.deployment == deployment:
        if state.catalogue_hash != digest:
            raise ValueError('deployment_reused')
        return state.revision
    if AssistantCatalogueDeployment.objects.filter(pk=deployment).exists():
        raise ValueError('deployment_reused')
    previous = state.members
    updated = {key: {**value, 'active': False} for key, value in previous.items()}
    for key, identity in items.items():
        old = previous.get(key)
        generation = (old['generation'] if old and old['active'] and old['identity'] == identity
                      else (old['generation'] + 1 if old else 1))
        updated[key] = {'identity': identity, 'generation': generation, 'active': True}
    if state.revision == 0:
        approved = validate_baseline(_hook('GROUP_GRANT_BASELINE', {}))
        entries = {group['id']: {member['id']: (group, member) for member in group['members']}
                   for group in groups}
        baseline = {}
        for gid, members in approved.items():
            baseline[gid] = {}
            for mid, identity in members.items():
                pair = entries.get(gid, {}).get(mid)
                if pair is None or member_revision(*pair) != identity:
                    raise ValueError('invalid_group_baseline')
                group, member = pair
                baseline[gid][mid] = member_revision(group, {**member, 'generation': updated[mid]['generation']})
        state.baseline = baseline
    AssistantCatalogueDeployment.objects.create(deployment=deployment)
    state.revision += 1
    state.deployment, state.catalogue_hash, state.members = deployment, digest, updated
    state.save(update_fields=['revision', 'deployment', 'catalogue_hash', 'members', 'baseline'])
    return state.revision


def verified_state(*, lock=False):
    """Prove this worker's complete catalogue and deployment match durable state."""
    try:
        provider = _hook('GROUP_AUTHORITATIVE_CATALOGUE')
        deployment = getattr(settings, 'DCM_ASSISTANT_GROUP_DEPLOYMENT', None)
        if not callable(provider) or not isinstance(deployment, str) or not deployment:
            raise ValueError('missing_worker_identity')
        digest = fingerprint(identities(validate_catalogue(provider())))
        query = AssistantCatalogueState.objects.select_for_update() if lock else AssistantCatalogueState.objects
        state = query.get(pk=1)
        if not state.revision or state.deployment != deployment or state.catalogue_hash != digest:
            raise ValueError('stale_worker')
        return state
    except Exception as exc:
        raise PermissionDenied('catalogue_unverified') from exc


def groups_enabled():
    """A deployment cannot escape the active contract by omitting its hook."""
    if _hook('GROUP_CATALOGUE') is not None:
        verified_state()
        return True
    try:
        if AssistantCatalogueState.objects.get(pk=1).revision:
            raise PermissionDenied('catalogue_unverified')
    except PermissionDenied:
        raise
    except Exception as exc:
        raise PermissionDenied('catalogue_unverified') from exc
    return False


def attach_generations(groups, state):
    for group in groups:
        for member in group['members']:
            entry = state.members.get(member['id'])
            if not entry or not entry['active'] or entry['identity'] != member_revision(group, member):
                raise PermissionDenied('catalogue_unverified')
            member['generation'] = entry['generation']
    return ActiveCatalogue(groups, state.revision, state.baseline)
