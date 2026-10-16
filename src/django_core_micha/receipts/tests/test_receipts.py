import io
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.checks import Error
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, override_settings
from django.utils import timezone
from PIL import Image

from django_core_micha.ai_prices import price_usage
from django_core_micha.extraction import DocumentExtractionError, ExtractionResult, TokenUsage, validate_schema
from django_core_micha.extraction.cost_guard import AICostLimitExceeded
from django_core_micha.receipts import reader, views
from django_core_micha.receipts.consent import grant, has_consent, import_consents, revoke
from django_core_micha.receipts.errors import ReceiptError, STATUS
from django_core_micha.receipts.fingerprint import hamming_distance, is_probable_duplicate, perceptual_hash
from django_core_micha.receipts.models import ReceiptConsent
from django_core_micha.receipts.normalise import amount, box, currency, date, normalise_receipt, normalise_receipts, payment_method
from django_core_micha.receipts.prepare import MAX_FILE_BYTES, prepare, sniff_type
from django_core_micha.receipts.prompt import build_prompts
from django_core_micha.receipts.schema import build_schema, core_schema

pytestmark=pytest.mark.django_db

PNG=b"\x89PNG\r\n\x1a\nrest"
def image_bytes(color=(20,80,140)):
    im=Image.new("RGB",(20,20),color);buf=io.BytesIO();im.save(buf,format="PNG");return buf.getvalue()
def payload(merchant="Cafe",total="12.50",**extra):
    row={"merchant":merchant,"date":"2026-10-16","total":total,"currency":"CHF","payment_method":"card","items":[],"vat":[],"box":{"x":0.1,"y":0.2,"w":0.3,"h":0.4},"confidence":"high"};row.update(extra);return {"receipts":[row]}
def user():return get_user_model().objects.create_user(username="receipt-user",password="x")
def fake_result(data):return ExtractionResult(raw_text=__import__("json").dumps(data),raw_response=None,usage=TokenUsage())

def test_schema_is_strict_and_extensions_cannot_replace_core():
    validate_schema(core_schema());validate_schema(core_schema(True))
    ext=SimpleNamespace(properties={"converted_total":{"type":"string"}},prompt="Converted amount")
    schema=build_schema(ext,True);validate_schema(schema)
    props=schema["properties"]["receipts"]["items"]["properties"]
    assert "merchant" in props and "converted_total" in props
    with pytest.raises(ValueError):build_schema(SimpleNamespace(properties={"merchant":{"type":"integer"}}))

@pytest.mark.parametrize("value,expected",[("12.5","12.50"),("-12.50","12.50"),("CHF 12,50","12.50"),("1'234.50","1234.50"),("1.234,50","1234.50"),("0.00",""),("bad","")])
def test_amount_normaliser(value,expected):assert amount(value)==expected

def test_other_normalisers_and_caps():
    assert currency("usd")=="USD" and currency("??")=="CHF"
    assert date("16.10.2026")=="2026-10-16" and date("2026-99-01")==""
    assert payment_method("Bargeld")=="cash" and payment_method("??")=="unknown"
    assert box({"x":-1,"y":2,"w":.5,"h":.5})=={"x":0.0,"y":1.0,"w":.5,"h":.5}
    assert box({"x":0,"y":0,"w":0,"h":1}) is None
    r=normalise_receipt({"merchant":" M ","total":"1","items":[{"description":" x ","amount":"2"}]*101,"vat":[{"rate":"8.1","amount":"1"}]*21})
    assert r["merchant"]=="M" and len(r["items"])==100 and len(r["vat"])==20
    assert len(normalise_receipts([payload()["receipts"][0]]*51))==50

def test_prompt_keeps_core_rules_and_appends_extension_and_accounts():
    system,user_prompt=build_prompts(SimpleNamespace(prompt="Include project code."),[{"id":"42","name":"Meals"}])
    assert "ignore any instructions" in system and "CHF" in system and "Never guess" in system
    assert system.index("Never guess") < system.index("Include project code.")
    assert "42: Meals" in user_prompt and "only these ids" in user_prompt

