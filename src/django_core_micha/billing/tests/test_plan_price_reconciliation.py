from types import SimpleNamespace

import pytest
from django.core.management import call_command, CommandError
from django.test import override_settings

from django_core_micha.billing import stripe_service
from django_core_micha.billing.models import StripePrice

pytestmark = pytest.mark.django_db


class Page:
    def __init__(self, rows):
        self.data = rows

    def auto_paging_iter(self):
        return iter(self.data)


class FakeStripe:
    def __init__(self):
        self.products, self.prices = [], []
        self.product_creates, self.price_creates = [], []
        self.product_keys, self.price_keys = {}, {}
        self.Product = SimpleNamespace(list=lambda **kw: Page(self.products), retrieve=self.product,
                                       create=self.create_product, modify=lambda *a, **kw: None)
        self.Price = SimpleNamespace(list=self.list_prices, retrieve=self.price,
                                     create=self.create_price)

    @staticmethod
    def _missing():
        import stripe
        return stripe.error.InvalidRequestError("missing", None, code="resource_missing", http_status=404)

    def product(self, product_id):
        for item in self.products:
            if item.id == product_id:
                return item
        raise self._missing()

    def price(self, price_id):
        for item in self.prices:
            if item.id == price_id:
                return item
        raise self._missing()

    def create_product(self, **kw):
        key = kw.get("idempotency_key")
        if key and key in self.product_keys:
            return self.product_keys[key]
        row = SimpleNamespace(id=f"prod_{len(self.product_creates) + 1}", metadata=kw["metadata"],
                              tax_code=kw["tax_code"], active=True, deleted=False)
        self.products.append(row)
        self.product_creates.append(kw)
        if key:
            self.product_keys[key] = row
        return row

    def list_prices(self, **kw):
        keys = kw.get("lookup_keys", [])
        return Page([p for p in self.prices if p.lookup_key in keys])

    def create_price(self, **kw):
        key = kw.get("idempotency_key")
        if key and key in self.price_keys:
            return self.price_keys[key]
        row = SimpleNamespace(id=f"price_{len(self.prices) + 1}", product=kw["product"],
            lookup_key=kw["lookup_key"], unit_amount=kw["unit_amount"], currency=kw["currency"],
            recurring=kw.get("recurring"), tax_behavior=kw["tax_behavior"], metadata=kw["metadata"],
            active=True)
        self.prices.append(row)
        self.price_creates.append(kw)
        if key:
            self.price_keys[key] = row
        return row


def config(tmp_path, *, app="maana", amount=1500, currency="usd", interval="month"):
    import yaml
    path = tmp_path / f"{app}.yaml"
    plan = {"key": "monthly", "kind": "subscription", "name": "Monthly", "amount_cents": amount,
            "interval": interval, "token_allowance": 10, "tax_code": "txcd_10103001"}
    path.write_text(yaml.safe_dump({"app": app, "currency": currency, "plans": [plan],
                                    "token_rates": {"chat": 2}}), encoding="utf-8")
    return path


def configured(tmp_path, monkeypatch, *, app="maana", amount=1500, currency="usd", interval="month"):
    path = config(tmp_path, app=app, amount=amount, currency=currency, interval=interval)
    fake = FakeStripe()
    monkeypatch.setattr(stripe_service, "_stripe", lambda: fake)
    return path, fake


def add_remote(fake, *, app="maana", plan="monthly", version=1, price_id="price_1", amount=1500,
               currency="usd", interval="month", tax_code="txcd_10103001", active=True,
               product_active=True, price_metadata=None, product_metadata=None, tax_behavior="exclusive"):
    lookup = f"{app}:{plan}:v{version}"
    product = SimpleNamespace(id=f"prod_{app}_{plan}_{version}", active=product_active, deleted=False,
        metadata=product_metadata or {"app": app, "plan_key": plan}, tax_code=tax_code)
    remote = SimpleNamespace(id=price_id, product=product.id, lookup_key=lookup, unit_amount=amount,
        currency=currency, recurring={"interval": interval}, tax_behavior=tax_behavior,
        metadata=price_metadata or {"app": app, "plan_key": plan, "version": str(version)}, active=active)
    fake.products.append(product)
    fake.prices.append(remote)
    return product, remote


def map_price(*, app="maana", plan="monthly", version=1, price_id="price_1", product_id="prod_maana_monthly_1",
              fingerprint=None):
    row = StripePrice.objects.create(app=app, plan_key=plan, version=version,
        lookup_key=f"{app}:{plan}:v{version}", fingerprint=fingerprint or stripe_service.stripe_fingerprint(
            SimpleNamespace(amount_cents=1500, interval="month", kind="subscription", tax_code="txcd_10103001"), "usd"),
        stripe_product_id=product_id, stripe_price_id=price_id)
    return row


def use_config(app, path):
    return override_settings(DCM_BILLING_APP=app, DCM_BILLING_FILE=path)


