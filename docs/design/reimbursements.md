# Reimbursement core rules

`django_core_micha.reimbursements` provides pure reimbursement data rules for money and FX quantization,
amount distribution, template accounts, cash allocation, required fields, and cash FX override reasons.
Its public API includes `AllocationError`, the money constants and helpers, `TEMPLATE_ACCOUNTS`,
`TEMPLATE_ACCOUNT_VALUES`, `is_template_account`, the cash allocation helpers, and the validation helpers.

Host applications keep their own models, ORM, storage, and authorization. Behavior is characterized against
`reimbursements claims/models.py`.
