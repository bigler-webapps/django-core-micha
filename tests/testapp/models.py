from django.contrib.auth import get_user_model
from django.db import models


class Widget(models.Model):
    name = models.CharField(max_length=100)
    secret = models.CharField(max_length=100, blank=True)
    updated_by = models.ForeignKey(
        get_user_model(),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        app_label = "testapp"


class Gadget(models.Model):
    title = models.CharField(max_length=100)

    class Meta:
        app_label = "testapp"
