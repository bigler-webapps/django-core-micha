from .models import AssistantTurn

def build_history(chat):
    rows=[]
    summary = chat.summaries.order_by("-up_to_sequence").first()
    turns = chat.turns.filter(state="completed")
    if summary:
        turns = turns.filter(sequence__gt=summary.up_to_sequence)
    for turn in turns.order_by("sequence"):
        rows.extend(({"role":"user","content":turn.user_text},{"role":"assistant","content":turn.assistant_text}))
    return rows

def latest_summary(chat):
    return chat.summaries.order_by("-up_to_sequence").first()

def summary_payload(chat):
    summary = latest_summary(chat)
    if not summary:
        return None
    return {"up_to_seq": summary.up_to_sequence, "text": summary.text,
            "created_at": summary.created_at.isoformat()}
