"""Decimal-only money and FX helpers for reimbursement data."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Sequence

MONEY_DECIMAL_PLACES = 2
MONEY_MAX_DIGITS = 12
FX_RATE_DECIMAL_PLACES = 8
FX_RATE_MAX_DIGITS = 18
MONEY_QUANTIZER = Decimal("0.01")
FX_RATE_QUANTIZER = Decimal("0.00000001")


def quantize_money(value):
    if value is None:
        return value
    return Decimal(value).quantize(MONEY_QUANTIZER, rounding=ROUND_HALF_UP)


def quantize_fx_rate(value):
    if value is None:
        return value
    return Decimal(value).quantize(FX_RATE_QUANTIZER, rounding=ROUND_HALF_UP)


def convert_amount(original_amount, fx_rate):
    return quantize_money(Decimal(original_amount) * Decimal(fx_rate))


def _finite_decimal(value, name: str) -> Decimal:
    decimal_value = Decimal(value)
    if not decimal_value.is_finite():
        raise ValueError(f"{name} must be finite")
    return decimal_value


def _coefficient_and_exponent(value: Decimal) -> tuple[int, int]:
    parts = value.as_tuple()
    coefficient = int("".join(str(digit) for digit in parts.digits) or "0")
    if parts.sign:
        coefficient = -coefficient
    return coefficient, parts.exponent


def _scaled_integer(value: Decimal, exponent: int) -> int:
    coefficient, value_exponent = _coefficient_and_exponent(value)
    return coefficient * (10 ** (value_exponent - exponent))


def _amount_in_cents(value: Decimal) -> int:
    coefficient, exponent = _coefficient_and_exponent(value)
    cent_shift = exponent + MONEY_DECIMAL_PLACES
    if cent_shift >= 0:
        return coefficient * (10**cent_shift)
    divisor = 10 ** (-cent_shift)
    if coefficient % divisor:
        raise ValueError("quantized money value does not have an exact cent amount")
    return coefficient // divisor


def distribute_amount(total, weights: Iterable, tie_break_keys: Sequence | None = None) -> list[Decimal]:
    """Distribute a quantized total by largest remainder, preserving every cent."""
    decimal_total = _finite_decimal(total, "total")
    quantized_total = quantize_money(decimal_total)
    decimal_weights = [_finite_decimal(weight, "weight") for weight in weights]

    if not decimal_weights:
        raise ValueError("weights must not be empty")
    if any(weight <= 0 for weight in decimal_weights):
        raise ValueError("weights must be positive")

    if tie_break_keys is None:
        keys = list(range(len(decimal_weights)))
    else:
        keys = list(tie_break_keys)
        if len(keys) != len(decimal_weights):
            raise ValueError("tie_break_keys must match the number of weights")

    shared_exponent = min(_coefficient_and_exponent(weight)[1] for weight in decimal_weights)
    integer_weights = [
        _scaled_integer(weight, shared_exponent) for weight in decimal_weights
    ]
    weight_sum = sum(integer_weights)
    total_cents = _amount_in_cents(quantized_total)

    base_cents = []
    remainders = []
    for integer_weight in integer_weights:
        whole_cents, remainder = divmod(total_cents * integer_weight, weight_sum)
        base_cents.append(whole_cents)
        remainders.append(remainder)

    remaining_cents = total_cents - sum(base_cents)
    order = sorted(
        range(len(integer_weights)),
        key=lambda index: (-remainders[index], keys[index]),
    )
    for index in order[:remaining_cents]:
        base_cents[index] += 1

    return [Decimal(cents).scaleb(-MONEY_DECIMAL_PLACES) for cents in base_cents]


def split_conserving(original_total, converted_total, original_shares: Iterable) -> list[tuple[Decimal, Decimal]]:
    """Pair original shares with converted shares that conserve both totals."""
    quantized_original_total = quantize_money(
        _finite_decimal(original_total, "original_total")
    )
    shares = [_finite_decimal(share, "original_share") for share in original_shares]
    if not shares:
        raise ValueError("original_shares must not be empty")
    if any(share <= 0 for share in shares):
        raise ValueError("original_shares must be positive")

    shared_exponent = min(
        [_coefficient_and_exponent(share)[1] for share in shares]
        + [_coefficient_and_exponent(quantized_original_total)[1]]
    )
    shares_total = sum(_scaled_integer(share, shared_exponent) for share in shares)
    expected_total = _scaled_integer(quantized_original_total, shared_exponent)
    if shares_total != expected_total:
        raise ValueError("original_shares must sum exactly to the quantized original_total")

    converted_shares = distribute_amount(converted_total, shares)
    return list(zip(shares, converted_shares))
