# Architecture Overview

## Components
- `ui` (Streamlit): chat + citations + evidence viewer + feedback.
- `api` (FastAPI): ingestion, retrieval, generation, authz, audit.
- `qdrant`: vector storage with payload metadata and ACL fields.
- `postgres`: document registry, ingestion state, append-only query audits.
- `keycloak`: OIDC IdP for SSO/JWT claims.
- `opa`: ABAC/RBAC policy decisions.
- `ollama` (optional compose profile): local CPU model serving.

## Data Flow
1. Ingestion
- Google Drive via LlamaHub `GoogleDriveReader` (OAuth default).
- Supported files: Google Docs, Google Sheets (exported to XLSX), PDF, DOCX, TXT, XLSX.
- Metadata + permissions extracted and attached to each node.
- Text nodes chunked and embedded into Qdrant `text_nodes`.
- PDF page renders + embedded images (local and Google Drive PDFs) embedded into `image_nodes` (optional OCR text attached per image node).
- Document registry/upsert persisted in Postgres.

2. Query
- User context from transitional payload (Phase 1) or JWT claims (Phase 2+).
- ACL hard filter applied in Qdrant query (`is_public`, email/domain/user/group matches).
- Dense + BM25 hybrid retrieval for text; image retrieval optional.
- Late fusion via RRF for multimodal evidence.
- OPA called for policy decision logging and defense in depth.
- Generation constrained to retrieved context only.
- Citation requirement enforced; refusal on insufficient evidence.

3. Audit
- `run_id` per query.
- Query hash (raw query optional via config).
- Retrieved node IDs, cited node IDs, policy decision, model/config versions.
- Append-only enforcement in Postgres via DB triggers.

## Security Controls
- Retrieval-time ACL filtering in vector DB.
- Secondary ACL payload checks in API.
- OPA policy decision path with fail-closed default.
- JWT validation against Keycloak OIDC certs.
- No secrets in repository (`.env.example` only).
- Public LLM APIs disabled by default.

## Upgrade Paths
- VLM router hook is integrated in generation and remains disabled by default (`VLM_ROUTER=disabled`).
- GPU VLM backend can replace the no-op router without API contract changes.
- Service-account/domain delegation for Google Workspace later.
- Replace Streamlit with Next.js without API contract changes.