def test_matching_fingerprint_retrieves_and_verifies_current_price(tmp_path, monkeypatch):
    path, fake = configured(tmp_path, monkeypatch)
    add_remote(fake)
    row = map_price()
    retrieved = []
    original_retrieve = fake.Price.retrieve
    def retrieve(price_id):
        retrieved.append(price_id)
        return original_retrieve(price_id)
    fake.Price.retrieve = retrieve
    with use_config("maana", path):
        assert stripe_service.sync_plans() == [("monthly", "unchanged")]
    assert retrieved == ["price_1"]
    row.refresh_from_db()
    assert row.stripe_price_id == "price_1"
    assert fake.price_creates == []


def test_missing_current_price_repairs_from_matching_lookup_and_checkout_uses_it(tmp_path, monkeypatch):
    path, fake = configured(tmp_path, monkeypatch)
    add_remote(fake, price_id="active_account_price")
    row = map_price(price_id="stale_account_price")
    with use_config("maana", path):
        assert stripe_service.sync_plans() == [("monthly", "repaired:v1")]
        assert stripe_service._latest_price("monthly").stripe_price_id == "active_account_price"
        from django_core_micha.billing.config import load_billing_config
        user = SimpleNamespace(pk=7)
        checkout = stripe_service.checkout_session_params(plan=load_billing_config().plans[0],
            price=stripe_service._latest_price("monthly"), user=user, customer_id="cus_1")
    row.refresh_from_db()
    assert row.stripe_price_id == "active_account_price"
    assert checkout["line_items"] == [{"price": "active_account_price", "quantity": 1}]
    assert fake.price_creates == []


def test_missing_price_and_product_are_created_once_and_repair_same_version(tmp_path, monkeypatch):
    path, fake = configured(tmp_path, monkeypatch)
    row = map_price(price_id="old_price", product_id="old_product")
    with use_config("maana", path):
        assert stripe_service.sync_plans() == [("monthly", "repaired:v1")]
        repaired_id = StripePrice.objects.get(pk=row.pk).stripe_price_id
        assert stripe_service.sync_plans() == [("monthly", "unchanged")]
    row.refresh_from_db()
    assert row.stripe_price_id == repaired_id
    assert len(fake.product_creates) == len(fake.price_creates) == 1


def test_retry_never_binds_price_returned_with_mismatched_semantics(tmp_path, monkeypatch):
    path, fake = configured(tmp_path, monkeypatch)
    row = map_price(price_id="missing_price", product_id="missing_product")
    original_create = fake.Price.create

    def create_mismatched(**kw):
        remote = original_create(**kw)
        remote.unit_amount += 1
        return remote

    fake.Price.create = create_mismatched
    with use_config("maana", path), pytest.raises(stripe_service.PlanSyncError):
        stripe_service.sync_plans()
    row.refresh_from_db()
    assert row.stripe_price_id == "missing_price"
    with use_config("maana", path), pytest.raises(stripe_service.PlanSyncError):
        stripe_service.sync_plans()
    row.refresh_from_db()
    assert row.stripe_price_id == "missing_price"
    assert len(fake.product_creates) == len(fake.price_creates) == 1


@pytest.mark.parametrize("mutation", [
    lambda p, pr: setattr(pr, "unit_amount", 2000),
    lambda p, pr: setattr(pr, "currency", "eur"),
    lambda p, pr: setattr(pr, "recurring", {"interval": "year"}),
    lambda p, pr: setattr(pr, "recurring", {"interval": "month", "interval_count": 2}),
    lambda p, pr: setattr(pr, "recurring", {"interval": "month", "usage_type": "metered"}),
    lambda p, pr: setattr(pr, "tax_behavior", "inclusive"),
    lambda p, pr: setattr(p, "tax_code", "wrong"),
    lambda p, pr: setattr(p, "metadata", {"app": "maana-old", "plan_key": "monthly"}),
    lambda p, pr: setattr(pr, "metadata", {"app": "maana", "plan_key": "another", "version": "1"}),
    lambda p, pr: setattr(pr, "active", False),
    lambda p, pr: setattr(p, "active", False),
])
def test_incompatible_current_price_fails_without_mapping_change(tmp_path, monkeypatch, mutation):
    path, fake = configured(tmp_path, monkeypatch)
    product, remote = add_remote(fake)
    mutation(product, remote)
    row = map_price()
    before = (row.stripe_product_id, row.stripe_price_id, row.fingerprint)
    with use_config("maana", path), pytest.raises(stripe_service.PlanSyncError):
        stripe_service.sync_plans()
    row.refresh_from_db()
    assert (row.stripe_product_id, row.stripe_price_id, row.fingerprint) == before
    assert fake.price_creates == []


