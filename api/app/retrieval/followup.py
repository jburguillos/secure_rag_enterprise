"""Follow-up query helpers for conversational RAG flows."""

from __future__ import annotations

from collections.abc import Sequence

from app.models.schemas import ChatMessage

_FOLLOWUP_PREFIXES = (
    "and ",
    "also ",
    "what about",
    "how about",
    "ok, and",
    "okay, and",
    "y ",
    "tambien ",
    "también ",
    "ademas ",
    "además ",
    "sobre eso",
    "sobre eso,",
)

_FOLLOWUP_SHORT_FORMS = {
    "and?",
    "also?",
    "y?",
    "tambien?",
    "también?",
    "more",
    "more?",
    "mas",
    "más",
}

_FOLLOWUP_REFERENCE_PATTERNS = (
    " it ",
    " this ",
    " that ",
    " those ",
    " these ",
    " them ",
    " this one",
    " that one",
    " same one",
    " same file",
    " same doc",
    " same document",
    " same workbook",
    " same sheet",
    " esto ",
    " eso ",
    " esta ",
    " este ",
    " esa ",
    " ese ",
    " mismo archivo",
    " mismo documento",
    " mismo fichero",
    " misma hoja",
    " misma planilla",
    " mismo workbook",
    " mismo sheet",
)


def is_followup_like(query: str) -> bool:
    """Return True when the query looks like a follow-up that depends on prior turns."""

    text = (query or "").strip().lower()
    if not text:
        return False

    if text in _FOLLOWUP_SHORT_FORMS:
        return True

    # Detect short anaphoric references like "tell me about it/that one",
    # which should reuse prior user turn context.
    padded = f" {text} "
    if len(text.split()) <= 16 and any(pattern in padded for pattern in _FOLLOWUP_REFERENCE_PATTERNS):
        return True

    return any(text.startswith(prefix) for prefix in _FOLLOWUP_PREFIXES)


def _to_chat_message(item: ChatMessage | dict[str, str]) -> ChatMessage | None:
    if isinstance(item, ChatMessage):
        return item

    if not isinstance(item, dict):
        return None

    role = str(item.get("role") or "").strip().lower()
    content = str(item.get("content") or "").strip()
    if role not in {"user", "assistant", "system"}:
        return None
    if not content:
        return None
    return ChatMessage(role=role, content=content)


def latest_user_question(history: Sequence[ChatMessage | dict[str, str]], *, exclude_text: str | None = None) -> str | None:
    """Return the latest prior user question from chat history."""

    exclude = (exclude_text or "").strip().lower()
    for item in reversed(history):
        message = _to_chat_message(item)
        if not message or message.role != "user":
            continue

        content = message.content.strip()
        if not content:
            continue

        if exclude and content.lower() == exclude:
            continue

        return content

    return None


def maybe_rewrite_followup(query: str, history: Sequence[ChatMessage | dict[str, str]]) -> tuple[str, bool]:
    """Rewrite follow-up queries into standalone form using prior user turn."""

    if not is_followup_like(query):
        return query, False

    previous_question = latest_user_question(history, exclude_text=query)
    if not previous_question:
        return query, False

    rewritten = f"{previous_question}\nFollow-up: {query.strip()}"
    return rewritten, True