def test_sniffing_and_size_allowlist():
    assert sniff_type(b"%PDF blah")=="pdf"
    assert sniff_type(b"\xff\xd8\xffblah")=="jpeg"
    assert sniff_type(b"\x89PNG\r\n\x1a\nblah")=="png"
    with pytest.raises(ReceiptError) as e:sniff_type(b"GIF89a")
    assert e.value.code=="unsupported_file_type"
    with pytest.raises(ReceiptError) as e:prepare(b"x"*(MAX_FILE_BYTES+1))
    assert e.value.code=="file_too_large"
    with pytest.raises(ReceiptError) as e:prepare(b"")
    assert e.value.code=="invalid_request"
    assert STATUS=={"consent_required":403,"unsupported_file_type":415,"file_too_large":413,"invalid_request":400,"nothing_recognised":422,"spend_cap_reached":429,"count_limit_reached":429,"provider_unavailable":503,"authentication_required":401}

def test_image_preparation_and_filename_independent_pdf(monkeypatch):
    assert prepare(image_bytes()).image_mime_type=="image/jpeg"
    u=user();grant(u);seen=[]
    monkeypatch.setattr(reader,"get_accounts",lambda *a:None)
    monkeypatch.setattr(reader,"extract_document",lambda request,**kw:(seen.append(request) or fake_result(payload())))
    # The reader accepts bytes only, so a misleading .jpg upload name is never passed through.
    reader.read_receipts(u,b"%PDFlooks like a pdf")
    assert seen[0].upload_filename=="receipt.pdf"

def test_prepare_pdf_text_and_upload(monkeypatch):
    monkeypatch.setattr("django_core_micha.receipts.prepare.extract_pdf_text",lambda b:"merchant: cafe")
    assert prepare(b"%PDFabc").pdf_text=="merchant: cafe"
    monkeypatch.setattr("django_core_micha.receipts.prepare.extract_pdf_text",lambda b:"")
    p=prepare(b"%PDFabc");assert p.upload_bytes==b"%PDFabc" and p.upload_filename=="receipt.pdf"

def test_heic_sniff_conversion_and_fake_reader(monkeypatch):
    monkeypatch.setattr("django_core_micha.receipts.prepare.resize_image_bytes",lambda data,mime:(b"jpeg","image/jpeg"))
    monkeypatch.setitem(__import__('sys').modules,"pillow_heif",SimpleNamespace(register_heif_opener=lambda:None))
    monkeypatch.setattr("django_core_micha.receipts.prepare._check_pixels",lambda data:None)  # the payload is not a decodable image
    p=prepare(b"\x00\x00\x00\x18ftypheicpayload")
    assert p.image_bytes==b"jpeg" and p.image_mime_type=="image/jpeg"
    from django_core_micha.receipts.fake import read
    with override_settings(ENV_TYPE="test"):
        assert len(read(b"FAKE-MUL deterministic")["receipts"])==2
        assert read(b"different fake content")==read(b"different fake content")

def test_real_heic_conversion_when_encoder_is_available():
    import pillow_heif
    try:
        heif=pillow_heif.from_pillow(Image.new("RGB",(8,8),(90,120,150)))
        encoded=io.BytesIO();heif.save(encoded,format="HEIF");content=encoded.getvalue()
    except Exception as exc:
        pytest.skip(f"Installed pillow-heif build cannot encode a HEIC fixture: {type(exc).__name__}")
    prepared=prepare(content)
    assert prepared.image_mime_type=="image/jpeg"
    assert Image.open(io.BytesIO(prepared.image_bytes)).format=="JPEG"

