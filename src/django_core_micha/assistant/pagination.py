"""Signed, context-bound cursor helpers."""
from django.core import signing

SALT = "dcm_assistant.cursor.v1"
class InvalidCursor(ValueError): pass

def encode_cursor(payload, context):
    return signing.dumps({"c": context, "p": payload}, salt=SALT, compress=True)

def decode_cursor(value, context):
    if not isinstance(value, str) or not value or len(value) > 1024: raise InvalidCursor
    try: data = signing.loads(value, salt=SALT, max_age=3600)
    except signing.BadSignature as exc: raise InvalidCursor from exc
    if not isinstance(data, dict) or data.get("c") != context or not isinstance(data.get("p"), dict): raise InvalidCursor
    return data["p"]
