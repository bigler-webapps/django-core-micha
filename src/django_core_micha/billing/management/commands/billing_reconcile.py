from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_datetime
from django.utils import timezone as django_timezone

from django_core_micha.billing.models import StripeEvent
from django_core_micha.billing.stripe_service import _dict, _stripe, complete_withdrawal_from_refund, process_stripe_event, withdrawal_refund_purchase


class Command(BaseCommand):
    help = "Reconcile app subscriptions and recent Stripe events."

    def add_arguments(self, parser):
        parser.add_argument("--since", help="ISO timestamp; defaults to seven days ago (UTC).")

    def handle(self, *args, **options):
        from django.conf import settings
        app = getattr(settings, "DCM_BILLING_APP", "")
        try:
            stripe = _stripe()
            since = parse_datetime(options["since"]) if options["since"] else django_timezone.now() - timedelta(days=7)
            if since is None:
                raise ValueError("invalid since timestamp")
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            count = 0
            for sub in stripe.Subscription.list(limit=100).auto_paging_iter():
                data = _dict(sub)
                if (data.get("metadata") or {}).get("app") != app:
                    continue
                event = {"type": "customer.subscription.updated", "data": {"object": data}}
                with transaction.atomic():
                    process_stripe_event(event)
                count += 1
            for event in stripe.Event.list(created={"gte": int(since.timestamp())}, limit=100).auto_paging_iter():
                data = _dict(event)
                event_id = data.get("id")
                if not event_id:
                    continue
                row, created = StripeEvent.objects.get_or_create(event_id=event_id,
                    defaults={"type": data.get("type", "unknown"), "status": "received"})
                if not created and row.status in ("processed", "ignored"):
                    continue
                try:
                    with transaction.atomic():
                        status = process_stripe_event(data)
                        StripeEvent.objects.filter(pk=row.pk).update(status=status, processed_at=django_timezone.now(), error_text="")
                except Exception as exc:
                    StripeEvent.objects.filter(pk=row.pk).update(status="failed", error_text=type(exc).__name__)
                    raise
                count += 1
            for refund in stripe.Refund.list(created={"gte": int(since.timestamp())}, limit=100).auto_paging_iter():
                data = _dict(refund)
                if withdrawal_refund_purchase(data) is None:
                    continue
                with transaction.atomic():
                    if complete_withdrawal_from_refund(data):
                        count += 1
            self.stdout.write(str(count))
        except Exception as exc:
            raise CommandError(f"billing reconciliation failed ({type(exc).__name__})") from None