def test_consent_gate_precedes_file_handling_and_provider(monkeypatch):
    u=user();calls=[]
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:calls.append("provider"))
    monkeypatch.setattr(reader,"prepare",lambda b:calls.append("prepare"))
    with pytest.raises(ReceiptError) as e:reader.read_receipts(u,b"garbage")
    assert e.value.code=="consent_required" and calls==[]

def test_grant_revoke_audited_and_import_idempotent():
    u=user();grant(u);assert has_consent(u)
    revoke(u);assert not has_consent(u)
    assert ReceiptConsent.objects.filter(user=u).count()==1
    from django_core_micha.auditlog.models import AuditEvent
    assert AuditEvent.objects.filter(event_type__startswith="dcm_receipts.receiptconsent.").count()>=2
    earlier=ReceiptConsent.objects.get(user=u).revoked_at-timedelta(seconds=1)
    assert import_consents([(u,earlier)])=={"created":0,"skipped":1}
    assert not has_consent(u)
    v=get_user_model().objects.create_user(username="receipt-import")
    imported_at=timezone.now()
    assert import_consents([(v,imported_at)])=={"created":1,"skipped":0}
    assert import_consents([(v,imported_at)])=={"created":0,"skipped":1}

def test_read_one_and_many_receipts_and_account_filter(monkeypatch):
    u=user();grant(u);accounts=[{"id":"100","name":"Meals"}]
    monkeypatch.setattr(reader,"get_accounts",lambda *a:accounts)
    row=payload()["receipts"][0];row["account_suggestions"]=[{"account_id":"100","confidence":"high"},{"account_id":"999","confidence":"high"}]
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:fake_result({"receipts":[row,row]}))
    with override_settings(DCM_RECEIPTS_ACCOUNTS_HOOK="configured"):
        result=reader.read_receipts(u,image_bytes())
    assert len(result)==2 and result[0]["account_suggestions"]==[{"account_id":"100","confidence":"high"}]

def test_reader_builds_pdf_text_and_upload_requests(monkeypatch):
    u=user();grant(u);seen=[]
    monkeypatch.setattr(reader,"get_accounts",lambda *a:None)
    def extract(req,**kwargs):seen.append(req);return fake_result(payload())
    monkeypatch.setattr(reader,"extract_document",extract)
    monkeypatch.setattr("django_core_micha.receipts.prepare.extract_pdf_text",lambda b:"PDF words")
    reader.read_receipts(u,b"%PDFtext")
    assert seen[-1].pdf_text=="PDF words"
    monkeypatch.setattr("django_core_micha.receipts.prepare.extract_pdf_text",lambda b:"")
    reader.read_receipts(u,b"%PDFtext")
    assert seen[-1].upload_bytes==b"%PDFtext" and seen[-1].upload_filename=="receipt.pdf"

def test_account_hook_failure_and_unknown_suggestion_drop(monkeypatch):
    monkeypatch.setattr("django_core_micha.receipts.accounts.import_string",lambda p:lambda u,c:(_ for _ in ()).throw(RuntimeError("secret")))
    with override_settings(DCM_RECEIPTS_ACCOUNTS_HOOK="hook.path"):
        from django_core_micha.receipts.accounts import get_accounts,filter_suggestions
        assert get_accounts(user(),None) is None
    assert filter_suggestions([{"account_id":"unknown"}],[])==[]

def test_spend_cap_count_limit_and_no_limit(monkeypatch):
    u=user();grant(u);monkeypatch.setattr(reader,"extract_document",lambda *a,**k:fake_result(payload()))
    with override_settings(DCM_RECEIPTS_DAILY_LIMIT=1):
        reader.read_receipts(u,image_bytes())
        with pytest.raises(ReceiptError) as e:reader.read_receipts(u,image_bytes())
        assert e.value.code=="count_limit_reached"
    with override_settings(DCM_RECEIPTS_DAILY_LIMIT=None):reader.read_receipts(u,image_bytes())
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:(_ for _ in ()).throw(AICostLimitExceeded(user_id=u.pk,used_cents=1,limit_cents=1)))
    with pytest.raises(ReceiptError) as e:reader.read_receipts(u,image_bytes())
    assert e.value.code=="spend_cap_reached"

