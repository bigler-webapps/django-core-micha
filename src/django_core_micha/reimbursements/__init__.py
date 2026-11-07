"""Pure reimbursement data rules; host apps own ORM, storage and authorization."""

from .accounts import TEMPLATE_ACCOUNTS, TEMPLATE_ACCOUNT_VALUES, is_template_account
from .cash import (
    allocation_converted,
    allocation_fx,
    check_allocation,
    fx_mismatch,
    remaining_balance,
)
from .errors import AllocationError
from .money import (
    FX_RATE_DECIMAL_PLACES,
    FX_RATE_MAX_DIGITS,
    FX_RATE_QUANTIZER,
    MONEY_DECIMAL_PLACES,
    MONEY_MAX_DIGITS,
    MONEY_QUANTIZER,
    convert_amount,
    distribute_amount,
    quantize_fx_rate,
    quantize_money,
    split_conserving,
)
from .validation import (
    NON_DRAFT_REQUIRED_FIELDS,
    fx_override_reason_missing,
    missing_required_fields,
)

__all__ = (
    "AllocationError",
    "FX_RATE_DECIMAL_PLACES",
    "FX_RATE_MAX_DIGITS",
    "FX_RATE_QUANTIZER",
    "MONEY_DECIMAL_PLACES",
    "MONEY_MAX_DIGITS",
    "MONEY_QUANTIZER",
    "NON_DRAFT_REQUIRED_FIELDS",
    "TEMPLATE_ACCOUNTS",
    "TEMPLATE_ACCOUNT_VALUES",
    "allocation_converted",
    "allocation_fx",
    "check_allocation",
    "convert_amount",
    "distribute_amount",
    "fx_mismatch",
    "fx_override_reason_missing",
    "is_template_account",
    "missing_required_fields",
    "quantize_fx_rate",
    "quantize_money",
    "remaining_balance",
    "split_conserving",
)
