from .models import AssistantTurn
def build_history(chat):
    rows=[]
    for turn in chat.turns.filter(state="completed").order_by("sequence"):
        rows.extend(({"role":"user","content":turn.user_text},{"role":"assistant","content":turn.assistant_text}))
    return rows