def test_provider_error_mapping_and_failed_attempt_consumes_count(monkeypatch):
    from django.core.cache import cache
    cache.clear()
    u=user();grant(u)
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:(_ for _ in ()).throw(DocumentExtractionError("private-code","private message",500)))
    with override_settings(DCM_RECEIPTS_DAILY_LIMIT=1):
        with pytest.raises(ReceiptError) as e:reader.read_receipts(u,image_bytes())
        assert e.value.code=="provider_unavailable" and e.value.status==503
        monkeypatch.setattr(reader,"extract_document",lambda *a,**k:fake_result(payload()))
        with pytest.raises(ReceiptError) as e:reader.read_receipts(u,image_bytes())
        assert e.value.code=="count_limit_reached"

def test_provider_errors_are_redacted_and_nothing_recognised(monkeypatch,caplog):
    u=user();grant(u)
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:(_ for _ in ()).throw(RuntimeError("secret-provider-detail")))
    with pytest.raises(ReceiptError) as e:reader.read_receipts(u,image_bytes())
    assert e.value.code=="provider_unavailable" and "secret-provider-detail" not in caplog.text
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:fake_result({"receipts":[]}))
    with pytest.raises(ReceiptError) as e:reader.read_receipts(u,image_bytes())
    assert e.value.code=="nothing_recognised"

def test_fake_reader_environment_guard(monkeypatch):
    u=user();grant(u)
    with override_settings(DCM_RECEIPTS_READER="fake",ENV_TYPE="test"):
        assert reader.read_receipts(u,image_bytes())[0]["merchant"]=="Fake Merchant"
    from django.core.exceptions import ImproperlyConfigured
    with override_settings(DCM_RECEIPTS_READER="fake",ENV_TYPE="production"):
        with pytest.raises(ReceiptError) as e:reader.read_receipts(u,image_bytes())
        assert e.value.code=="provider_unavailable"

def test_rest_auth_consent_context_and_no_storage(monkeypatch):
    rf=RequestFactory();anon=rf.get("/consent/");anon.user=SimpleNamespace(is_authenticated=False);resp=views.consent_view(anon);assert resp.status_code==401 and resp["Cache-Control"]=="no-store"
    anon_read=rf.post("/read/");anon_read.user=SimpleNamespace(is_authenticated=False)
    assert views.read_view(anon_read).status_code==401
    u=user();req=rf.post("/read/",data={});req.user=u
    resp=views.read_view(req);assert resp.status_code==403 and json.loads(resp.content)["code"]=="consent_required"
    grant(u)
    req=rf.post("/read/",data={});req.user=u
    resp=views.read_view(req);assert resp.status_code==400 and json.loads(resp.content)["code"]=="invalid_request"
    req=rf.post("/read/",data={"file":SimpleUploadedFile("whatever.jpg",image_bytes(),content_type="image/jpeg"),"context":"["});req.user=u
    resp=views.read_view(req);assert resp.status_code==400
    before=ReceiptConsent.objects.count()
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:fake_result(payload()))
    req=rf.post("/read/",data={"file":SimpleUploadedFile("x.jpg",image_bytes(),content_type="image/jpeg")});req.user=u
    resp=views.read_view(req);assert resp.status_code==200 and json.loads(resp.content)["receipts"] and ReceiptConsent.objects.count()==before

def test_fingerprint_and_default_price():
    a=Image.new("RGB",(20,20),"white");b=Image.new("RGB",(20,20),"white")
    for x in range(0,20,2):
        for y in range(20):a.putpixel((x,y),(0,0,0))
    for x in range(1,20,2):
        for y in range(20):b.putpixel((x,y),(0,0,0))
    ba=io.BytesIO();a.save(ba,format="PNG");bb=io.BytesIO();b.save(bb,format="PNG");a=ba.getvalue();b=bb.getvalue();ha=perceptual_hash(a);hb=perceptual_hash(b)
    assert ha==perceptual_hash(a) and ha!=hb and hamming_distance(ha,hb)>0 and not is_probable_duplicate(ha,hb)
    assert perceptual_hash(b"garbage")=="" and hamming_distance("",ha) is None
    assert price_usage("gpt-6-luna",input_tokens=1000,output_tokens=1000)>0



