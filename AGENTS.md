# AGENTS.md — Secure Multimodal RAG MVP (Codex Instructions)

## Project goal
Build a production-oriented MVP of a secure, locally deployable (CPU-first) multimodal RAG:
- Google Drive ingestion first (LlamaIndex + LlamaHub)
- Qdrant for vectors (ACL payload filtering)
- Postgres for metadata + audit logs (append-only)
- FastAPI backend + Streamlit UI
- Keycloak (OIDC) + OPA policies (ABAC/RBAC) in later phases
- No public LLM APIs in production mode

## Non-negotiable security invariants
1) **No ACL leakage**: unauthorized content must never be retrieved or sent to the LLM.
2) **Retrieval-time filtering**: apply ACL filters inside the vector DB query (no post-filter).
3) **Citations or refusal**: factual answers must include ≥1 citation; otherwise refuse.
4) **No secrets in repo**: never commit tokens, credentials, or private keys.
5) **No outbound calls by default**: production mode must not call external LLM APIs.

## Phased delivery (must follow)
- Phase 0: Scaffold + docker compose + health checks
- Phase 1: Google Drive ingestion (LlamaHub) + text RAG + citations + tests + README
- Phase 2: Keycloak auth + OPA policy + audit logs + ACL regression tests
- Phase 3: Multimodal ingestion (PDF page images + optional OCR) + image index
- Phase 4: Late-fusion retrieval + VLM routing interface (GPU path)
- Phase 5: Hardening (monitoring hooks, load tests, security regression suite)

**Do not implement later phases until earlier phases pass tests and are documented.**

## Coding standards
- Python 3.11+
- Use type hints for public functions
- Add docstrings for modules and complex functions
- Keep functions small; prefer dependency injection for clients (Qdrant, Postgres, OPA)
- Centralize configuration in `config.yml` + environment variables

## Testing requirements
- Every PR/change must:
  - add/maintain tests for security invariants (especially ACL filtering)
  - run `pytest -q` successfully
- Include at least:
  - public doc allowed
  - unauthorized user blocked
  - citation requirement enforced
  - prompt-injection regression cases (basic)

## Verification commands (must keep updated)
- `docker compose up --build`
- `pytest -q`
- `make ingest-gdrive` (or equivalent script)
- `make load-test` (or equivalent)

## Documentation requirements
- Update README whenever behavior, env vars, or setup changes.
- Provide `.env.example` and never store secrets.
- Document any model downloads explicitly.

## What to avoid
- Don’t add unnecessary frameworks.
- Don’t hardcode credentials.
- Don’t “fake” security controls—if not implemented, mark as TODO and document.
