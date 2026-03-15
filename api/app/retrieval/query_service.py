"""Query orchestration service."""

from __future__ import annotations

from collections import defaultdict
import json
import re
from typing import Any
from uuid import UUID, uuid4

from app.audit.service import persist_policy_decision, persist_query_audit
from app.auth.context import Entitlements
from app.config import get_settings
from app.db.database import get_session
from app.generation.ollama_client import OllamaClient
from app.generation.service import GenerationResult, generate_chat_answer, generate_grounded_answer
from app.models.schemas import Citation, PolicyDecision, QueryFilters, QueryRequest, QueryResponse
from app.policy.opa_client import PolicyClient, PolicyResult
from app.retrieval.acl import payload_access_allowed
from app.retrieval.answerability import judge_answerability
from app.retrieval.diversity import diversify_by_doc
from app.retrieval.followup import maybe_rewrite_followup
from app.retrieval.hybrid import RetrievalService
from app.retrieval.intent import build_smalltalk_response, decide_auto_retrieval_mode, detect_disallowed_request

_SUMMARIZE_HINTS = (
    "summarize",
    "summary",
    "summarise",
    "resumen",
    "resumir",
    "resume ",
    "sumariza",
    "sumarice",
    "summarize the documents",
    "summarize the files",
    "what documents do you have",
    "what files do you have",
    "documents you have",
    "files you have",
    "for each file",
    "for each document",
    "each file",
    "each document",
    "cada archivo",
    "cada documento",
    "cada fichero",
)

_DOC_EXTENSIONS = (".pdf", ".docx", ".txt", ".md", ".doc")
_SINGLE_DOC_HINT_PATTERNS = (
    re.compile(r"\bin\s+[a-zA-Z0-9_.\-\s]+\.(pdf|docx|txt|md|doc)\b", re.IGNORECASE),
    re.compile(r"\b(this|that|the)\s+(paper|document|doc|file|pdf)\b", re.IGNORECASE),
    re.compile(r"\b(este|esta|ese|esa)\s+(documento|archivo|fichero|pdf)\b", re.IGNORECASE),
)
_MULTI_DOC_HINT_PATTERNS = (
    re.compile(r"\b(all|each|every|compare|across|documents|files|papers|docs)\b", re.IGNORECASE),
    re.compile(r"\b(todos|cada|compara|documentos|archivos|ficheros|papers|docs)\b", re.IGNORECASE),
)
_SINGLE_DOC_WORD_PATTERNS = (
    re.compile(r"\bpaper\b", re.IGNORECASE),
    re.compile(r"\bdocument\b", re.IGNORECASE),
    re.compile(r"\bdoc\b", re.IGNORECASE),
    re.compile(r"\bfile\b", re.IGNORECASE),
    re.compile(r"\bpdf\b", re.IGNORECASE),
    re.compile(r"\bdocumento\b", re.IGNORECASE),
    re.compile(r"\barchivo\b", re.IGNORECASE),
    re.compile(r"\bfichero\b", re.IGNORECASE),
)
_QUERY_SOURCE_HINTS: dict[str, tuple[str, ...]] = {
    "google_drive": (
        "google drive",
        "drive pdf",
        "drive pdfs",
        "drive file",
        "drive files",
        "drive folder",
        "drive folders",
        "from drive",
        "in drive",
    ),
    "local_folder": (
        "local file",
        "local files",
        "local folder",
        "local folders",
        "from local",
        "on disk",
        "local pdf",
        "local pdfs",
    ),
}
_QUERY_MIME_HINTS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"\bpdfs?\b", re.IGNORECASE), ("application/pdf", ".pdf")),
    (
        re.compile(r"\bdocx\b|\bword docs?\b|\bword files?\b", re.IGNORECASE),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    ),
    (re.compile(r"\btxt\b|\btext files?\b", re.IGNORECASE), ("text/plain", ".txt")),
    (
        re.compile(r"\b(excel|xlsx|spreadsheet|spreadsheets|workbook|workbooks|sheet|sheets)\b", re.IGNORECASE),
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    ),
    (
        re.compile(r"\bgoogle sheets?\b", re.IGNORECASE),
        ("application/vnd.google-apps.spreadsheet",),
    ),
)
_PATH_TOKEN_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.\-]*(?:/[a-zA-Z0-9][a-zA-Z0-9_.\-]*)+")
_PATH_DOC_SUFFIXES = (".pdf", ".docx", ".txt", ".md", ".xlsx")
_IMAGE_OCR_MIN_CHARS = 40
_IMAGE_OCR_MIN_TOKENS = 6
_GENERIC_IMAGE_TEXT_PATTERNS = (
    re.compile(r"^visual evidence from document .+ page \d+ \((?:page|embedded)\)$", re.IGNORECASE),
    re.compile(r"^image on page \d+$", re.IGNORECASE),
)
_INVENTORY_REQUEST_PATTERNS = (
    re.compile(r"\bwhat\s+(types|kind|kinds)\s+of\s+.+\bdocuments?\s+are\s+available\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+(files|documents|docs|papers)\s+(are available|do you have)\b", re.IGNORECASE),
    re.compile(r"\blist\s+(exact\s+)?(indexed\s+)?(file names|filenames|documents|files|titles)\b", re.IGNORECASE),
    re.compile(r"\b(use only|only use)\s+(document\s+titles|metadata|corpus metadata)\b", re.IGNORECASE),
    re.compile(r"\bavailable\s+in\s+the\s+indexed\s+corpus\b", re.IGNORECASE),
)
_INVENTORY_CATEGORY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Portfolio", ("portfolio", "portafolio")),
    ("Fund Management", ("fund management", "fund-management")),
    ("Due Diligence", ("due diligence", "diligence")),
    ("Market Research", ("market research", "market-research")),
    ("Dealflow", ("dealflow",)),
    ("Legal Compliance", ("legal compliance", "compliance")),
)
_INVENTORY_SCAN_LIMIT = 5000
_INVENTORY_EXAMPLES_PER_CATEGORY = 3
_CLARIFICATION_SUGGESTION_LIMIT = 3
_QUERY_STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "for",
    "to",
    "and",
    "or",
    "in",
    "on",
    "with",
    "what",
    "which",
    "are",
    "is",
    "do",
    "does",
    "from",
    "about",
    "using",
    "only",
    "under",
    "available",
    "indexed",
    "corpus",
    "fund",
}
_QUERY_TERM_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "commitment": ("commitments", "lp", "register", "capital call", "capital calls"),
    "capital": ("fundraising", "capital call", "capital calls"),
    "call": ("capital call", "capital calls", "register"),
    "runway": ("budget", "cash", "cash runway"),
    "revenue": ("kpi", "tracker", "budget"),
    "budget": ("runway", "cash", "forecast"),
    "investor": ("lp", "commitment", "fundraising"),
}


