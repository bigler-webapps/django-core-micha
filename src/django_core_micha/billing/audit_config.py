from django_core_micha.billing.models import BillingCustomer, StripePrice, Subscription, TeamPackage, TeamSeat, FreeAccess, TokenAccount, TokenEntry, StripeEvent, AICostRecord, Purchase
from django_core_micha.auditlog import register
for model in (BillingCustomer, StripePrice, Subscription, TeamPackage, TeamSeat, FreeAccess, TokenAccount, TokenEntry, StripeEvent, AICostRecord, Purchase):
    register(model, redact_fields=tuple(f.name for f in model._meta.fields if f.name.startswith("stripe_") or f.name in ("email", "reference")))
