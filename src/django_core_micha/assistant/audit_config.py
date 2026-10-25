from django_core_micha.auditlog import register
from .models import AssistantChat, AssistantConfig, AssistantSummary, AssistantTurn

register(AssistantChat, redact_fields=("title",))
register(AssistantTurn, redact_fields=("user_text", "assistant_text", "tool_trace", "usage"))
register(AssistantSummary, redact_fields=("text", "usage"))
register(AssistantConfig)
