"""Transactional choice writes and revision-safe ordering."""

import uuid

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .guard import receipt_for_request
from .models import PrivacyChoice, PrivacyDecision, PrivacyReceipt


def apply_choice_request(request, manifest, validated, existing_receipt=None, now=None):
    """Persist one validated request atomically; return ``(receipt_id, outcome)``.

    Refusal-only requests are safe to apply at any expected sequence. Grants require
    an exact sequence match, so an old tab cannot restore a grant after withdrawal.
    """
    now = now or timezone.now()
    if existing_receipt is None:
        existing_receipt = receipt_for_request(request)

    has_grant = any(
        state == PrivacyChoice.GRANTED for _purpose, state in validated["choices"].values()
    )
    stale = False
    with transaction.atomic():
        if existing_receipt is not None:
            # The no-op write takes SQLite's writer lock before reading the sequence;
            # select_for_update provides the corresponding row lock on supporting DBs.
            PrivacyReceipt.objects.filter(pk=existing_receipt.pk).update(seq=F("seq"))
            receipt = PrivacyReceipt.objects.select_for_update().filter(pk=existing_receipt.pk).first()
        else:
            receipt = None
        if receipt is None:
            if has_grant and validated["expected_seq"] != 0:
                return None, "stale"
            receipt = PrivacyReceipt.objects.create(
                receipt_id=uuid.uuid4().hex,
                app=manifest["app"],
                site=manifest["site"],
                seq=0,
            )

        if PrivacyDecision.objects.filter(receipt=receipt, request_id=validated["request_id"]).exists():
            return receipt.receipt_id, "idempotent"
        if has_grant and validated["expected_seq"] != receipt.seq:
            stale = True
        else:
            receipt.seq += 1
            receipt.save(update_fields=["seq"])
            expires_at = now + timezone.timedelta(days=manifest["choice_validity_days"])
            for purpose_id, (purpose, state) in validated["choices"].items():
                old = PrivacyChoice.objects.select_for_update().filter(
                    receipt=receipt, purpose_id=purpose_id
                ).first()
                active_old_grant = bool(
                    old
                    and old.state == PrivacyChoice.GRANTED
                    and old.purpose_revision == purpose["revision"]
                    and old.expires_at > now
                )
                kind = (
                    PrivacyDecision.WITHDRAWN
                    if state == PrivacyChoice.REFUSED and active_old_grant
                    else state
                )
                PrivacyChoice.objects.update_or_create(
                    receipt=receipt,
                    purpose_id=purpose_id,
                    defaults={
                        "purpose_revision": purpose["revision"],
                        "state": state,
                        "effective_at": now,
                        "expires_at": expires_at,
                    },
                )
                PrivacyDecision.objects.create(
                    receipt=receipt,
                    purpose_id=purpose_id,
                    purpose_revision=purpose["revision"],
                    kind=kind,
                    seq=receipt.seq,
                    decided_at=now,
                    locale=validated["locale"],
                    notice_version=manifest["notice"]["version"],
                    channel=validated["channel"],
                    request_id=validated["request_id"],
                )
    return receipt.receipt_id, "stale" if stale else "applied"
