"""Deterministic offline receipt reader for local and test environments."""
import hashlib
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

def read(content):
    if getattr(settings,"ENV_TYPE","") not in {"local","test"}:raise ImproperlyConfigured("Fake reader forbidden outside local/test")
    cents=int(hashlib.sha256(content).hexdigest()[:4],16)%9900+100
    def one(merchant,total,box):return {"merchant":merchant,"date":"","total":f"{total/100:.2f}","currency":"CHF","payment_method":"unknown","items":[],"vat":[],"box":box,"confidence":"high"}
    if content.startswith(b"FAKE-MUL"):return {"receipts":[one("Fake Merchant",cents,{"x":0.05,"y":0.1,"w":0.4,"h":0.4}),one("Fake Merchant 2",cents+100,{"x":0.55,"y":0.1,"w":0.4,"h":0.4})]}
    return {"receipts":[one("Fake Merchant",cents,{"x":0,"y":0,"w":0,"h":0})]}

