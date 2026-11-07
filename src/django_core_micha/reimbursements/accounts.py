"""Template account choices used by reimbursement exports."""

TEMPLATE_ACCOUNTS = (
    ("422000 Travel costs", "422000 Travel costs"),
    ("422010 Accommodation and boarding costs", "422010 Accommodation and boarding costs"),
    ("423000 Medical examinations, vaccinations etc.", "423000 Medical examinations, vaccinations etc."),
    ("423020 Other personnel costs", "423020 Other personnel costs"),
    ("421000 Continuous education", "421000 Continuous education"),
    ("500000 Procurement of material", "500000 Procurement of material"),
    ("500040 Books", "500040 Books"),
    ("510010 Procurement equipment < CHF 5'000", "510010 Procurement equipment < CHF 5'000"),
    ("550000 Office material, printed matters", "550000 Office material, printed matters"),
    ("550010 Photocopies", "550010 Photocopies"),
    ("550020 Telephone, Fax, Internet", "550020 Telephone, Fax, Internet"),
    ("550030 Postage", "550030 Postage"),
    (
        "550060 Expenses for meetings, guests, seminaries (enclose name list)",
        "550060 Expenses for meetings, guests, seminaries (enclose name list)",
    ),
    ("550070 Donations", "550070 Donations"),
    ("550080 Authorizations, Fees", "550080 Authorizations, Fees"),
    (
        "568000 Field expenses (Remuneration field workers, consumables bought + used locally)",
        "568000 Field expenses (Remuneration field workers, consumables bought + used locally)",
    ),
    ("Other expenses", "Other expenses"),
)

TEMPLATE_ACCOUNT_VALUES = frozenset(value for value, _label in TEMPLATE_ACCOUNTS)


def is_template_account(value) -> bool:
    return value in TEMPLATE_ACCOUNT_VALUES
