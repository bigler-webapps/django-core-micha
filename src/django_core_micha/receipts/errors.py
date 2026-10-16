"""Stable client-safe receipt errors."""

STATUS = {"consent_required":403,"unsupported_file_type":415,"file_too_large":413,"invalid_request":400,"nothing_recognised":422,"spend_cap_reached":429,"count_limit_reached":429,"provider_unavailable":503,"authentication_required":401}

class ReceiptError(Exception):
    def __init__(self, code, status=None):
        self.code = code
        self.status = status or STATUS[code]
        super().__init__(code)

