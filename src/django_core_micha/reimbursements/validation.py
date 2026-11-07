"""Pure validation rules for reimbursement workflow data."""

from collections.abc import Mapping

NON_DRAFT_REQUIRED_FIELDS = (
    "cost_center",
    "claimant_profile",
    "expense_date",
    "merchant_name",
    "account",
    "original_amount",
    "original_currency",
    "fx_rate",
    "converted_amount",
    "converted_currency",
)


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def missing_required_fields(values: Mapping) -> list[str]:
    return [
        field
        for field in NON_DRAFT_REQUIRED_FIELDS
        if field not in values or _is_missing(values[field])
    ]


def fx_override_reason_missing(payment_method, workflow_status, manual_override, reason) -> bool:
    reason_is_blank = reason is None or (isinstance(reason, str) and not reason.strip())
    return (
        workflow_status != "draft"
        and payment_method == "cash"
        and bool(manual_override)
        and reason_is_blank
    )
