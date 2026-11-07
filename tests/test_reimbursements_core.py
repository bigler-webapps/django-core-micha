from decimal import Decimal

import pytest

from django_core_micha.reimbursements import (
    NON_DRAFT_REQUIRED_FIELDS,
    TEMPLATE_ACCOUNTS,
    TEMPLATE_ACCOUNT_VALUES,
    AllocationError,
    allocation_converted,
    allocation_fx,
    check_allocation,
    convert_amount,
    distribute_amount,
    fx_mismatch,
    fx_override_reason_missing,
    is_template_account,
    missing_required_fields,
    quantize_fx_rate,
    quantize_money,
    remaining_balance,
    split_conserving,
)


def test_money_quantization_uses_half_up_and_fx_scale():
    assert quantize_money(Decimal("2.675")) == Decimal("2.68")
    assert quantize_money(Decimal("2.665")) == Decimal("2.67")
    assert quantize_fx_rate(Decimal("1.234567895")) == Decimal("1.23456790")
    assert quantize_money(None) is None
    assert quantize_fx_rate(None) is None


def test_convert_amount_quantizes_the_product():
    assert convert_amount(Decimal("12.345"), Decimal("1.2")) == Decimal("14.81")
    assert convert_amount(Decimal("100"), Decimal("1.234567895")) == Decimal("123.46")


def test_distribute_amount_preserves_total_and_breaks_remainder_ties_by_key():
    assert distribute_amount(
        Decimal("100.00"),
        [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")],
    ) == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]

    inputs = (Decimal("0.02"), [Decimal("1"), Decimal("1"), Decimal("1")], ["z", "a", "b"])
    first = distribute_amount(*inputs)
    second = distribute_amount(*inputs)
    assert first == [Decimal("0.00"), Decimal("0.01"), Decimal("0.01")]
    assert second == first
    assert sum(first, Decimal("0.00")) == Decimal("0.02")


@pytest.mark.parametrize(
    "total, weights",
    [
        (Decimal("1.00"), []),
        (Decimal("1.00"), [Decimal("1"), Decimal("0")]),
        (Decimal("1.00"), [Decimal("NaN")]),
        (Decimal("Infinity"), [Decimal("1")]),
    ],
)
def test_distribute_amount_rejects_empty_non_positive_or_non_finite_values(total, weights):
    with pytest.raises(ValueError):
        distribute_amount(total, weights)


def test_split_conserving_keeps_both_currency_totals_exact():
    pairs = split_conserving(
        Decimal("10.00"),
        Decimal("11.00"),
        [Decimal("3.33"), Decimal("3.33"), Decimal("3.34")],
    )
    assert pairs == [
        (Decimal("3.33"), Decimal("3.66")),
        (Decimal("3.33"), Decimal("3.66")),
        (Decimal("3.34"), Decimal("3.68")),
    ]
    assert sum((original for original, _converted in pairs), Decimal("0")) == Decimal("10.00")
    assert sum((converted for _original, converted in pairs), Decimal("0")) == Decimal("11.00")


def test_template_accounts_match_the_claims_catalog_exactly():
    expected = [
        "422000 Travel costs",
        "422010 Accommodation and boarding costs",
        "423000 Medical examinations, vaccinations etc.",
        "423020 Other personnel costs",
        "421000 Continuous education",
        "500000 Procurement of material",
        "500040 Books",
        "510010 Procurement equipment < CHF 5'000",
        "550000 Office material, printed matters",
        "550010 Photocopies",
        "550020 Telephone, Fax, Internet",
        "550030 Postage",
        "550060 Expenses for meetings, guests, seminaries (enclose name list)",
        "550070 Donations",
        "550080 Authorizations, Fees",
        "568000 Field expenses (Remuneration field workers, consumables bought + used locally)",
        "Other expenses",
    ]
    assert TEMPLATE_ACCOUNTS == tuple((value, value) for value in expected)
    assert len(TEMPLATE_ACCOUNTS) == 17
    assert TEMPLATE_ACCOUNT_VALUES == frozenset(expected)
    assert all(is_template_account(value) for value in expected)  # every catalog entry, including "Other expenses"
    assert not is_template_account("550000 Office materials")


def test_cash_balance_and_allocation_limits():
    assert remaining_balance(
        Decimal("100.00"), [Decimal("20.01"), Decimal("30.02")]
    ) == Decimal("49.97")

    check_allocation(Decimal("100.00"), [Decimal("30.00")], Decimal("70.00"))
    with pytest.raises(AllocationError) as over_allocated:
        check_allocation(Decimal("100.00"), [Decimal("30.00")], Decimal("70.01"))
    assert over_allocated.value.code == "over_allocated"
    assert isinstance(over_allocated.value.message, str)

    with pytest.raises(AllocationError) as non_positive:
        check_allocation(Decimal("100.00"), [], Decimal("0.00"))
    assert non_positive.value.code == "non_positive"


def test_cash_fx_rules_and_conversion():
    assert fx_mismatch(Decimal("1.234567891"), Decimal("1.234567894"), False) is False
    assert fx_mismatch(Decimal("1.2"), Decimal("1.3"), False) is True
    assert fx_mismatch(Decimal("1.2"), Decimal("1.3"), True) is False
    assert allocation_fx(Decimal("1.234567899"), Decimal("1.3"), False) == Decimal("1.23456790")
    assert allocation_fx(Decimal("1.2"), Decimal("1.345678901"), True) == Decimal("1.34567890")
    assert allocation_converted(Decimal("12.35"), Decimal("1.23456789")) == Decimal("15.25")


def test_missing_required_fields_handles_absent_none_blank_and_order():
    values = {
        "cost_center": "cc-1",
        "claimant_profile": None,
        "expense_date": "2026-11-07",
        "merchant_name": " \t ",
        "account": "Other expenses",
        "original_amount": Decimal("1.00"),
        "original_currency": "CHF",
        "fx_rate": Decimal("1.00000000"),
        "converted_amount": Decimal("1.00"),
        "converted_currency": "CHF",
    }
    assert missing_required_fields(values) == ["claimant_profile", "merchant_name"]
    assert missing_required_fields({"merchant_name": ""}) == [
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
    ]
    assert missing_required_fields(values) == [
        field
        for field in NON_DRAFT_REQUIRED_FIELDS
        if field in {"claimant_profile", "merchant_name"}
    ]
    complete = {field: "present" for field in NON_DRAFT_REQUIRED_FIELDS}
    assert missing_required_fields(complete) == []


def test_fx_override_reason_is_required_only_for_non_draft_cash_override():
    assert fx_override_reason_missing("cash", "draft", True, "") is False
    assert fx_override_reason_missing("card", "ready_for_export", True, "") is False
    assert fx_override_reason_missing("cash", "ready_for_export", True, " \t ") is True
    assert fx_override_reason_missing("cash", "ready_for_export", True, "market rate") is False
    assert fx_override_reason_missing("cash", "ready_for_export", False, "") is False  # no override, no reason needed