def _normalize_doc_ref(text: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _doc_aliases(payload: dict[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for raw in (
        payload.get("name"),
        payload.get("title"),
        payload.get("doc_id"),
        payload.get("file_id"),
    ):
        value = str(raw or "").strip()
        if not value:
            continue
        normalized = _normalize_doc_ref(value)
        if normalized:
            aliases.add(normalized)
        lower = value.lower()
        for ext in _DOC_EXTENSIONS:
            if lower.endswith(ext):
                stem = value[: -len(ext)].strip()
                stem_normalized = _normalize_doc_ref(stem)
                if stem_normalized:
                    aliases.add(stem_normalized)
                break
    return aliases


def _targeted_doc_ids_from_query(query: str, nodes: list[Any]) -> set[str]:
    normalized_query = _normalize_doc_ref(query)
    if not normalized_query:
        return set()

    matched: set[str] = set()
    for node in nodes:
        payload = getattr(node, "payload", None) or {}
        doc_id = str(payload.get("doc_id") or payload.get("file_id") or "").strip()
        if not doc_id:
            continue
        aliases = _doc_aliases(payload)
        for alias in aliases:
            if len(alias) < 5:
                continue
            if alias in normalized_query:
                matched.add(doc_id)
                break

    return matched


def _candidate_docs(nodes: list[Any]) -> list[dict[str, str]]:
    seen: set[str] = set()
    candidates: list[dict[str, str]] = []
    for node in nodes:
        payload = getattr(node, "payload", None) or {}
        doc_id = str(payload.get("doc_id") or payload.get("file_id") or "").strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        candidates.append(
            {
                "doc_id": doc_id,
                "name": str(payload.get("name") or payload.get("title") or doc_id),
            }
        )
    return candidates


def _looks_like_single_doc_request(query: str) -> bool:
    lowered = (query or "").strip().lower()
    if not lowered:
        return False
    if any(pattern.search(lowered) for pattern in _MULTI_DOC_HINT_PATTERNS):
        return False
    if any(pattern.search(lowered) for pattern in _SINGLE_DOC_HINT_PATTERNS):
        return True
    return any(pattern.search(lowered) for pattern in _SINGLE_DOC_WORD_PATTERNS)


async def _llm_targeted_doc_ids_from_query(query: str, nodes: list[Any]) -> set[str]:
    candidates = _candidate_docs(nodes)
    if len(candidates) < 2:
        return set()
    if not _looks_like_single_doc_request(query):
        return set()

    prompt_candidates = "\n".join(
        f"- doc_id={item['doc_id']} name={item['name']}" for item in candidates[:8]
    )
    client = OllamaClient()
    try:
        raw = await client.generate_from_messages(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You select whether a user query is targeting exactly one document from a bounded list "
                        "of already authorized candidates. Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Given the query and the candidate document titles below, decide whether the user is clearly "
                        "asking about exactly one document. If yes, return that doc_id. If ambiguous or multi-document, "
                        'return {"scope":"none","doc_id":null}.\n\n'
                        f"Query: {query}\n\nCandidates:\n{prompt_candidates}\n\n"
                        'JSON schema: {"scope":"single_document"|"none","doc_id":"candidate_doc_id"|null}'
                    ),
                },
            ],
            temperature=0.0,
            num_predict=96,
            num_ctx=2048,
        )
    except Exception:
        return set()

    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return set()
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return set()

    if not isinstance(payload, dict):
        return set()
    if str(payload.get("scope") or "").strip().lower() != "single_document":
        return set()

    doc_id = str(payload.get("doc_id") or "").strip()
    candidate_ids = {item["doc_id"] for item in candidates}
    if doc_id and doc_id in candidate_ids:
        return {doc_id}
    return set()


def _effective_query_mode(request: QueryRequest) -> str:
    if request.mode == "summarize":
        return "summarize"

    lowered = (request.query or "").strip().lower()
    if any(hint in lowered for hint in _SUMMARIZE_HINTS):
        return "summarize"
    return "qa"