def test_unreadable_image_is_a_bad_file_not_a_provider_fault():
    with pytest.raises(ReceiptError) as exc:prepare(b"\xff\xd8\xff" + b"not really a jpeg")
    assert exc.value.code=="unsupported_file_type"


def test_rest_rejects_oversized_upload_before_reading_it(monkeypatch):
    u=user();grant(u)
    monkeypatch.setattr(views,"MAX_FILE_BYTES",10)
    def boom(*a,**k):raise AssertionError("must not be read")
    monkeypatch.setattr(reader,"read_receipts",boom)
    upload=SimpleUploadedFile("r.png",PNG+b"x"*50,content_type="image/png")
    req=RequestFactory().post("/read/",{"file":upload});req.user=u
    response=views.read_view(req)
    assert response.status_code==413 and json.loads(response.content)=={"code":"file_too_large"}


def test_daily_limit_counts_atomically_per_user_and_day():
    from django.core.cache import cache
    from django_core_micha.receipts.limits import check_and_increment
    cache.clear();u=user()
    with override_settings(DCM_RECEIPTS_DAILY_LIMIT=2):
        check_and_increment(u);check_and_increment(u)
        with pytest.raises(ReceiptError) as exc:check_and_increment(u)
    assert exc.value.code=="count_limit_reached"


def test_extension_normaliser_runs_but_cannot_overwrite_core_fields(monkeypatch):
    u=user();grant(u)
    def normalise(row,context):
        row["merchant"]="HACKED";row["total"]="999.00";row["note"]=f"{row['note']}-{context['k']}";return row
    ext=SimpleNamespace(properties={"note":{"type":"string"}},prompt="note",normalise=normalise)
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:fake_result(payload(note="n")))
    out=reader.read_receipts(u,image_bytes(),context={"k":"v"},extension=ext)
    assert out[0]["merchant"]=="Cafe" and out[0]["total"]=="12.50" and out[0]["note"]=="n-v"


def test_multi_receipt_boxes_survive_and_suggestions_need_the_hook(monkeypatch):
    u=user();grant(u)
    data={"receipts":[payload("A")["receipts"][0],payload("B",box={"x":0.5,"y":0.5,"w":0.4,"h":0.4})["receipts"][0]]}
    data["receipts"][0]["account_suggestions"]=[{"account_id":"7","confidence":"high"}]
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:fake_result(data))
    out=reader.read_receipts(u,image_bytes())
    assert out[0]["box"]=={"x":0.1,"y":0.2,"w":0.3,"h":0.4} and out[1]["box"]["x"]==0.5
    assert all("account_suggestions" not in row for row in out)  # no hook configured: nothing comes back whatever the provider said
    monkeypatch.setattr(reader,"get_accounts",lambda *a:None)  # a failing hook behaves the same
    assert all("account_suggestions" not in row for row in reader.read_receipts(u,image_bytes()))


def test_malformed_provider_payload_is_a_stable_error_not_a_crash(monkeypatch):
    u=user();grant(u)
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:fake_result({"receipts":[{"items":7}]}))
    with pytest.raises(ReceiptError) as e:reader.read_receipts(u,image_bytes())
    assert e.value.code=="provider_unavailable"


def test_revocation_while_the_file_is_prepared_stops_the_provider_call(monkeypatch):
    u=user();grant(u);calls=[]
    real=reader.prepare
    def prepare_then_revoke(content):
        out=real(content);revoke(u);return out
    monkeypatch.setattr(reader,"prepare",prepare_then_revoke)
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:calls.append("provider"))
    with pytest.raises(ReceiptError) as e:reader.read_receipts(u,image_bytes())
    assert e.value.code=="consent_required" and calls==[]