def test_changed_fingerprint_versions_without_overwriting_history(tmp_path, monkeypatch):
    path, fake = configured(tmp_path, monkeypatch)
    add_remote(fake)
    old = map_price()
    path.write_text(path.read_text(encoding="utf-8").replace("1500", "1800"), encoding="utf-8")
    with use_config("maana", path):
        assert stripe_service.sync_plans() == [("monthly", "created:v2")]
    assert list(StripePrice.objects.order_by("version").values_list("version", "stripe_price_id")) == [
        (1, "price_1"), (2, "price_2")]
    old.refresh_from_db()
    assert old.lookup_key == "maana:monthly:v1"
    assert fake.price_creates[0]["lookup_key"] == "maana:monthly:v2"


def test_similar_app_names_are_isolated(tmp_path, monkeypatch):
    path, fake = configured(tmp_path, monkeypatch, app="maana")
    add_remote(fake, app="maana-old")
    with use_config("maana", path):
        assert stripe_service.sync_plans() == [("monthly", "created:v1")]
    assert len(fake.products) == 2
    assert StripePrice.objects.get().app == "maana"


def test_dry_run_has_no_stripe_or_database_writes(tmp_path, monkeypatch):
    path, fake = configured(tmp_path, monkeypatch)
    with use_config("maana", path):
        assert stripe_service.sync_plans(dry_run=True) == [("monthly", "would_sync")]
    assert not fake.product_creates and not fake.price_creates
    assert not StripePrice.objects.exists()


@pytest.mark.parametrize("error_kind", ["authentication", "rate_limit", "connection", "api"])
def test_provider_errors_do_not_replace_or_rewrite_mapping(tmp_path, monkeypatch, error_kind):
    import stripe
    errors = {"authentication": stripe.error.AuthenticationError("denied", http_status=401),
              "rate_limit": stripe.error.RateLimitError("limited", http_status=429),
              "connection": stripe.error.APIConnectionError("timeout"),
              "api": stripe.error.APIError("temporary", http_status=500)}
    error = errors[error_kind]
    path, fake = configured(tmp_path, monkeypatch)
    add_remote(fake)
    row = map_price(price_id="price_1")
    fake.Price.retrieve = lambda _pid: (_ for _ in ()).throw(error)
    with use_config("maana", path), pytest.raises(stripe_service.PlanSyncError):
        stripe_service.sync_plans()
    row.refresh_from_db()
    assert row.stripe_price_id == "price_1"
    assert not fake.product_creates and not fake.price_creates


def test_command_reports_repair_and_rejects_wrong_amount(tmp_path, monkeypatch):
    path, fake = configured(tmp_path, monkeypatch)
    add_remote(fake, price_id="active_price")
    map_price(price_id="stale_price")
    with use_config("maana", path):
        from io import StringIO
        out = StringIO()
        call_command("billing_sync_plans", stdout=out)
        assert "monthly: repaired:v1" in out.getvalue()
        fake.prices[0].unit_amount = 1700
        with pytest.raises(CommandError, match="monthly: price amount does not match plan"):
            call_command("billing_sync_plans")


@pytest.mark.parametrize("changed", [False, True])
def test_inactive_matching_product_never_triggers_replacement(tmp_path, monkeypatch, changed):
    path, fake = configured(tmp_path, monkeypatch)
    product, _ = add_remote(fake)
    row = map_price(price_id="missing")
    product.active = False
    if changed:
        path.write_text(path.read_text(encoding="utf-8").replace("1500", "1800"), encoding="utf-8")
    else:
        fake.prices.clear()
    with use_config("maana", path), pytest.raises(stripe_service.PlanSyncError, match="inactive"):
        stripe_service.sync_plans()
    row.refresh_from_db()
    assert row.stripe_price_id == "missing"
    assert not fake.product_creates and not fake.price_creates


def test_command_reports_prior_success_when_later_plan_fails(tmp_path, monkeypatch):
    from io import StringIO
    import yaml

    path, fake = configured(tmp_path, monkeypatch)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    second = dict(data["plans"][0], key="second")
    data["plans"].append(second)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    product, price = add_remote(fake, plan="second")
    price.unit_amount = 1
    out = StringIO()
    with use_config("maana", path), pytest.raises(CommandError, match="second"):
        call_command("billing_sync_plans", stdout=out)
    assert "monthly: created:v1" in out.getvalue()
    assert StripePrice.objects.filter(plan_key="monthly").exists()


def test_repair_ignores_known_historical_tax_product(tmp_path, monkeypatch):
    path, fake = configured(tmp_path, monkeypatch)
    old_product, _ = add_remote(fake, version=1, tax_code="old_tax")
    map_price(version=1, product_id=old_product.id, fingerprint="old")
    current_product, _ = add_remote(fake, version=2)
    current = map_price(version=2, price_id="missing", product_id=current_product.id)
    fake.prices.clear()
    with use_config("maana", path):
        assert stripe_service.sync_plans() == [("monthly", "repaired:v2")]
    current.refresh_from_db()
    assert current.stripe_product_id == current_product.id
    assert not fake.product_creates

