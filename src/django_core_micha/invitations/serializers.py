from rest_framework import serializers

from rest_framework import serializers
from .models import AccessCode

class AccessCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccessCode
        fields = ("id", "code", "is_active", "created_at")
        read_only_fields = ("id", "created_at")

        

class InviteUserSerializer(serializers.Serializer):
    """Simple serializer holding the invite target email address."""
    email = serializers.EmailField()