def test_huge_declared_canvas_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr("django_core_micha.receipts.prepare.MAX_PIXELS",100)
    with pytest.raises(ReceiptError) as e:prepare(image_bytes())  # 20x20 = 400 pixels
    assert e.value.code=="unsupported_file_type"


def test_rest_error_bodies_are_stable_redacted_and_nothing_is_stored(monkeypatch):
    from django.apps import apps
    u=user();grant(u);rf=RequestFactory()
    def post():
        req=rf.post("/read/",data={"file":SimpleUploadedFile("x.jpg",image_bytes(),content_type="image/jpeg")});req.user=u;return views.read_view(req)
    models=[m for m in apps.get_models() if m is not ReceiptConsent]
    before={m:m.objects.count() for m in models}
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:(_ for _ in ()).throw(AICostLimitExceeded(user_id=u.pk,used_cents=1,limit_cents=1)))
    resp=post();assert resp.status_code==429 and json.loads(resp.content)=={"code":"spend_cap_reached"}
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:(_ for _ in ()).throw(RuntimeError("sk-secret-detail")))
    resp=post();assert resp.status_code==503 and json.loads(resp.content)=={"code":"provider_unavailable"} and b"sk-secret" not in resp.content
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:fake_result({"receipts":[]}))
    resp=post();assert resp.status_code==422 and json.loads(resp.content)=={"code":"nothing_recognised"}
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:fake_result(payload()))
    assert post().status_code==200
    assert {m:m.objects.count() for m in models}==before


def test_no_store_header_also_on_method_not_allowed_and_declared_oversize():
    u=user();grant(u);rf=RequestFactory()
    req=rf.get("/read/");req.user=u
    resp=views.read_view(req);assert resp.status_code==405 and resp["Cache-Control"]=="no-store"
    req=rf.post("/read/",data={"file":SimpleUploadedFile("x.png",PNG)},CONTENT_LENGTH=str(MAX_FILE_BYTES+(2<<20)));req.user=u
    resp=views.read_view(req);assert resp.status_code==413 and resp["Cache-Control"]=="no-store"


def test_defaults_are_the_documented_central_model_and_priced():
    from django.conf import settings
    assert getattr(settings,"DCM_RECEIPTS_MODEL","gpt-6-luna")=="gpt-6-luna"
    captured={}
    u=user();grant(u)
    import django_core_micha.receipts.reader as r
    orig=r.extract_document
    r.extract_document=lambda req,**k:(captured.update(k),fake_result(payload()))[1]
    try:r.read_receipts(u,image_bytes())
    finally:r.extract_document=orig
    assert captured["model"]=="gpt-6-luna" and captured["provider"]=="openai" and captured["user_id"]==u.pk
    assert price_usage(captured["model"],input_tokens=1000,output_tokens=1000)>0


def test_misspelled_reader_setting_never_reaches_the_provider(monkeypatch):
    u=user();grant(u);calls=[]
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:calls.append("provider"))
    with override_settings(DCM_RECEIPTS_READER="faek"):
        with pytest.raises(ReceiptError) as e:reader.read_receipts(u,image_bytes())
    assert e.value.code=="provider_unavailable" and calls==[]


def test_system_check_refuses_fake_reader_in_production_and_unknown_values():
    from django.core.checks import run_checks
    ids=lambda: {m.id for m in run_checks()}
    with override_settings(DCM_RECEIPTS_READER="fake",ENV_TYPE="production"):assert "dcm_receipts.E001" in ids()
    with override_settings(DCM_RECEIPTS_READER="fake",ENV_TYPE="test"):assert "dcm_receipts.E001" not in ids()
    with override_settings(DCM_RECEIPTS_READER="faek"):assert "dcm_receipts.E002" in ids()


