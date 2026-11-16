import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from django_core_micha.billing.models import AnnualTermReminder, Contract, Refund, Declaration
from django_core_micha.billing.contracts import (apply_due_cancellations, ensure_contract_schedule,
    retry_refund, run_transition_guard, resume_withdrawals)
from django_core_micha.billing.declarations import retry_declaration_emails
from django_core_micha.billing.annual_term_reminders import process_annual_term_reminders

log=logging.getLogger(__name__)


class Command(BaseCommand):
    help="Process billing declarations, retries, due cancellations, and continuation guards."

    def handle(self,*args,**options):
        app=getattr(settings,"DCM_BILLING_APP","")
        retry_declaration_emails()
        reminder_error = None
        try:
            reminder_sent, reminder_unresolved, reminder_skipped = process_annual_term_reminders(app=app)
        except Exception as exc:
            log.error("Annual reminder processing failed error=%s", type(exc).__name__)
            self.stderr.write(f"annual reminder processing failed ({type(exc).__name__})")
            reminder_error = type(exc).__name__
            reminder_sent, reminder_unresolved, reminder_skipped = 0, 0, 0
        for contract in Contract.objects.filter(app=app,schedule_status__in=("pending","failed")):
            try: ensure_contract_schedule(contract)
            except Exception as exc: log.error("Schedule retry failed contract=%s error=%s",contract.pk,type(exc).__name__)
        now=timezone.now()
        resumed=resume_withdrawals()
        for refund in Refund.objects.filter(contract__app=app,status__in=("pending","requested","failed","requires_action")):
            try: retry_refund(refund.pk)
            except Exception as exc: log.error("Refund retry failed refund=%s error=%s",refund.pk,type(exc).__name__)
        cancellations=apply_due_cancellations(now)
        guarded=run_transition_guard(now)
        overdue=list(Refund.objects.filter(contract__app=app,due_by__lt=now).exclude(status__in=("succeeded","cancelled")).values_list("pk","status"))
        for refund_id,status in overdue: self.stdout.write(f"overdue refund={refund_id} status={status}")
        for reminder in AnnualTermReminder.objects.filter(contract__app=app, status="unresolved").select_related("contract"):
            self.stdout.write(f"unresolved annual_reminder={reminder.pk} contract={reminder.contract.reference} reason={reminder.last_error_code}")
        self.stdout.write(f"annual_reminders_accepted={reminder_sent} annual_reminders_unresolved={reminder_unresolved} annual_reminders_skipped={reminder_skipped} withdrawals_resumed={resumed} cancellations_applied={cancellations} transition_guards={guarded} overdue_refunds={len(overdue)}")
        if reminder_error:
            raise CommandError(f"annual reminder processing failed ({reminder_error})")
