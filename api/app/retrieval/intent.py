"""Query intent helpers for deciding when retrieval should be skipped."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from app.models.schemas import ChatMessage
from app.retrieval.followup import is_followup_like, latest_user_question

# Phrases that should stay conversational and avoid retrieval/grounding checks.
_SMALLTALK_PATTERNS = [
    re.compile(r"^(ok|okay|okey|vale|listo|perfecto|genial|de acuerdo|entendido|noted|got it)$", re.IGNORECASE),
    re.compile(r"^(thanks|thank you|gracias|muchas gracias|thx)[!. ]*$", re.IGNORECASE),
    re.compile(r"^(sounds good|suena bien|todo bien|all good)[!. ]*$", re.IGNORECASE),
    re.compile(r"^(sounds?|suena)\s+\w+[!. ]*$", re.IGNORECASE),
    re.compile(r"^(hola|hello|hi|buenas|good morning|good afternoon|good evening)[!. ]*$", re.IGNORECASE),
    re.compile(r"^(que tal|como estas|how are you)[?.! ]*$", re.IGNORECASE),
]

_RAG_STRONG_TRIGGERS = {
    "summarize",
    "summary",
    "resumen",
    "resume",
    "explain",
    "define",
    "compare",
    "list",
    "show",
    "find",
    "search",
    "document",
    "documents",
    "citation",
    "citations",
    "evidence",
    "policy",
    "pdf",
    "xlsx",
    "docx",
    "txt",
    "md",
    "workbook",
    "spreadsheet",
    "sheet",
    "drive",
    "archivo",
    "documento",
    "doc",
    "docs",
}

_DOC_REFERENCE_PATTERNS = [
    re.compile(r"\b[a-zA-Z0-9_.\-]+\.(pdf|docx|txt|md|xlsx)\b", re.IGNORECASE),
    re.compile(r"\b(this|that|the)\s+(document|doc|file|paper|pdf|workbook|spreadsheet|sheet)\b", re.IGNORECASE),
    re.compile(r"\b(este|esta|ese|esa)\s+(documento|archivo|fichero|pdf|workbook|spreadsheet|sheet)\b", re.IGNORECASE),
]

_RAG_QUESTION_PATTERNS = [
    re.compile(r"^(what|who|where|when|why|how)\b", re.IGNORECASE),
    re.compile(r"^(que|qué|quien|quién|donde|dónde|cuando|cuándo|como|cómo)\b", re.IGNORECASE),
]

_CHAT_TASK_PATTERNS = [
    re.compile(r"\b(i need|help me|can you help|could you help|i want to|i'm trying to)\b", re.IGNORECASE),
    re.compile(r"\b(necesito|ayudame|ayúdame|me ayudas|quiero|estoy intentando)\b", re.IGNORECASE),
    re.compile(r"\b(research|investigat|brainstorm|plan|roadmap|strategy|approach|recommend)\b", re.IGNORECASE),
    re.compile(r"\b(investigar|investigacion|investigación|plan|roadmap|estrategia|enfoque|recomi)\b", re.IGNORECASE),
]

_GENERIC_CHAT_QUESTION_PATTERNS = [
    re.compile(r"^(what do you recommend|what should i do|where should i start)\b", re.IGNORECASE),
    re.compile(r"^(que me recomiendas|qué me recomiendas|que hago ahora|qué hago ahora|por donde empiezo|por dónde empiezo)\b", re.IGNORECASE),
]

_ACK_TOKENS = {
    "ok",
    "okay",
    "vale",
    "listo",
    "perfect",
    "perfecto",
    "genial",
    "great",
    "nice",
    "thanks",
    "thank",
    "gracias",
    "cool",
    "sounds",
    "sound",
    "suena",
}


@dataclass(frozen=True)
class AutoRetrievalDecision:
    mode: Literal["chat", "rag"]
    reason: str


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip()).strip()


def _tokenize(normalized_query: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_]+", normalized_query.lower())


def _matches_any(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _looks_like_acknowledgement(tokens: list[str]) -> bool:
    if not tokens:
        return True

    for token in tokens:
        if token in _ACK_TOKENS:
            return True
        if token.startswith("thank") or token.startswith("graci"):
            return True
        if token.startswith("sound") or token.startswith("suen"):
            return True

    return False


def _is_smalltalk_message(normalized_query: str) -> bool:
    lowered = normalized_query.lower()

    for pattern in _SMALLTALK_PATTERNS:
        if pattern.match(lowered):
            return True

    if lowered.startswith(("ok ", "okay ", "vale ", "perfecto ", "genial ", "thanks ", "gracias ")):
        return True

    if lowered.endswith("!") and len(lowered.split()) <= 4 and "?" not in lowered:
        short = lowered.rstrip("!").strip()
        if short in {"great", "genial", "perfect", "perfecto", "nice", "gracias", "thanks"}:
            return True

    return False


def _looks_like_rag_request(normalized_query: str, tokens: list[str]) -> bool:
    lowered = normalized_query.lower()

    if _matches_any(_DOC_REFERENCE_PATTERNS, lowered):
        return True

    if tokens and any(token in _RAG_STRONG_TRIGGERS for token in tokens):
        return True

    if _matches_any(_RAG_QUESTION_PATTERNS, lowered) and len(tokens) >= 4:
        return True

    if "?" in lowered and len(tokens) >= 4 and not _matches_any(_GENERIC_CHAT_QUESTION_PATTERNS, lowered):
        return True

    return False


def _looks_like_chat_task(normalized_query: str, tokens: list[str]) -> bool:
    lowered = normalized_query.lower()

    if _matches_any(_GENERIC_CHAT_QUESTION_PATTERNS, lowered):
        return True

    if _matches_any(_CHAT_TASK_PATTERNS, lowered):
        return True

    if len(tokens) <= 6 and _looks_like_acknowledgement(tokens):
        return True

    return False


def decide_auto_retrieval_mode(
    query: str,
    chat_history: list[ChatMessage | dict[str, str]] | None = None,
) -> AutoRetrievalDecision:
    """Choose whether auto mode should behave as chat or RAG."""

    normalized = _normalize_query(query)
    if not normalized:
        return AutoRetrievalDecision(mode="chat", reason="empty_query")

    if _is_smalltalk_message(normalized):
        return AutoRetrievalDecision(mode="chat", reason="smalltalk")

    tokens = _tokenize(normalized)

    if is_followup_like(normalized):
        previous_question = latest_user_question(chat_history or [], exclude_text=query)
        if previous_question:
            previous_decision = decide_auto_retrieval_mode(previous_question, [])
            return AutoRetrievalDecision(
                mode=previous_decision.mode,
                reason=f"followup_{previous_decision.mode}",
            )

    if _looks_like_rag_request(normalized, tokens):
        return AutoRetrievalDecision(mode="rag", reason="knowledge_request")

    if _looks_like_chat_task(normalized, tokens):
        return AutoRetrievalDecision(mode="chat", reason="general_assistance")

    return AutoRetrievalDecision(mode="chat", reason="default_chat")


def is_non_rag_chat_message(query: str) -> bool:
    """Return True when query looks like a conversational turn, not a knowledge request."""

    return decide_auto_retrieval_mode(query).mode == "chat"


def build_smalltalk_response(query: str, *, chat_mode: bool = False) -> str:
    """Generate a concise assistant response for conversational turns."""

    lowered = (query or "").strip().lower()

    if any(word in lowered for word in ["thanks", "thank", "gracias"]):
        if chat_mode:
            return "De nada. Si quieres, seguimos la conversacion."
        return "You are welcome. When you want, ask a document question and I will answer with citations."

    if any(word in lowered for word in ["hola", "hello", "hi", "buenas"]):
        if chat_mode:
            return "Hola, que tal? En que te ayudo?"
        return "Hello. Ask me about your indexed documents when you are ready."

    if any(word in lowered for word in ["que tal", "como estas", "how are you"]):
        if chat_mode:
            return "Todo bien por aqui. Como estas tu?"
        return "Hello. Ask me about your indexed documents when you are ready."

    if chat_mode:
        if any(word in lowered for word in ["investig", "research", "plan", "recom", "ayud", "help"]):
            return "Puedo ayudarte con eso. Si quieres, dime el objetivo y te propongo un enfoque concreto."
        return "Te sigo. Si quieres, continua con mas detalle o haz una pregunta concreta."

    return "Understood. When you want evidence from documents, ask a question and I will answer with citations."
