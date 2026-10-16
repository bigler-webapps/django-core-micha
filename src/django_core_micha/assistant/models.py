import uuid
from django.conf import settings
from django.db import models

class AssistantChat(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dcm_assistant_chats")
    scope = models.CharField(max_length=255, blank=True, default="", db_index=True)
    title = models.CharField(max_length=200, default="New chat")
    title_source = models.CharField(max_length=12, choices=(("automatic", "automatic"), ("user", "user")), default="automatic")
    archived_at = models.DateTimeField(null=True, blank=True)
    selected_driver = models.CharField(max_length=32, blank=True, default="")
    selected_model = models.CharField(max_length=128, blank=True, default="")
    selected_effort = models.CharField(max_length=12, blank=True, default="medium")
    session_driver = models.CharField(max_length=32, blank=True, default="")
    session_reference = models.CharField(max_length=255, blank=True, default="")
    session_tokens = models.PositiveIntegerField(default=0)
    session_prompt_version = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class AssistantTurn(models.Model):
    STATES = tuple((x,x) for x in ("generating","completed","failed","cancelled","interrupted"))
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey(AssistantChat, on_delete=models.CASCADE, related_name="turns")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    sequence = models.PositiveIntegerField()
    user_text = models.TextField()
    assistant_text = models.TextField(blank=True)
    state = models.CharField(max_length=16, choices=STATES, default="generating")
    driver = models.CharField(max_length=32, blank=True)
    model = models.CharField(max_length=128, blank=True)
    effort = models.CharField(max_length=12, blank=True)
    billing_source = models.CharField(max_length=24, default="deployment")
    usage = models.JSONField(default=dict, blank=True)
    cost_usd_micros = models.BigIntegerField(default=0)
    context_tokens = models.PositiveIntegerField(default=0)
    tool_trace = models.JSONField(default=list, blank=True)
    error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=("chat","sequence"), name="dcm_asst_chat_seq_unique"), models.UniqueConstraint(fields=("chat",), condition=models.Q(state="generating"), name="dcm_asst_one_generating")]
        indexes = [models.Index(fields=("chat", "-sequence"), name="dcm_asst_turn_seq_idx")]

class AssistantSummary(models.Model):
    chat = models.ForeignKey(AssistantChat, on_delete=models.CASCADE, related_name="summaries")
    up_to_sequence = models.PositiveIntegerField()
    text = models.TextField()
    usage = models.JSONField(default=dict, blank=True)
    cost_usd_micros = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("chat", "up_to_sequence"), name="dcm_asst_summary_range_unique")]
        indexes = [models.Index(fields=("chat", "-up_to_sequence"), name="dcm_asst_summary_latest_idx")]
