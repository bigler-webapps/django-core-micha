"""Pure rules for allocating cash withdrawals to reimbursements."""

from decimal import Decimal

from .errors import AllocationError
from .money import convert_amount, quantize_fx_rate, quantize_money


def remaining_balance(withdrawal_amount, allocated_amounts) -> Decimal:
    allocated_total = sum(
        (Decimal(amount) for amount in allocated_amounts), Decimal("0")
    )
    return quantize_money(withdrawal_amount) - quantize_money(allocated_total)


def check_allocation(withdrawal_amount, already_allocated, new_original_amount) -> None:
    new_amount = quantize_money(new_original_amount)
    if new_amount is None or new_amount <= Decimal("0.00"):
        raise AllocationError("non_positive")
    if new_amount > remaining_balance(withdrawal_amount, already_allocated):
        raise AllocationError("over_allocated")


def fx_mismatch(withdrawal_fx, reimbursement_fx, manual_override) -> bool:
    return not manual_override and quantize_fx_rate(withdrawal_fx) != quantize_fx_rate(
        reimbursement_fx
    )


def allocation_fx(withdrawal_fx, reimbursement_fx, manual_override) -> Decimal:
    rate = reimbursement_fx if manual_override else withdrawal_fx
    return quantize_fx_rate(rate)


def allocation_converted(original_amount, effective_fx) -> Decimal:
    return convert_amount(original_amount, effective_fx)
