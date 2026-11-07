"""Errors raised by pure reimbursement allocation rules."""


class AllocationError(Exception):
    """An allocation violates a reimbursement allocation rule."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message if message is not None else code
        super().__init__(self.message)
