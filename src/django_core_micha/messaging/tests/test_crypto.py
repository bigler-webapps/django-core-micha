import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from django_core_micha.messaging import crypto


@pytest.fixture(autouse=True)
def clear_keyring_registry():
    crypto._registered_apps.clear()
    crypto._build_multi_fernet.cache_clear()
    yield
    crypto._registered_apps.clear()
    crypto._build_multi_fernet.cache_clear()


def test_registration_fails_closed_for_missing_or_empty_ring():
    with override_settings(MESSAGING_KEYRINGS={}):
        with pytest.raises(ImproperlyConfigured):
            crypto.register_messaging_app("missing")
    with override_settings(MESSAGING_KEYRINGS={"empty": []}):
        with pytest.raises(ImproperlyConfigured):
            crypto.register_messaging_app("empty")


def test_registration_rejects_malformed_and_shared_rings():
    valid = Fernet.generate_key().decode()
    with override_settings(MESSAGING_KEYRINGS={"bad": ["not-a-fernet-key"]}):
        with pytest.raises(ImproperlyConfigured):
            crypto.register_messaging_app("bad")
    with override_settings(MESSAGING_KEYRINGS={"one": [valid], "two": [valid]}):
        with pytest.raises(ImproperlyConfigured):
            crypto.register_messaging_app("one")


def test_rotation_round_trip_accepts_retired_key_but_encrypts_with_new_primary():
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()
    with override_settings(MESSAGING_KEYRINGS={"app": [old_key]}):
        crypto.register_messaging_app("app")
        old_ciphertext = crypto.encrypt_text(app_key="app", value="secret")

    crypto._build_multi_fernet.cache_clear()
    with override_settings(MESSAGING_KEYRINGS={"app": [new_key, old_key]}):
        assert crypto.decrypt_text(app_key="app", value=old_ciphertext) == "secret"
        rotated = crypto.encrypt_text(app_key="app", value="secret")
        assert Fernet(new_key.encode()).decrypt(rotated.encode()) == b"secret"


def test_decrypt_never_falls_back_to_plaintext():
    with override_settings(MESSAGING_KEYRINGS={"app": [Fernet.generate_key().decode()]}):
        crypto.register_messaging_app("app")
        with pytest.raises(ValueError):
            crypto.decrypt_text(app_key="app", value="not ciphertext")