def test_extension_cannot_mutate_nested_core_values_and_lazy_hook_failure_is_soft(monkeypatch):
    u=user();grant(u)
    def normalise(row,context):
        row["box"]["x"]=0.99;return row
    ext=SimpleNamespace(properties={},prompt="",normalise=normalise)
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:fake_result(payload()))
    assert reader.read_receipts(u,image_bytes(),extension=ext)[0]["box"]["x"]==0.1
    def lazy(user,context):
        yield {"id":"1","name":"Meals"};raise RuntimeError("boom")
    with override_settings(DCM_RECEIPTS_ACCOUNTS_HOOK=lazy):
        assert reader.read_receipts(u,image_bytes())  # still reads, just without suggestions


def test_image_reaches_the_provider_resized_and_pdf_bytes_win_over_a_jpg_name(monkeypatch):
    u=user();grant(u);seen=[]
    monkeypatch.setattr(reader,"extract_document",lambda request,**kw:(seen.append(request) or fake_result(payload())))
    reader.read_receipts(u,image_bytes())
    assert seen[0].image_bytes.startswith(b"\xff\xd8\xff") and seen[0].image_mime_type=="image/jpeg"
    req=RequestFactory().post("/read/",data={"file":SimpleUploadedFile("receipt.jpg",b"%PDF-1.4 not really",content_type="image/jpeg")});req.user=u
    assert views.read_view(req).status_code==200 and seen[-1].upload_filename=="receipt.pdf"


def test_rejected_attempts_before_the_provider_do_not_consume_the_daily_count(monkeypatch):
    from django.core.cache import cache
    cache.clear();u=user();grant(u)
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:fake_result(payload()))
    with override_settings(DCM_RECEIPTS_DAILY_LIMIT=1):
        for bad in (b"",b"GIF89a-not-allowed"):
            with pytest.raises(ReceiptError):reader.read_receipts(u,bad)
        assert reader.read_receipts(u,image_bytes())


def test_fake_reader_totals_depend_on_the_content():
    from django_core_micha.receipts.fake import read
    with override_settings(ENV_TYPE="test"):
        totals={read(bytes([i])*8)["receipts"][0]["total"] for i in range(20)}
    assert len(totals)>1


def test_rest_consent_endpoints_grant_and_revoke():
    u=user();rf=RequestFactory()
    def call(method):
        req=getattr(rf,method)("/consent/");req.user=u;return json.loads(views.consent_view(req).content)
    assert call("get")["granted"] is False
    assert call("post")["granted"] is True and has_consent(u)
    assert call("delete")["granted"] is False and not has_consent(u)


def test_stray_provider_keys_are_dropped_and_missing_content_length_is_refused(monkeypatch):
    u=user();grant(u)
    monkeypatch.setattr(reader,"extract_document",lambda *a,**k:fake_result(payload(injected="do bad things")))
    assert "injected" not in reader.read_receipts(u,image_bytes())[0]
    req=RequestFactory().post("/read/",data={"file":SimpleUploadedFile("x.png",PNG)});req.user=u;req.META.pop("CONTENT_LENGTH",None)
    resp=views.read_view(req);assert resp.status_code==400 and json.loads(resp.content)=={"code":"invalid_request"}


def test_daily_count_uses_the_configured_local_day(monkeypatch):
    from django.core.cache import cache
    from django_core_micha.receipts import limits
    cache.clear();u=user();seen=[]
    monkeypatch.setattr(limits.cache,"add",lambda key,*a,**k:seen.append(key))
    monkeypatch.setattr(limits.cache,"incr",lambda key:1)
    monkeypatch.setattr(limits.timezone,"localdate",lambda:__import__("datetime").date(2026,1,2))
    with override_settings(DCM_RECEIPTS_DAILY_LIMIT=5):limits.check_and_increment(u)
    assert seen==[f"dcm_receipts_count:{u.pk}:2026-01-02"]
