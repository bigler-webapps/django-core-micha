from django.db import models

from .crypto import decrypt_text, encrypt_text


class EncryptedTextField(models.TextField):
    """Text encrypted under the owning model's app ring before database writes.

    Values intentionally remain ciphertext when loaded from the ORM: decrypting
    requires an authenticated policy path, which model hydration cannot provide.
    Services use :func:`decrypt_text` only after that authorization check.
    """

    def __init__(self, *args, app_key_accessor: str, **kwargs):
        self.app_key_accessor = app_key_accessor
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["app_key_accessor"] = self.app_key_accessor
        return name, path, args, kwargs

    def pre_save(self, model_instance, add):
        value = super().pre_save(model_instance, add)
        if value is None:
            return value
        app_key = getattr(model_instance, self.app_key_accessor)
        try:
            decrypt_text(app_key=app_key, value=value)
        except ValueError:
            value = encrypt_text(app_key=app_key, value=value)
            setattr(model_instance, self.attname, value)
        return value
