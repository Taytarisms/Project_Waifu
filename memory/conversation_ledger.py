import uuid


def clean(value):
    return str(value or "").replace("\x00", "").strip()


def public_exchange(turn, assistant_name="Assistant"):
    user, assistant = clean(turn.get("user")), clean(turn.get("assistant"))
    if not user or not assistant:
        return None
    return {"id": clean(turn.get("id")) or str(uuid.uuid4()),
            "speaker": clean(turn.get("speaker")) or "User",
            "assistant_speaker": clean(turn.get("assistant_speaker")) or assistant_name or "Assistant",
            "user": user, "assistant": assistant,
            **{key: clean(turn.get(key)) for key in
               ("source", "provider", "model", "session_id", "created_at")}}


def exchange_messages(turn):
    speaker = turn.get("speaker") or "User"
    text = turn["user"]
    if speaker != "User" and not text.casefold().startswith(f"{speaker}:".casefold()):
        text = f"{speaker}: {text}"
    return [{"role": "user", "content": text},
            {"role": "assistant", "content": turn["assistant"]}]


def render_exchange(turn):
    user, assistant = exchange_messages(turn)
    return f"user: {user['content']}\n{turn.get('assistant_speaker') or 'Assistant'}: {assistant['content']}"
