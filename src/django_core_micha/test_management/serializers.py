from rest_framework import serializers


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("must be an object")
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError({key: "unknown field" for key in sorted(unknown)})
        return super().to_internal_value(data)


class JsonObjectField(serializers.JSONField):
    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        if not isinstance(value, dict):
            raise serializers.ValidationError("must be an object")
        return value


class RunCreateSerializer(StrictSerializer):
    idempotency_key = serializers.CharField(max_length=200)
    baseline = JsonObjectField(required=False, default=dict)


class AccountCreateSerializer(StrictSerializer):
    idempotency_key = serializers.CharField(max_length=200)
    template = serializers.CharField(max_length=100)
    synthetic_identity = JsonObjectField()
    password = serializers.CharField(write_only=True, min_length=12, max_length=256)
    configuration = JsonObjectField(required=False, default=dict)


class AccountUpdateSerializer(StrictSerializer):
    configuration = JsonObjectField(required=False)
    template = serializers.CharField(max_length=100, required=False)
    password = serializers.CharField(write_only=True, min_length=12, max_length=256, required=False)


class FixtureCreateSerializer(StrictSerializer):
    idempotency_key = serializers.CharField(max_length=200)
    name = serializers.CharField(max_length=100)
    parameters = JsonObjectField(required=False, default=dict)
