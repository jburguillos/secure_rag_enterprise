"""Grounded generation service."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.config import get_settings
from app.generation.guardrails import enforce_citation_requirement, should_refuse_for_insufficient_evidence
from app.generation.ollama_client import OllamaClient
from app.models.schemas import Citation
from app.retrieval.qdrant_service import RetrievedNode


@dataclass
class GenerationResult:
    answer: str
    refusal_reason: str | None


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


def _build_map_reduce_prompt(*, query: str, evidence: list[RetrievedNode], citations: list[Citation]) -> str:
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
        [f"[{idx + 1}] doc={c.doc_id} page={c.page} node={c.node_id}" for idx, c in enumerate(citations)]
    )

    return (
        "Per-document map summaries are provided below. "
        "Produce a concise final summary with one bullet per document. "
        "Use only provided context and include citation markers in each bullet. "
        "If information is missing, say unknown without claiming access issues.\n\n"
        f"Question: {query}\n\n"
        f"Map blocks:\n{chr(10).join(doc_blocks)}\n\n"
        f"Citation registry:\n{citation_instructions}"
    )


async def generate_grounded_answer(*, query: str, mode: str, evidence: list[RetrievedNode], citations: list[Citation], include_images: bool) -> GenerationResult:
    settings = get_settings()

    if should_refuse_for_insufficient_evidence(len(evidence)):
        return GenerationResult(answer=settings.refusal_text, refusal_reason="insufficient_evidence")

    citation_ok, citation_reason = enforce_citation_requirement(len(citations))
    if not citation_ok:
        return GenerationResult(answer=settings.refusal_text, refusal_reason=citation_reason)

    if mode == "summarize" and len({_doc_id(node) for node in evidence}) > 1:
        prompt = _build_map_reduce_prompt(query=query, evidence=evidence, citations=citations)
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
            [f"[{idx + 1}] doc={c.doc_id} page={c.page} node={c.node_id}" for idx, c in enumerate(citations)]
        )
        prompt = (
            "Answer strictly from context. If missing, say you do not know. "
            "Include citation markers like [1], [2] tied to provided citations.\n\n"
            f"Mode: {mode}\n"
            f"Question: {query}\n\n"
            f"Context:\n{context}\n\n"
            f"Citations:\n{citation_instructions}"
        )

    system = (
        "You are a secure enterprise RAG assistant. "
        "Never use information outside context. "
        "Do not claim lack of access when authorized evidence is present."
    )

    client = OllamaClient()
    try:
        answer = await client.generate(system_prompt=system, user_prompt=prompt)
    except Exception:
        return GenerationResult(answer=settings.refusal_text, refusal_reason="llm_unavailable")

    if not answer:
        return GenerationResult(answer=settings.refusal_text, refusal_reason="empty_model_response")
    return GenerationResult(answer=answer, refusal_reason=None)
