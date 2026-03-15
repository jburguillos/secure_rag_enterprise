"""Grounded generation service."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import re
from typing import Any

from app.config import get_settings
from app.generation.guardrails import enforce_citation_requirement, should_refuse_for_insufficient_evidence
from app.generation.ollama_client import OllamaClient
from app.generation.vlm_router import VLMRouter
from app.models.schemas import Citation
from app.retrieval.qdrant_service import RetrievedNode


@dataclass
class GenerationResult:
    answer: str
    refusal_reason: str | None
    used_citation_indices: list[int] = field(default_factory=list)


_META_STYLE_PATTERNS = [
    re.compile(r"(?i)\bseg.n el texto[:,]?\s*"),
    re.compile(r"(?i)\ben el texto[:,]?\s*"),
    re.compile(r"(?i)\bseg.n el contexto(?: proporcionado)?[:,]?\s*"),
    re.compile(r"(?i)\bbased on the provided context[:,]?\s*"),
    re.compile(r"(?i)\baccording to (?:the )?(?:provided )?context[:,]?\s*"),
    re.compile(r"(?i)\bas seen in the text[:,]?\s*"),
    re.compile(r"(?i)\bin the text[:,]?\s*"),
]

_SEGMENT_TOKEN_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "de",
    "del",
    "do",
    "does",
    "el",
    "en",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "la",
    "las",
    "los",
    "my",
    "of",
    "on",
    "or",
    "para",
    "por",
    "que",
    "the",
    "to",
    "un",
    "una",
    "with",
    "y",
    "you",
    "your",
}
_PER_DOCUMENT_SUMMARY_PATTERNS = (
    re.compile(r"\bone bullet per document\b", re.IGNORECASE),
    re.compile(r"\bone bullet per file\b", re.IGNORECASE),
    re.compile(r"\b(each|every)\s+(document|file|doc|paper)\b", re.IGNORECASE),
    re.compile(r"\bfor each\s+(document|file|doc|paper)\b", re.IGNORECASE),
    re.compile(r"\bcada\s+(documento|archivo|fichero|paper)\b", re.IGNORECASE),
)


def _domain_context_instruction() -> str:
    settings = get_settings()
    hint = (settings.domain_context_hint or "").strip()
    if not hint:
        return ""
    return (
        f"Domain orientation: {hint} "
        "Treat this only as retrieval guidance, never as standalone evidence. "
        "All factual claims must still come from retrieved context and citations."
    )


def _strip_meta_language(answer: str) -> str:
    cleaned = (answer or "").strip()
    for pattern in _META_STYLE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"^[,;:\-\s]+", "", cleaned)
    return cleaned.strip()


def _build_context(evidence: list[RetrievedNode], max_chars: int) -> str:
    parts: list[str] = []
    total = 0
    for idx, node in enumerate(evidence, start=1):
        text = (node.text or "").strip()
        if not text:
            continue
        block = f"[E{idx}] {text}\n"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


def _extract_vlm_image_paths(evidence: list[RetrievedNode], max_images: int) -> list[str]:
    seen: set[str] = set()
    image_paths: list[str] = []
    for node in evidence:
        payload = node.payload or {}
        if str(payload.get("modality") or "text") != "image":
            continue
        image_path = str(payload.get("image_path") or "").strip()
        if not image_path or image_path in seen:
            continue
        seen.add(image_path)
        image_paths.append(image_path)
        if len(image_paths) >= max(1, max_images):
            break
    return image_paths


def _doc_id(node: RetrievedNode) -> str:
    payload = node.payload or {}
    return str(payload.get("doc_id") or payload.get("file_id") or "unknown_doc")


def _doc_name(node: RetrievedNode) -> str:
    payload = node.payload or {}
    return str(payload.get("name") or payload.get("title") or _doc_id(node))


def _build_doc_snippet(nodes: list[RetrievedNode], max_chars: int) -> str:
    chunks: list[str] = []
    total = 0
    for node in nodes:
        text = (node.text or "").strip()
        if not text:
            continue
        if total + len(text) > max_chars:
            text = text[: max(0, max_chars - total)]
        chunks.append(text)
        total += len(text)
        if total >= max_chars:
            break
    return "\n".join(chunks)


def _wants_per_document_summary(query: str) -> bool:
    lowered = (query or "").strip().lower()
    if not lowered:
        return False
    return any(pattern.search(lowered) for pattern in _PER_DOCUMENT_SUMMARY_PATTERNS)


def _extract_used_citation_indices(answer: str, citation_count: int) -> list[int]:
    if not answer or citation_count <= 0:
        return []

    matches = re.findall(r"\[(\d{1,3})\]", answer)
    used: list[int] = []
    seen: set[int] = set()
    for raw in matches:
        idx = int(raw)
        if 1 <= idx <= citation_count and idx not in seen:
            seen.add(idx)
            used.append(idx)
    return used


def _segment_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9_]{3,}", (text or "").lower())
        if token not in _SEGMENT_TOKEN_STOPWORDS
    }


def _best_citation_index_for_segment(
    segment: str,
    evidence: list[RetrievedNode],
    citations: list[Citation],
) -> int | None:
    segment_tokens = _segment_tokens(segment)
    if not segment_tokens:
        return None

    citation_index = {citation.node_id: idx + 1 for idx, citation in enumerate(citations)}
    best_idx: int | None = None
    best_score = 0

    for node in evidence:
        idx = citation_index.get(node.node_id)
        if idx is None:
            continue
        searchable = f"{_doc_name(node)}\n{node.text or ''}"
        overlap = len(segment_tokens & _segment_tokens(searchable))
        if overlap > best_score:
            best_score = overlap
            best_idx = idx

    if best_score >= 2:
        return best_idx
    return None


def _attach_missing_citations(answer: str, evidence: list[RetrievedNode], citations: list[Citation]) -> str:
    updated_lines: list[str] = []
    for line in answer.splitlines():
        stripped = line.strip()
        if not stripped or re.search(r"\[\d{1,3}\]", stripped):
            updated_lines.append(line)
            continue

        idx = _best_citation_index_for_segment(stripped, evidence, citations)
        if idx is None:
            updated_lines.append(line)
            continue

        updated_lines.append(f"{line.rstrip()} [{idx}]")

    return "\n".join(updated_lines)


def _build_map_reduce_prompt(
    *,
    query: str,
    evidence: list[RetrievedNode],
    citations: list[Citation],
    per_document: bool,
) -> str:
    settings = get_settings()
    citation_index = {c.node_id: idx + 1 for idx, c in enumerate(citations)}

    grouped: dict[str, list[RetrievedNode]] = defaultdict(list)
    order: list[str] = []
    for node in evidence:
        did = _doc_id(node)
        if did not in grouped:
            order.append(did)
        grouped[did].append(node)

    doc_blocks: list[str] = []
    max_docs = max(1, settings.summarize_map_max_docs)
    for did in order[:max_docs]:
        nodes = grouped[did]
        snippet = _build_doc_snippet(nodes, max_chars=max(200, settings.summarize_map_chars_per_doc))
        name = _doc_name(nodes[0])

        markers: list[str] = []
        for node in nodes:
            marker = citation_index.get(node.node_id)
            if marker is not None:
                markers.append(f"[{marker}]")
        marker_text = ", ".join(sorted(set(markers))) if markers else "none"

        doc_blocks.append(
            f"Doc: {name} ({did})\n"
            f"Map summary:\n{snippet}\n"
            f"Citations: {marker_text}"
        )

    citation_instructions = "\n".join(
        [
            f"[{idx + 1}] doc={c.doc_id} page={c.page} sheet={c.sheet_name} rows={c.row_start}-{c.row_end} "
            f"range={c.cell_range} node={c.node_id}"
            for idx, c in enumerate(citations)
        ]
    )

    if per_document:
        instructions = (
            "Per-document map summaries are provided below. "
            "Produce a concise final summary with one bullet per document. "
            "Only include documents that materially address the user question. "
            "Write directly for the end user and avoid meta-commentary. "
            "Do not mention 'text', 'context', 'map blocks', 'citation registry', or 'provided documents'. "
            "Never write phrases like 'segun el texto', 'segun el contexto', or 'based on the provided context'. "
            "Use only provided evidence and include citation markers in each bullet. "
            "If information is missing, say unknown without claiming access issues."
        )
    else:
        instructions = (
            "Grouped evidence summaries are provided below. "
            "Produce a concise integrated summary of the most relevant findings. "
            "Do not force one bullet per document. "
            "Mention only documents that materially support a claim. "
            "Use 2 to 4 bullets if that improves readability; otherwise use a short paragraph. "
            "Write directly for the end user and avoid meta-commentary. "
            "Do not mention 'text', 'context', 'map blocks', 'citation registry', or 'provided documents'. "
            "Never write phrases like 'segun el texto', 'segun el contexto', or 'based on the provided context'. "
            "Only mention visual evidence when the OCR or extracted text is informative; never write placeholders like 'unknown content'. "
            "Use only provided evidence and include citation markers only for claims you actually make. "
            "If information is missing, say unknown without claiming access issues."
        )

    return (
        f"{instructions}\n\n"
        f"Question: {query}\n\n"
        f"Map blocks:\n{chr(10).join(doc_blocks)}\n\n"
        f"Citation registry:\n{citation_instructions}"
    )


def _build_chat_messages(*, query: str, chat_history: list[dict[str, Any]] | None, max_turns: int = 12) -> list[dict[str, str]]:
    domain_instruction = _domain_context_instruction()
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are a helpful enterprise assistant in conversational mode. "
                "Respond naturally and directly. "
                "If the user asks about documents, explain that RAG mode provides grounded answers with citations. "
                f"{domain_instruction}"
            ),
        }
    ]

    if chat_history:
        tail = chat_history[-max_turns:]
        for item in tail:
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or "").strip()
            if role not in {"user", "assistant", "system"} or not content:
                continue
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": query.strip()})
    return messages


async def generate_chat_answer(*, query: str, chat_history: list[dict[str, Any]] | None) -> GenerationResult:
    messages = _build_chat_messages(query=query, chat_history=chat_history)
    client = OllamaClient()
    settings = get_settings()

    try:
        answer = await client.generate_from_messages(
            messages=messages,
            temperature=0.4,
            num_predict=384,
            num_ctx=2048,
        )
    except Exception:
        return GenerationResult(answer=settings.llm_unavailable_text, refusal_reason="llm_unavailable")

    if not answer:
        return GenerationResult(answer="", refusal_reason="empty_model_response")
    return GenerationResult(answer=answer, refusal_reason=None)


async def generate_grounded_answer(
    *,
    query: str,
    mode: str,
    evidence: list[RetrievedNode],
    citations: list[Citation],
    include_images: bool,
) -> GenerationResult:
    settings = get_settings()

    if should_refuse_for_insufficient_evidence(len(evidence)):
        return GenerationResult(answer=settings.refusal_text, refusal_reason="insufficient_evidence")

    citation_ok, citation_reason = enforce_citation_requirement(len(citations))
    if not citation_ok:
        return GenerationResult(answer=settings.refusal_text, refusal_reason=citation_reason)

    if include_images:
        image_paths = _extract_vlm_image_paths(evidence, max_images=settings.vlm_router_max_images)
        router_mode = (settings.vlm_router or "").strip().lower()
        if image_paths and router_mode and router_mode != "disabled":
            router = VLMRouter(enabled=True)
            vlm_prompt = (
                "Answer from provided visual evidence and supplied citations only. "
                "Use citation markers like [1], [2]. "
                "If uncertain, be explicit about uncertainty.\n\n"
                f"Question: {query}"
            )
            try:
                vlm_result = await router.maybe_route(prompt=vlm_prompt, image_paths=image_paths)
            except Exception:
                vlm_result = None

            if vlm_result and vlm_result.used_vlm and (vlm_result.answer or "").strip():
                answer = _strip_meta_language(vlm_result.answer)
                answer = _attach_missing_citations(answer, evidence, citations)
                used_citation_indices = _extract_used_citation_indices(answer, len(citations))
                used_ok, used_reason = enforce_citation_requirement(len(used_citation_indices))
                if not used_ok:
                    return GenerationResult(answer=settings.refusal_text, refusal_reason=used_reason)
                return GenerationResult(
                    answer=answer,
                    refusal_reason=None,
                    used_citation_indices=used_citation_indices,
                )

    if mode == "summarize" and len({_doc_id(node) for node in evidence}) > 1:
        prompt = _build_map_reduce_prompt(
            query=query,
            evidence=evidence,
            citations=citations,
            per_document=_wants_per_document_summary(query),
        )
    else:
        safe_max_chars = min(settings.max_context_chars, 3500)
        context = _build_context(evidence, max_chars=safe_max_chars)
        if not context:
            if include_images and any((n.payload or {}).get("modality") == "image" for n in evidence):
                return GenerationResult(
                    answer="Visual evidence found but no OCR text is available. Open the referenced page image.",
                    refusal_reason="visual_evidence_without_text",
                )
            return GenerationResult(answer=settings.refusal_text, refusal_reason="insufficient_text_context")

        citation_instructions = "\n".join(
            [
                f"[{idx + 1}] doc={c.doc_id} page={c.page} sheet={c.sheet_name} rows={c.row_start}-{c.row_end} "
                f"range={c.cell_range} node={c.node_id}"
                for idx, c in enumerate(citations)
            ]
        )
        prompt = (
            "Respond directly for the end user. "
            "Do not use meta phrases like 'segun el texto', 'segun el contexto', 'en el texto', "
            "'based on the provided context', or 'according to the context'. "
            "You may synthesize a grounded plan, recommendation, or next steps when they are clearly supported by the evidence. "
            "Only mention visual evidence when the OCR or extracted text is informative; never write placeholders like 'unknown content'. "
            "Use only the provided evidence and add citation markers [1], [2], etc.\n\n"
            f"Mode: {mode}\n"
            f"Question: {query}\n\n"
            f"Context:\n{context}\n\n"
            f"Citations:\n{citation_instructions}"
        )

    system = (
        "You are a secure enterprise RAG assistant. "
        "Never use information outside context. "
        "Do not claim lack of access when authorized evidence is present. "
        "Write in final-user style and avoid mentioning context/text metadata. "
        "If a visual/image item lacks useful OCR or extracted text, omit it instead of describing placeholder or unknown content. "
        "Ground any plan or recommendation in the cited evidence. "
        f"{_domain_context_instruction()}"
    )

    client = OllamaClient()
    try:
        answer = await client.generate(system_prompt=system, user_prompt=prompt)
    except Exception:
        return GenerationResult(answer=settings.llm_unavailable_text, refusal_reason="llm_unavailable")

    answer = _strip_meta_language(answer)
    if not answer:
        return GenerationResult(answer=settings.refusal_text, refusal_reason="empty_model_response")

    answer = _attach_missing_citations(answer, evidence, citations)
    used_citation_indices = _extract_used_citation_indices(answer, len(citations))
    used_ok, used_reason = enforce_citation_requirement(len(used_citation_indices))
    if not used_ok:
        return GenerationResult(answer=settings.refusal_text, refusal_reason=used_reason)

    return GenerationResult(answer=answer, refusal_reason=None, used_citation_indices=used_citation_indices)
