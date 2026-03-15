"""Answerability gate for grounded RAG responses."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re

from app.config import get_settings
from app.generation.ollama_client import OllamaClient
from app.models.schemas import Citation
from app.retrieval.qdrant_service import RetrievedNode

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "make",
    "me",
    "need",
    "of",
    "on",
    "or",
    "plan",
    "por",
    "que",
    "quiero",
    "research",
    "should",
    "sobre",
    "the",
    "to",
    "un",
    "una",
    "what",
    "with",
    "y",
}

_DOC_DISCOVERY_TERMS = {
    "paper",
    "papers",
    "research",
    "study",
    "studies",
    "doc",
    "docs",
    "document",
    "documents",
    "archivo",
    "archivos",
    "documento",
    "documentos",
    "articulo",
    "articulos",
    "artículo",
    "artículos",
    "spreadsheet",
    "spreadsheets",
    "excel",
    "xlsx",
    "sheet",
    "sheets",
    "workbook",
    "workbooks",
}

_QUERY_EXPANSIONS = {
    "vc": ["venture", "capital"],
}

_MENTION_TERMS = {
    "mention",
    "mentions",
    "mentioned",
    "contain",
    "contains",
    "contained",
    "include",
    "includes",
    "including",
    "menciona",
    "menciona",
    "mencionan",
    "contiene",
    "incluye",
}

_HEURISTIC_PRIORITY_REASONS = {
    "summary_supported",
    "document_discovery_supported",
    "broad_query_supported",
}


def _looks_like_mention_query(query: str) -> bool:
    lowered = (query or "").lower()
    return any(term in lowered for term in _MENTION_TERMS)


@dataclass(frozen=True)
class AnswerabilityDecision:
    answerable: bool
    reason: str
    support_indices: list[int] = field(default_factory=list)
    source: str = "heuristic"


def _meaningful_query_terms(query: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN_RE.finditer((query or "").lower()):
        token = match.group(0)
        if len(token) < 3 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
        for expanded in _QUERY_EXPANSIONS.get(token, []):
            if expanded in _STOPWORDS or expanded in seen:
                continue
            seen.add(expanded)
            terms.append(expanded)
    return terms


def _raw_query_markers(query: str) -> list[str]:
    markers: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN_RE.finditer((query or "").lower()):
        token = match.group(0)
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        markers.append(token)
        if token.endswith("s") and len(token) > 3:
            singular = token[:-1]
            if singular not in seen:
                seen.add(singular)
                markers.append(singular)
    return markers


def _sanitize_support_indices(raw_indices: list[int], citation_count: int) -> list[int]:
    output: list[int] = []
    seen: set[int] = set()
    for idx in raw_indices:
        if 1 <= idx <= citation_count and idx not in seen:
            seen.add(idx)
            output.append(idx)
    return output


def _heuristic_answerability(
    *,
    query: str,
    mode: str,
    evidence: list[RetrievedNode],
    citation_count: int,
) -> AnswerabilityDecision:
    if not evidence or citation_count <= 0:
        return AnswerabilityDecision(answerable=False, reason="insufficient_evidence", source="heuristic")

    if mode == "summarize":
        support = list(range(1, min(citation_count, len(evidence)) + 1))
        return AnswerabilityDecision(answerable=bool(support), reason="summary_supported", support_indices=support, source="heuristic")

    query_terms = _meaningful_query_terms(query)
    raw_markers = _raw_query_markers(query)
    if not query_terms:
        support = list(range(1, min(citation_count, len(evidence), 2) + 1))
        return AnswerabilityDecision(answerable=bool(support), reason="broad_query_supported", support_indices=support, source="heuristic")

    wants_document_discovery = any(term in _DOC_DISCOVERY_TERMS for term in query_terms)

    scored_indices: list[tuple[int, int]] = []
    for idx, node in enumerate(evidence, start=1):
        text = (node.text or "").lower()
        payload = node.payload or {}
        doc_name = str(payload.get("name") or payload.get("title") or payload.get("doc_id") or payload.get("file_id") or "").lower()
        sheet_name = str(payload.get("sheet_name") or "").lower()
        cell_range = str(payload.get("cell_range") or "").lower()
        headers = payload.get("column_headers") or []
        header_text = " ".join(str(header).lower() for header in headers if str(header).strip())
        row_start = payload.get("row_start")
        row_end = payload.get("row_end")
        row_range = f"{row_start}-{row_end}" if row_start is not None and row_end is not None else ""
        table_preview = str(payload.get("table_preview") or "").lower()
        searchable = "\n".join(
            part
            for part in [doc_name, sheet_name, cell_range, row_range, header_text, table_preview, text]
            if part
        ).strip()
        if not searchable:
            continue
        overlap = sum(1 for term in query_terms if term in searchable)
        if wants_document_discovery and doc_name:
            if any(term in doc_name for term in query_terms) or any(marker in doc_name for marker in raw_markers):
                overlap += 2
        if overlap > 0:
            scored_indices.append((idx, overlap))

    scored_indices.sort(key=lambda item: item[1], reverse=True)
    support = [idx for idx, _score in scored_indices[: min(3, citation_count)]]
    if support:
        reason = "document_discovery_supported" if wants_document_discovery else "topic_supported"
        return AnswerabilityDecision(answerable=True, reason=reason, support_indices=support, source="heuristic")

    return AnswerabilityDecision(answerable=False, reason="insufficient_evidence", source="heuristic")


def _build_judge_prompt(*, query: str, mode: str, evidence: list[RetrievedNode], citations: list[Citation]) -> str:
    settings = get_settings()
    blocks: list[str] = []
    max_chars = max(200, settings.answerability_max_chars_per_node)

    for idx, (node, citation) in enumerate(zip(evidence, citations, strict=False), start=1):
        text = (node.text or "").strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "..."
        blocks.append(
            f"[{idx}] doc={citation.doc_id} node={citation.node_id} page={citation.page}\n"
            f"title={citation.doc_name or citation.doc_id}\n"
            f"{text or '[no text]'}"
        )

    return (
        "Decide whether the retrieved evidence is sufficient to answer the user request with grounded citations. "
        "Allow high-level synthesis, research plans, and recommendations only when they can be explicitly grounded in the evidence. "
        "Document titles and metadata count as evidence for discovery questions such as whether relevant papers or documents exist. "
        "Return JSON only with keys answerable, reason, support_indices.\n\n"
        f"Mode: {mode}\n"
        f"Question: {query}\n\n"
        f"Evidence blocks:\n{chr(10).join(blocks)}\n\n"
        "JSON schema:\n"
        '{"answerable": true, "reason": "short_reason", "support_indices": [1, 2]}'
    )


def _parse_judge_response(raw_text: str, citation_count: int) -> AnswerabilityDecision | None:
    if not raw_text:
        return None

    match = _JSON_RE.search(raw_text)
    if not match:
        return None

    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict):
        return None

    raw_indices = payload.get("support_indices") or []
    if not isinstance(raw_indices, list):
        raw_indices = []

    indices: list[int] = []
    for item in raw_indices:
        try:
            indices.append(int(item))
        except (TypeError, ValueError):
            continue

    answerable = bool(payload.get("answerable"))
    reason = str(payload.get("reason") or ("supported" if answerable else "insufficient_evidence"))
    support_indices = _sanitize_support_indices(indices, citation_count)

    if answerable and not support_indices and citation_count > 0:
        support_indices = [1]

    if not answerable:
        support_indices = []

    return AnswerabilityDecision(
        answerable=answerable,
        reason=reason,
        support_indices=support_indices,
        source="llm",
    )


async def judge_answerability(
    *,
    query: str,
    mode: str,
    evidence: list[RetrievedNode],
    citations: list[Citation],
) -> AnswerabilityDecision:
    """Determine whether evidence can support a grounded answer with citations."""

    settings = get_settings()
    limited_evidence = evidence[: settings.answerability_max_evidence_nodes]
    limited_citations = citations[: settings.answerability_max_evidence_nodes]

    heuristic = _heuristic_answerability(
        query=query,
        mode=mode,
        evidence=limited_evidence,
        citation_count=len(limited_citations),
    )

    if mode == "summarize":
        return heuristic

    if heuristic.answerable and (
        heuristic.reason in _HEURISTIC_PRIORITY_REASONS or _looks_like_mention_query(query)
    ):
        return heuristic

    if not settings.enable_answerability_judge:
        return heuristic

    if not settings.answerability_use_llm:
        return heuristic

    if not limited_evidence or not limited_citations:
        return heuristic

    client = OllamaClient()
    try:
        raw = await client.generate_from_messages(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict evidence sufficiency judge for secure enterprise RAG. "
                        "Use only the provided evidence. Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": _build_judge_prompt(
                        query=query,
                        mode=mode,
                        evidence=limited_evidence,
                        citations=limited_citations,
                    ),
                },
            ],
            temperature=0.0,
            num_predict=128,
            num_ctx=2048,
        )
    except Exception:
        return heuristic

    parsed = _parse_judge_response(raw, len(limited_citations))
    if parsed is None:
        return heuristic
    return parsed
