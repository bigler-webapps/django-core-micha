from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date
from django.db import transaction

from django_core_micha.billing.models import Contract, Declaration


class Command(BaseCommand):
    help="Resolve a queued billing declaration."

    def add_arguments(self,parser):
        parser.add_argument("reference")
        decision=parser.add_mutually_exclusive_group(required=True)
        decision.add_argument("--accept",action="store_true")
        decision.add_argument("--reject",action="store_true")
        parser.add_argument("--contract")
        parser.add_argument("--effective")
        parser.add_argument("--refund-cents",type=int)
        parser.add_argument("--note",default="")
        parser.add_argument("--actor",default="billing-operator-cli")

    def handle(self,*args,**opts):
        app=getattr(settings,"DCM_BILLING_APP","")
        if opts["refund_cents"] is not None and opts["refund_cents"]<0: raise CommandError("--refund-cents must be non-negative")
        effective=parse_date(opts["effective"]) if opts["effective"] else None
        if opts["effective"] and effective is None: raise CommandError("--effective must be YYYY-MM-DD")
        with transaction.atomic():
            declaration=Declaration.objects.select_for_update().filter(reference=opts["reference"],app=app).first()
            if not declaration: raise CommandError("declaration not found")
            if declaration.status in ("effective","rejected"): raise CommandError("declaration is already resolved")
            if opts["reject"] and not opts["note"].strip(): raise CommandError("--note is required with --reject")
            contract=None
            contract_ref=opts["contract"]
            if contract_ref:
                contract=Contract.objects.filter(reference=contract_ref,app=app).first()
                if not contract: raise CommandError("contract not found")
                declaration.contract=contract; declaration.user=contract.user
            elif declaration.contract_id:
                contract=declaration.contract
            if opts["accept"] and not contract: raise CommandError("accepting requires a linked contract or --contract")
            if opts["refund_cents"] is not None and contract and opts["refund_cents"]>sum(x.amount_paid_cents for x in contract.payments.all()):
                raise CommandError("--refund-cents cannot exceed total payments")
            declaration.actor_label=opts["actor"][:200]
            declaration.decision_note=opts["note"][:2000]
            declaration.decision_refund_cents=opts["refund_cents"]
            if effective and declaration.kind=="cancellation" and declaration.cancellation_type!="extraordinary": declaration.requested_end_date=effective
            declaration.save(update_fields=("contract","user","actor_label","decision_note","decision_refund_cents","requested_end_date"))
            if opts["reject"]:
                declaration.status="rejected"; declaration.save(update_fields=("status",))
                self.stdout.write(f"declaration={declaration.reference} status=rejected actor={declaration.actor_label}")
                return
            from django_core_micha.billing.contracts import apply_cancellation,apply_extraordinary_termination,apply_withdrawal
            if declaration.kind=="cancellation" and declaration.cancellation_type=="extraordinary" and (effective or opts["refund_cents"]):
                apply_extraordinary_termination(declaration,effective,opts["refund_cents"])
            elif declaration.kind=="cancellation":
                if opts["refund_cents"] is not None: raise CommandError("--refund-cents on a cancellation needs an extraordinary declaration")
                apply_cancellation(declaration,operator_accepted=True)
            elif contract.withdrawal_policy=="full": apply_withdrawal(declaration,operator_accepted=True)
            else:
                if opts["refund_cents"] is not None: raise CommandError("--refund-cents is available for full-refund contracts")
                from django_core_micha.billing.declarations import apply_withdrawal_declaration
                apply_withdrawal_declaration(declaration)
            declaration.refresh_from_db()
            self.stdout.write(f"declaration={declaration.reference} status={declaration.status} actor={declaration.actor_label}")
