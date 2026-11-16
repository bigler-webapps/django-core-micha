"""Renew the same local instance controller during an explicit stopped-instance resume."""

from datetime import timedelta
import hmac

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from django_core_micha.test_management.activation import validate_configuration
from django_core_micha.test_management.models import TestInstance


class Command(BaseCommand):
    def handle(self, *args, **options):
        try:
            proof = validate_configuration()
            with transaction.atomic():
                instance = TestInstance.objects.select_for_update().filter(instance_id=proof.instance_id).first()
                if instance is None:
                    return
                if (instance.status != TestInstance.Status.ACTIVE or
                        not hmac.compare_digest(instance.controller_token_digest, proof.token_digest)):
                    raise ValueError
                instance.expires_at = timezone.now() + timedelta(seconds=int(settings.TEST_MANAGEMENT_CONTROLLER_TTL_SECONDS))
                instance.save(update_fields=["expires_at"])
        except Exception:
            raise CommandError("The isolated controller could not be renewed.") from None