def _ordered_union(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for value in group:
            clean = str(value or "").strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            merged.append(clean)
    return merged


def _normalize_path_prefix(value: str) -> str:
    cleaned = (value or "").replace("\\", "/").strip().strip(".,;:!?()[]{}\"'`")
    cleaned = re.sub(r"/{2,}", "/", cleaned).strip("/")
    return cleaned


def _extract_path_prefix_filters(query: str) -> tuple[list[str], list[str]]:
    folder_prefixes: list[str] = []
    path_prefixes: list[str] = []
    if not query:
        return folder_prefixes, path_prefixes

    for match in _PATH_TOKEN_RE.findall(query):
        normalized = _normalize_path_prefix(match)
        if not normalized or "/" not in normalized:
            continue

        lowered = normalized.lower()
        path_prefixes.append(normalized)
        path_prefixes.append(lowered)

        if any(lowered.endswith(ext) for ext in _PATH_DOC_SUFFIXES):
            parent = normalized.rsplit("/", 1)[0].strip()
            if parent:
                folder_prefixes.append(parent)
                folder_prefixes.append(parent.lower())
        else:
            folder_prefixes.append(normalized)
            folder_prefixes.append(lowered)

    return _ordered_union(folder_prefixes), _ordered_union(path_prefixes)


def _looks_like_inventory_request(query: str) -> bool:
    lowered = (query or "").strip().lower()
    if not lowered:
        return False
    return any(pattern.search(lowered) for pattern in _INVENTORY_REQUEST_PATTERNS)


def _requested_inventory_categories(query: str) -> list[str]:
    lowered = (query or "").strip().lower()
    requested: list[str] = []
    for label, hints in _INVENTORY_CATEGORY_HINTS:
        if any(hint in lowered for hint in hints):
            requested.append(label)
    return requested


def _normalize_query_term(token: str) -> str:
    value = token.strip().lower()
    if len(value) > 4 and value.endswith("ies"):
        return value[:-3] + "y"
    if len(value) > 4 and value.endswith("s"):
        return value[:-1]
    return value


def _expanded_query_terms(query: str) -> set[str]:
    tokens = {
        _normalize_query_term(match)
        for match in re.findall(r"[a-zA-Z0-9_]+", (query or "").lower())
    }
    tokens = {token for token in tokens if token and token not in _QUERY_STOPWORDS and len(token) > 2}
    expanded = set(tokens)
    lowered = (query or "").lower()
    if "capital commitment" in lowered or "capital commitments" in lowered:
        expanded.update({"commitment", "lp", "register", "capital call", "capital calls", "investor relations"})
    for token in list(tokens):
        expanded.update(_QUERY_TERM_EXPANSIONS.get(token, ()))
    return expanded


def _clarification_doc_score(query_terms: set[str], node: Any) -> int:
    payload = getattr(node, "payload", None) or {}
    title = str(payload.get("name") or payload.get("title") or "").lower()
    path = str(payload.get("drive_path") or payload.get("folder_path") or "").lower()
    sheet_name = str(payload.get("sheet_name") or "").lower()
    score = 0
    for term in query_terms:
        if term and term in title:
            score += 6
        if term and term in path:
            score += 3
        if term and term in sheet_name:
            score += 4
    if payload.get("tabular_node_type") == "workbook_summary":
        score += 2
    if payload.get("source_kind") == "tabular":
        score += 1
    return score


def _select_clarification_nodes(query: str, nodes: list[Any]) -> list[Any]:
    query_terms = _expanded_query_terms(query)
    if not query_terms:
        return []
    ranked = sorted(
        nodes,
        key=lambda node: (
            -_clarification_doc_score(query_terms, node),
            _inventory_doc_sort_key(node),
        ),
    )
    selected = [node for node in ranked if _clarification_doc_score(query_terms, node) > 0]
    return selected[:_CLARIFICATION_SUGGESTION_LIMIT]


def _build_clarification_answer(*, query: str, citations: list[Citation]) -> str:
    if not citations:
        return get_settings().refusal_text

    lines = [
        "I cannot answer that reliably from the currently selected evidence.",
        "To narrow it down, tell me which of these documents you want me to use:",
    ]
    for idx, citation in enumerate(citations, start=1):
        name = citation.doc_name or citation.doc_id
        if citation.sheet_name:
            lines.append(f"- {name} / {citation.sheet_name} [{idx}]")
        else:
            lines.append(f"- {name} [{idx}]")

    first = citations[0]
    first_name = first.doc_name or first.doc_id
    second = citations[1] if len(citations) > 1 else None
    lines.append("You can also ask one of these:")
    lines.append(f'- "In {first_name}, summarize the relevant evidence with citations."')
    if second is not None:
        second_name = second.doc_name or second.doc_id
        lines.append(
            f'- "Use only {first_name} and {second_name}. What do they say about {query.rstrip("?")}?"'
        )
    return "\n".join(lines)


def _inventory_category_from_payload(payload: dict[str, Any]) -> str:
    path = str(payload.get("folder_path") or payload.get("drive_path") or "").strip()
    if path:
        for segment in [part.strip() for part in path.split("/") if part.strip()]:
            if re.match(r"^\d+_", segment):
                clean = re.sub(r"^\d+_", "", segment).replace("_", " ").strip()
                if clean:
                    return clean.title()
    source = str(payload.get("source") or payload.get("dataset_source") or "").strip()
    if source == "local_folder":
        return "Local Folder"
    if source == "google_drive":
        return "Google Drive"
    return "Uncategorized"


def _inventory_doc_sort_key(node: Any) -> tuple[str, str]:
    payload = getattr(node, "payload", None) or {}
    category = _inventory_category_from_payload(payload)
    title = str(payload.get("name") or payload.get("title") or payload.get("doc_id") or "").lower()
    return (category.lower(), title)


def _inventory_node_rank(node: Any) -> int:
    payload = getattr(node, "payload", None) or {}
    modality = str(payload.get("modality") or "text")
    tabular_node_type = str(payload.get("tabular_node_type") or "")
    score = 0
    if modality == "text":
        score += 10
    if tabular_node_type == "workbook_summary":
        score += 5
    elif tabular_node_type == "sheet_summary":
        score += 3
    if payload.get("page") == 1:
        score += 1
    return score


def _unique_inventory_docs(nodes: list[Any]) -> list[Any]:
    by_doc: dict[str, Any] = {}
    for node in nodes:
        payload = getattr(node, "payload", None) or {}
        doc_id = str(payload.get("doc_id") or payload.get("file_id") or "").strip()
        if not doc_id:
            continue
        existing = by_doc.get(doc_id)
        if existing is None or _inventory_node_rank(node) > _inventory_node_rank(existing):
            by_doc[doc_id] = node
    return sorted(by_doc.values(), key=_inventory_doc_sort_key)


async def _authorize_nodes(
    *,
    nodes: list[Any],
    entitlements: Entitlements,
    policy: PolicyClient,
) -> tuple[list[Any], PolicyResult]:
    allowed_nodes: list[Any] = []
    effective_policy: PolicyResult | None = None

    if not nodes:
        effective_policy = await policy.evaluate(
            entitlements=entitlements,
            resource_acl=_empty_resource_acl(),
            transitional_drive_acl=True,
        )
        return allowed_nodes, effective_policy

    for node in nodes:
        payload = getattr(node, "payload", None) or {}
        if not payload_access_allowed(payload, entitlements):
            continue

        acl = {
            "allowed_users": payload.get("allowed_users") or [],
            "allowed_groups": payload.get("allowed_groups") or [],
            "allowed_emails": payload.get("allowed_emails") or [],
            "allowed_domains": payload.get("allowed_domains") or [],
            "is_public": bool(payload.get("is_public", False)),
        }
        decision = await policy.evaluate(entitlements=entitlements, resource_acl=acl, transitional_drive_acl=True)
        if decision.allow:
            allowed_nodes.append(node)
            effective_policy = decision

    if effective_policy is None:
        effective_policy = await policy.evaluate(
            entitlements=entitlements,
            resource_acl=_empty_resource_acl(),
            transitional_drive_acl=True,
        )
    return allowed_nodes, effective_policy


def _build_inventory_answer(
    *,
    query: str,
    nodes: list[Any],
    citations: list[Citation],
) -> str:
    if not nodes or not citations:
        return "No authorized indexed documents matched this request."

    requested_categories = _requested_inventory_categories(query)
    grouped: dict[str, list[tuple[Any, Citation]]] = defaultdict(list)
    citation_index = {citation.node_id: idx + 1 for idx, citation in enumerate(citations)}
    for node, citation in zip(nodes, citations, strict=False):
        category = _inventory_category_from_payload(getattr(node, "payload", None) or {})
        grouped[category].append((node, citation))

    if requested_categories:
        ordered_categories = [label for label in requested_categories if label in grouped]
    else:
        ordered_categories = sorted(grouped, key=lambda label: (-len(grouped[label]), label.lower()))

    if not ordered_categories:
        ordered_categories = sorted(grouped)

    lines = ["Indexed corpus categories and example file names:"]
    for category in ordered_categories:
        entries = grouped.get(category, [])
        if not entries:
            continue
        total_docs = len(entries)
        examples: list[str] = []
        for _, citation in entries[:_INVENTORY_EXAMPLES_PER_CATEGORY]:
            name = citation.doc_name or citation.doc_id
            marker = citation_index.get(citation.node_id, 0)
            examples.append(f"{name} [{marker}]")
        lines.append(f"- {category} ({total_docs} indexed files): " + "; ".join(examples))

    return "\n".join(lines)


async def _build_inventory_query_response(
    *,
    request: QueryRequest,
    entitlements: Entitlements,
    run_id: UUID,
    retrieval: RetrievalService,
    policy: PolicyClient,
    effective_filters: QueryFilters | None,
) -> QueryResponse:
    inventory_nodes = retrieval.retrieve_inventory(
        entitlements,
        query_filters=effective_filters,
        limit=_INVENTORY_SCAN_LIMIT,
    )
    allowed_nodes, effective_policy = await _authorize_nodes(nodes=inventory_nodes, entitlements=entitlements, policy=policy)
    unique_docs = _unique_inventory_docs(allowed_nodes)

    requested_categories = set(_requested_inventory_categories(request.query))
    if requested_categories:
        unique_docs = [
            node
            for node in unique_docs
            if _inventory_category_from_payload(getattr(node, "payload", None) or {}) in requested_categories
        ]

    citation_nodes: list[Any] = []
    for category in (
        _requested_inventory_categories(request.query)
        or sorted({_inventory_category_from_payload((node.payload or {})) for node in unique_docs})
    ):
        category_nodes = [
            node for node in unique_docs
            if _inventory_category_from_payload(getattr(node, "payload", None) or {}) == category
        ]
        citation_nodes.extend(category_nodes[:_INVENTORY_EXAMPLES_PER_CATEGORY])

    if not citation_nodes and unique_docs:
        citation_nodes = unique_docs[: min(len(unique_docs), _INVENTORY_EXAMPLES_PER_CATEGORY)]

    citations = [_citation_from_payload(node.payload, node.node_id) for node in citation_nodes]
    answer = _build_inventory_answer(query=request.query, nodes=citation_nodes, citations=citations)
    refusal_reason = None if citations else "insufficient_evidence"
    response_status = "answered" if citations else "refused"

    policy_model = PolicyDecision(
        decision_id=effective_policy.decision_id,
        allow=effective_policy.allow,
        reason=effective_policy.reason,
        policy_version=effective_policy.policy_version,
    )

    evidence_rows = [
        {
            "node_id": node.node_id,
            "doc_id": node.payload.get("doc_id") or node.payload.get("file_id"),
            "page": node.payload.get("page"),
            "chunk_id": node.payload.get("chunk_id"),
            "modality": node.payload.get("modality"),
            "score": node.score,
            "payload": node.payload,
        }
        for node in citation_nodes
    ]
    citation_rows = [c.model_dump() for c in citations]

    with get_session() as session:
        persist_policy_decision(
            session,
            decision_id=policy_model.decision_id,
            run_id=run_id,
            user_id_hash=None,
            user_groups=entitlements.groups,
            policy_input={
                "query_hash_only": True,
                "retrieval_mode": request.retrieval_mode,
                "inventory_mode": True,
                "filters": effective_filters.model_dump() if effective_filters else None,
                "inventory_scanned_nodes": len(inventory_nodes),
                "inventory_allowed_nodes": len(allowed_nodes),
                "inventory_unique_docs": len(unique_docs),
                "used_citation_count": len(citations),
                "requested_categories": sorted(requested_categories),
            },
            policy_result=policy_model.model_dump(),
            policy_version=policy_model.policy_version,
        )
        persist_query_audit(
            session,
            run_id=run_id,
            query=request.query,
            mode="qa",
            response_status=response_status,
            refusal_reason=refusal_reason,
            user_id=entitlements.user_id,
            email=entitlements.email,
            groups=entitlements.groups,
            evidence_rows=evidence_rows,
            citation_rows=citation_rows,
            policy_decision_id=policy_model.decision_id,
            model_id="metadata_inventory",
            model_version="1.0",
        )

    return QueryResponse(
        run_id=run_id,
        answer=answer if citations else get_settings().refusal_text,
        refusal_reason=refusal_reason,
        citations=citations,
        policy_decision=policy_model,
    )


async def _build_clarification_query_response(
    *,
    request: QueryRequest,
    entitlements: Entitlements,
    run_id: UUID,
    retrieval: RetrievalService,
    policy: PolicyClient,
    effective_filters: QueryFilters | None,
    effective_policy: PolicyResult,
) -> QueryResponse | None:
    retrieve_inventory = getattr(retrieval, "retrieve_inventory", None)
    if not callable(retrieve_inventory):
        return None

    inventory_nodes = retrieve_inventory(
        entitlements,
        query_filters=effective_filters,
        limit=_INVENTORY_SCAN_LIMIT,
    )
    unique_inventory_nodes = _unique_inventory_docs(inventory_nodes)
    allowed_inventory_nodes, _ = await _authorize_nodes(
        nodes=unique_inventory_nodes,
        entitlements=entitlements,
        policy=policy,
    )
    suggestion_nodes = _select_clarification_nodes(request.query, allowed_inventory_nodes)
    if not suggestion_nodes:
        return None

    citations = [_citation_from_payload(node.payload, node.node_id) for node in suggestion_nodes]
    answer = _build_clarification_answer(query=request.query, citations=citations)
    policy_model = PolicyDecision(
        decision_id=effective_policy.decision_id,
        allow=effective_policy.allow,
        reason=effective_policy.reason,
        policy_version=effective_policy.policy_version,
    )

    evidence_rows = [
        {
            "node_id": node.node_id,
            "doc_id": node.payload.get("doc_id") or node.payload.get("file_id"),
            "page": node.payload.get("page"),
            "chunk_id": node.payload.get("chunk_id"),
            "modality": node.payload.get("modality"),
            "score": node.score,
            "payload": node.payload,
        }
        for node in suggestion_nodes
    ]
    citation_rows = [citation.model_dump() for citation in citations]

    with get_session() as session:
        persist_policy_decision(
            session,
            decision_id=policy_model.decision_id,
            run_id=run_id,
            user_id_hash=None,
            user_groups=entitlements.groups,
            policy_input={
                "query_hash_only": True,
                "retrieval_mode": request.retrieval_mode,
                "clarification_mode": True,
                "filters": effective_filters.model_dump() if effective_filters else None,
                "clarification_suggestion_count": len(citations),
            },
            policy_result=policy_model.model_dump(),
            policy_version=policy_model.policy_version,
        )
        persist_query_audit(
            session,
            run_id=run_id,
            query=request.query,
            mode="qa",
            response_status="clarification",
            refusal_reason=None,
            user_id=entitlements.user_id,
            email=entitlements.email,
            groups=entitlements.groups,
            evidence_rows=evidence_rows,
            citation_rows=citation_rows,
            policy_decision_id=policy_model.decision_id,
            model_id="clarification_fallback",
            model_version="1.0",
        )

    return QueryResponse(
        run_id=run_id,
        answer=answer,
        refusal_reason=None,
        citations=citations,
        policy_decision=policy_model,
    )


def _infer_query_filters(query: str) -> QueryFilters | None:
    lowered = (query or "").strip().lower()
    if not lowered:
        return None

    sources = [
        source
        for source, hints in _QUERY_SOURCE_HINTS.items()
        if any(hint in lowered for hint in hints)
    ]
    mime_types: list[str] = []
    for pattern, values in _QUERY_MIME_HINTS:
        if pattern.search(lowered):
            mime_types.extend(values)

    folder_prefixes, path_prefixes = _extract_path_prefix_filters(query)

    if not sources and not mime_types and not folder_prefixes and not path_prefixes:
        return None

    return QueryFilters(
        sources=_ordered_union(sources),
        mime_types=_ordered_union(mime_types),
        folder_prefixes=_ordered_union(folder_prefixes),
        path_prefixes=_ordered_union(path_prefixes),
    )


def _merge_query_filters(explicit: QueryFilters | None, inferred: QueryFilters | None) -> QueryFilters | None:
    if explicit is None and inferred is None:
        return None

    explicit = explicit or QueryFilters()
    inferred = inferred or QueryFilters()

    merged = QueryFilters(
        sources=_ordered_union(explicit.sources, inferred.sources),
        mime_types=_ordered_union(explicit.mime_types, inferred.mime_types),
        doc_ids=_ordered_union(explicit.doc_ids, inferred.doc_ids),
        tags=_ordered_union(explicit.tags, inferred.tags),
        folder_prefixes=_ordered_union(explicit.folder_prefixes, inferred.folder_prefixes),
        path_prefixes=_ordered_union(explicit.path_prefixes, inferred.path_prefixes),
        modified_from=explicit.modified_from or inferred.modified_from,
        modified_to=explicit.modified_to or inferred.modified_to,
    )

    if not any(
        (
            merged.sources,
            merged.mime_types,
            merged.doc_ids,
            merged.tags,
            merged.folder_prefixes,
            merged.path_prefixes,
            merged.modified_from,
            merged.modified_to,
        )
    ):
        return None
    return merged


def _is_useful_visual_node(node: Any) -> bool:
    payload = getattr(node, "payload", None) or {}
    if str(payload.get("modality") or "text") != "image":
        return True

    ocr_text = str(payload.get("ocr_text") or "").strip()
    token_count = len(re.findall(r"[a-zA-Z0-9_]+", ocr_text))
    if ocr_text and len(ocr_text) >= _IMAGE_OCR_MIN_CHARS and token_count >= _IMAGE_OCR_MIN_TOKENS:
        return True

    text = str(payload.get("text") or "").strip()
    if any(pattern.match(text) for pattern in _GENERIC_IMAGE_TEXT_PATTERNS):
        return False

    return bool(text) and len(text) >= _IMAGE_OCR_MIN_CHARS and len(re.findall(r"[a-zA-Z0-9_]+", text)) >= _IMAGE_OCR_MIN_TOKENS


def _prune_low_value_visual_nodes(nodes: list[Any]) -> tuple[list[Any], int]:
    kept: list[Any] = []
    dropped = 0
    for node in nodes:
        if _is_useful_visual_node(node):
            kept.append(node)
        else:
            dropped += 1
    return kept, dropped


def _cap_tabular_generation_nodes(nodes: list[Any], *, max_blocks_per_sheet: int) -> list[Any]:
    if max_blocks_per_sheet <= 0:
        return nodes

    kept: list[Any] = []
    per_sheet_counts: dict[tuple[str, str], int] = {}
    for node in nodes:
        payload = getattr(node, "payload", None) or {}
        if payload.get("source_kind") != "tabular" or payload.get("tabular_node_type") != "row_block":
            kept.append(node)
            continue

        doc_id = str(payload.get("doc_id") or payload.get("file_id") or "")
        sheet_name = str(payload.get("sheet_name") or "")
        key = (doc_id, sheet_name)
        current = per_sheet_counts.get(key, 0)
        if current >= max_blocks_per_sheet:
            continue
        per_sheet_counts[key] = current + 1
        kept.append(node)
    return kept


def _citation_from_payload(payload: dict[str, Any], node_id: str) -> Citation:
    return Citation(
        doc_id=str(payload.get("doc_id") or payload.get("file_id") or "unknown_doc"),
        doc_name=payload.get("name") or payload.get("title"),
        page=payload.get("page"),
        sheet_name=payload.get("sheet_name"),
        cell_range=payload.get("cell_range"),
        row_start=payload.get("row_start"),
        row_end=payload.get("row_end"),
        tabular_node_type=payload.get("tabular_node_type"),
        chunk_id=payload.get("chunk_id"),
        node_id=node_id,
        modality=str(payload.get("modality") or "text"),
        webViewLink=payload.get("webViewLink"),
    )


def _empty_resource_acl() -> dict[str, Any]:
    return {
        "allowed_users": [],
        "allowed_groups": [],
        "allowed_emails": [],
        "allowed_domains": [],
        "is_public": False,
    }


def _security_refusal_answer(reason: str) -> str:
    if reason == "auth_bypass":
        return "I cannot help with bypassing authorization controls."
    if reason == "data_exfiltration":
        return "I cannot help with data exfiltration or outbound data transfer requests."
    return "I cannot comply with that request."


async def _build_security_refusal_query_response(
    *,
    request: QueryRequest,
    entitlements: Entitlements,
    run_id: UUID,
    reason: str,
) -> QueryResponse:
    settings = get_settings()
    effective_mode = _effective_query_mode(request)
    answer = _security_refusal_answer(reason)
    refusal_reason = "policy_violation"
    response_status = "refused"

    policy_model = PolicyDecision(
        allow=False,
        reason=f"blocked_{reason}",
        policy_version="1.0",
    )

    with get_session() as session:
        persist_policy_decision(
            session,
            decision_id=policy_model.decision_id,
            run_id=run_id,
            user_id_hash=None,
            user_groups=entitlements.groups,
            policy_input={
                "query_hash_only": True,
                "retrieval_mode": request.retrieval_mode,
                "effective_mode": effective_mode,
                "security_blocked": True,
                "security_reason": reason,
            },
            policy_result=policy_model.model_dump(),
            policy_version=policy_model.policy_version,
        )
        persist_query_audit(
            session,
            run_id=run_id,
            query=request.query,
            mode=effective_mode,
            response_status=response_status,
            refusal_reason=refusal_reason,
            user_id=entitlements.user_id,
            email=entitlements.email,
            groups=entitlements.groups,
            evidence_rows=[],
            citation_rows=[],
            policy_decision_id=policy_model.decision_id,
            model_id="security_guardrails",
            model_version="1.0",
        )

    return QueryResponse(
        run_id=run_id,
        answer=answer,
        refusal_reason=refusal_reason,
        citations=[],
        policy_decision=policy_model,
    )


async def _build_smalltalk_query_response(
    *,
    request: QueryRequest,
    entitlements: Entitlements,
    run_id: UUID,
    reason: str,
) -> QueryResponse:
    """Return a conversational response for non-RAG turns."""

    settings = get_settings()
    smalltalk_mode = _effective_query_mode(request)
    fallback_answer = build_smalltalk_response(request.query, chat_mode=True)
    history_payload = [m.model_dump() for m in request.chat_history]
    generated = await generate_chat_answer(query=request.query, chat_history=history_payload)

    answer = generated.answer or fallback_answer
    audit_refusal_reason = generated.refusal_reason if not generated.answer else None
    response_status = "chat" if generated.answer else "chat_fallback"
    model_id = settings.ollama_chat_model if generated.answer else "rule_based_non_rag"

    policy_model = PolicyDecision(
        allow=True,
        reason=reason,
        policy_version="1.0",
    )

    with get_session() as session:
        persist_policy_decision(
            session,
            decision_id=policy_model.decision_id,
            run_id=run_id,
            user_id_hash=None,
            user_groups=entitlements.groups,
            policy_input={
                "query_hash_only": True,
                "smalltalk_bypass": True,
                "retrieval_mode": request.retrieval_mode,
                "effective_mode": smalltalk_mode,
                "chat_llm_used": bool(generated.answer),
                "effective_top_k": 0,
                "evidence_count": 0,
                "allowed_count": 0,
                "generation_count": 0,
            },
            policy_result=policy_model.model_dump(),
            policy_version=policy_model.policy_version,
        )
        persist_query_audit(
            session,
            run_id=run_id,
            query=request.query,
            mode=smalltalk_mode,
            response_status=response_status,
            refusal_reason=audit_refusal_reason,
            user_id=entitlements.user_id,
            email=entitlements.email,
            groups=entitlements.groups,
            evidence_rows=[],
            citation_rows=[],
            policy_decision_id=policy_model.decision_id,
            model_id=model_id,
            model_version="local" if generated.answer else "1.0",
        )

    return QueryResponse(
        run_id=run_id,
        answer=answer,
        refusal_reason=None,
        citations=[],
        policy_decision=policy_model,
    )


async def run_query_flow(request: QueryRequest, entitlements: Entitlements) -> QueryResponse:
    settings = get_settings()
    run_id = uuid4()
    effective_mode = _effective_query_mode(request)
    inferred_filters = _infer_query_filters(request.query)
    effective_filters = _merge_query_filters(request.filters, inferred_filters)

    retrieval_query = request.query
    rewritten_for_followup = False
    if request.chat_history:
        retrieval_query, rewritten_for_followup = maybe_rewrite_followup(request.query, request.chat_history)

    blocked_reason = detect_disallowed_request(request.query)
    if blocked_reason:
        return await _build_security_refusal_query_response(
            request=request,
            entitlements=entitlements,
            run_id=run_id,
            reason=blocked_reason,
        )

    if request.retrieval_mode == "chat":
        return await _build_smalltalk_query_response(
            request=request,
            entitlements=entitlements,
            run_id=run_id,
            reason="forced_chat_mode",
        )

    if request.retrieval_mode == "auto":
        auto_decision = decide_auto_retrieval_mode(request.query, request.chat_history)
        if auto_decision.mode == "chat":
            return await _build_smalltalk_query_response(
                request=request,
                entitlements=entitlements,
                run_id=run_id,
                reason=f"auto_{auto_decision.reason}",
            )

    retrieval = RetrievalService()
    policy = PolicyClient()

    if _looks_like_inventory_request(request.query):
        return await _build_inventory_query_response(
            request=request,
            entitlements=entitlements,
            run_id=run_id,
            retrieval=retrieval,
            policy=policy,
            effective_filters=effective_filters,
        )

    requested_top_k = request.top_k if request.top_k and request.top_k > 0 else settings.top_k_fused
    effective_top_k = min(requested_top_k, settings.top_k_fused)

    bundle = retrieval.retrieve_multimodal(
        query=retrieval_query,
        entitlements=entitlements,
        include_images=request.include_images,
        top_k=effective_top_k,
        query_filters=effective_filters,
    )

    allowed_nodes, effective_policy = await _authorize_nodes(
        nodes=bundle.evidence,
        entitlements=entitlements,
        policy=policy,
    )

    usable_nodes, dropped_low_value_images = _prune_low_value_visual_nodes(allowed_nodes)
    generation_cap = min(effective_top_k, settings.generation_max_evidence_nodes)
    targeted_doc_ids = _targeted_doc_ids_from_query(request.query, usable_nodes)
    selector_mode = "exact_match" if targeted_doc_ids else "none"
    if not targeted_doc_ids:
        targeted_doc_ids = await _llm_targeted_doc_ids_from_query(request.query, usable_nodes)
        if targeted_doc_ids:
            selector_mode = "llm_selector"
    scoped_nodes = [
        node
        for node in usable_nodes
        if not targeted_doc_ids or str((node.payload or {}).get("doc_id") or (node.payload or {}).get("file_id") or "") in targeted_doc_ids
    ]
    scoped_nodes = _cap_tabular_generation_nodes(
        scoped_nodes,
        max_blocks_per_sheet=max(1, settings.generation_tabular_max_blocks_per_sheet),
    )

    generation_nodes = diversify_by_doc(
        scoped_nodes,
        per_doc_max=max(1, settings.generation_doc_diversity_max_chunks),
        final_k=max(1, generation_cap),
    )
    candidate_citations = [_citation_from_payload(node.payload, node.node_id) for node in generation_nodes]

    answerability = await judge_answerability(
        query=request.query,
        mode=effective_mode,
        evidence=generation_nodes,
        citations=candidate_citations,
    )
    support_idx = set(answerability.support_indices)
    judged_nodes = [
        node for idx, node in enumerate(generation_nodes, start=1) if idx in support_idx
    ] if support_idx else []
    judged_citations = [
        citation for idx, citation in enumerate(candidate_citations, start=1) if idx in support_idx
    ] if support_idx else []

    if answerability.answerable and judged_nodes and judged_citations:
        generated = await generate_grounded_answer(
            query=request.query,
            mode=effective_mode,
            evidence=judged_nodes,
            citations=judged_citations,
            include_images=request.include_images,
        )
    else:
        if (answerability.reason or "") == "insufficient_evidence":
            clarification_response = await _build_clarification_query_response(
                request=request,
                entitlements=entitlements,
                run_id=run_id,
                retrieval=retrieval,
                policy=policy,
                effective_filters=effective_filters,
                effective_policy=effective_policy,
            )
            if clarification_response is not None:
                return clarification_response
        generated = GenerationResult(
            answer=settings.refusal_text,
            refusal_reason=answerability.reason or "insufficient_evidence",
        )

    if generated.refusal_reason:
        citations: list[Citation] = []
    else:
        used_idx = set(generated.used_citation_indices)
        citations = [
            citation for idx, citation in enumerate(judged_citations, start=1) if idx in used_idx
        ]

    response_status = "refused" if generated.refusal_reason else "answered"
    assert effective_policy is not None
    policy_model = PolicyDecision(
        decision_id=effective_policy.decision_id,
        allow=effective_policy.allow,
        reason=effective_policy.reason,
        policy_version=effective_policy.policy_version,
    )

    evidence_rows = [
        {
            "node_id": node.node_id,
            "doc_id": node.payload.get("doc_id") or node.payload.get("file_id"),
            "page": node.payload.get("page"),
            "chunk_id": node.payload.get("chunk_id"),
            "modality": node.payload.get("modality"),
            "score": node.score,
            "payload": node.payload,
        }
        for node in allowed_nodes
    ]
    citation_rows = [c.model_dump() for c in citations]

    with get_session() as session:
        persist_policy_decision(
            session,
            decision_id=policy_model.decision_id,
            run_id=run_id,
            user_id_hash=None,
            user_groups=entitlements.groups,
            policy_input={
                "query_hash_only": True,
                "retrieval_mode": request.retrieval_mode,
                "followup_rewrite_applied": rewritten_for_followup,
                "filters": effective_filters.model_dump() if effective_filters else None,
                "inferred_filters": inferred_filters.model_dump() if inferred_filters else None,
                "effective_top_k": effective_top_k,
                "evidence_count": len(bundle.evidence),
                "allowed_count": len(allowed_nodes),
                "usable_count": len(usable_nodes),
                "dropped_low_value_image_count": dropped_low_value_images,
                "targeted_doc_ids": sorted(targeted_doc_ids),
                "target_selector_mode": selector_mode,
                "scoped_count": len(scoped_nodes),
                "generation_count": len(generation_nodes),
                "candidate_citation_count": len(candidate_citations),
                "answerability_answerable": answerability.answerable,
                "answerability_reason": answerability.reason,
                "answerability_source": answerability.source,
                "answerability_support_count": len(judged_citations),
                "used_citation_count": len(citations),
            },
            policy_result=policy_model.model_dump(),
            policy_version=policy_model.policy_version,
        )
        persist_query_audit(
            session,
            run_id=run_id,
            query=request.query,
            mode=effective_mode,
            response_status=response_status,
            refusal_reason=generated.refusal_reason,
            user_id=entitlements.user_id,
            email=entitlements.email,
            groups=entitlements.groups,
            evidence_rows=evidence_rows,
            citation_rows=citation_rows,
            policy_decision_id=policy_model.decision_id,
            model_id=settings.ollama_chat_model,
            model_version="local",
        )

    return QueryResponse(
        run_id=run_id,
        answer=generated.answer,
        refusal_reason=generated.refusal_reason,
        citations=citations,
        policy_decision=policy_model,
    )
