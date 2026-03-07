# Secure Multimodal RAG Enterprise MVP

Production-oriented, locally deployable secure multimodal RAG MVP with:
- FastAPI backend (`/ingest`, `/query`, `/feedback`, `/runs`)
- Streamlit UI
- Qdrant vector store (retrieval-time ACL payload filters)
- Postgres metadata + append-only audit logs
- Keycloak (OIDC) + OPA policy engine
- Google Drive ingestion via LlamaIndex/LlamaHub (Phase 1 priority)
- Local-folder ingestion fallback
- CPU-first local inference path (Ollama)

## Implemented Scope
This repository is scaffolded and implemented across the planned phases in a single MVP baseline:
1. Phase 0 scaffold + compose stack
2. Phase 1 Google Drive first vertical slice (ingest -> index -> query -> citations)
3. Phase 2 auth/policy/audit wiring
4. Phase 3 multimodal PDF page image extraction + optional OCR
5. Phase 4 multimodal retrieval fusion + VLM routing placeholder
6. Phase 5 hardening scripts (metrics, load test, backup/restore, red-team regression)

## Repository Layout
```text
secure_rag_enterprise/
  api/
  ui/
  config/
  infra/
  scripts/
  tests/
  docker-compose.yml
  .env.example
  Makefile
  ARCHITECTURE.md
```

## Prerequisites
- Docker + Docker Compose
- Python 3.11+ (for local scripts/tests)
- Optional: GNU `make` (Windows can use direct `python ...` commands)

## 1) Setup
```bash
cp .env.example .env
mkdir -p data/google artifacts backups
```

## 2) Start Stack
```bash
docker compose up --build
```
Services:
- API: `http://localhost:8000`
- UI: `http://localhost:8501`
- Qdrant: `http://localhost:6333`
- Postgres: `localhost:5432`
- Keycloak: `http://localhost:8080`
- OPA: `http://localhost:8181`

## 3) Phase 0 Verification
```bash
docker compose ps
curl http://localhost:8000/health/liveness
curl http://localhost:8000/health/readiness
```

## 4) Google Drive OAuth Setup (Phase 1 Priority)
1. In Google Cloud Console, enable Drive API.
2. Create OAuth Desktop credentials and download `credentials.json`.
3. Place it at the path referenced by `GOOGLE_CREDENTIALS_PATH` (default `/data/google/credentials.json` in container).
4. Set `DRIVE_FOLDER_ID` in `.env` or pass it in request payload.
5. First run generates `token.json` at `GOOGLE_TOKEN_PATH`; never commit it.

Note: `DRIVE_AUTH_MODE=oauth` is default. `service_account` remains available as fallback.

## 5) Ingest Google Drive
### Make target
```bash
make ingest-gdrive FOLDER_ID=<drive_folder_id>
```

### PowerShell / direct Python equivalent
```powershell
python scripts/ingest_gdrive.py --folder-id <drive_folder_id> --auth-mode oauth --api-url http://localhost:8000
```

## 6) Local Folder Fallback Ingestion
```bash
make ingest-local PATH_ARG=./tests/data/sample_docs ACL_SIDECAR=./tests/data/sample_docs/acl_map.yaml
```

or
```powershell
python scripts/ingest_local.py --path ./tests/data/sample_docs --acl-sidecar ./tests/data/sample_docs/acl_map.yaml --api-url http://localhost:8000
```

## 7) Query API Example
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "query":"Summarize the public handbook",
    "mode":"qa",
    "include_images":true,
    "user_context":{"email":"hr.user@example.com","domain":"example.com","groups":["hr"]}
  }'
```

Expected:
- `run_id`
- grounded `answer`
- `citations[]` with `doc/page/chunk/node`
- `policy_decision`

## 8) Keycloak Login (Phase 2)
Imported realm: `secure-rag`
- `hr.user / ChangeMe123!`
- `finance.user / ChangeMe123!`
Groups:
- `HR`
- `Finance`

Set `AUTH_ENABLED=true` in `.env` to enforce JWT validation.

### Keycloak Group -> Drive Group Mapping
When Drive files are shared with Google Groups (for example `hr-shared@enterprise.com`), map IdP groups to Drive ACL groups:

```bash
DRIVE_GROUP_MAP_JSON={"hr":["hr-shared@enterprise.com"],"finance":["finance-shared@enterprise.com"]}
```

Notes:
- Keep Keycloak groups as business roles (`HR`, `Finance`).
- Share Drive files/folders to group emails in Google Drive.
- Re-run ingestion after permission changes so ACL payloads refresh.
## 9) OPA Policy
OPA policy file: `infra/opa/policy.rego`
- authenticated user required
- allow via `allowed_users` / `allowed_groups`
- transitional email/domain checks for Drive ACL
- default deny + fail-closed when OPA unavailable (`OPA_FAIL_CLOSED=true`)

## 10) Security Invariants in MVP
- Retrieval-time ACL filter in Qdrant (no post-filter leakage)
- Defense-in-depth: additional payload ACL check + OPA call
- Citation requirement for factual answers; refusal when insufficient evidence
- Append-only audit tables in Postgres (triggers deny update/delete)
- Public LLM disabled by default; outbound disabled by default in prod mode

## 11) Testing
### Unit + integration subset
```bash
pytest -q
```

### Security regression prompts
```bash
python scripts/security_regression.py --url http://localhost:8000 --cases tests/redteam/prompts.yaml
```

### Load test
```bash
python scripts/load_test.py --url http://localhost:8000/query --requests 200 --concurrency 8
```

### Multimodal benchmark subset
```bash
python scripts/benchmark_multimodal.py --api-url http://localhost:8000 --dataset tests/redteam/prompts.yaml
```

## 12) Backup / Restore
```bash
python scripts/backup_restore.py backup
python scripts/backup_restore.py restore
```

## 13) ACL Demo (HR vs Finance isolation)
1. Ingest `tests/data/sample_docs`.
2. Query as HR context for finance-only prompt -> refusal / no finance evidence.
3. Query as Finance context for HR-only prompt -> refusal / no HR evidence.
4. Query public prompt -> allowed with citations.

## 14) Notes
- For CPU-only operation, start Ollama service (compose profile):
  ```bash
  docker compose --profile ollama up -d ollama
  ```
- Pull required local models explicitly (first-time network use):
  ```bash
  docker compose exec ollama ollama pull llama3.1:8b
  docker compose exec ollama ollama pull nomic-embed-text
  ```
- In production environments, pre-stage model artifacts and keep outbound egress disabled.



## 15) High-Scale Querying (Hundreds/Thousands of Docs)
The query pipeline now includes:
- broad candidate retrieval + optional local reranking (`ENABLE_RERANK=true`)
- doc-diversity caps to avoid one file dominating context
- map-reduce summarization path for multi-document summaries
- metadata filters in `/query` (`source`, `mime_types`, `doc_ids`, `tags`, `modified_from`, `modified_to`)

Example filtered query (Drive-only PDFs modified after Jan 1, 2026):
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "query":"Summarize the most relevant venture capital findings with citations",
    "mode":"summarize",
    "top_k":8,
    "include_images":false,
    "filters":{
      "sources":["google_drive"],
      "mime_types":["application/pdf"],
      "modified_from":"2026-01-01T00:00:00Z"
    },
    "user_context":{"email":"jburguillos.ieu2021@student.ie.edu","domain":"student.ie.edu","groups":["HR"]}
  }'
```

Tuning knobs (`.env`):
- `RETRIEVAL_CANDIDATE_MULTIPLIER`
- `RETRIEVAL_CANDIDATE_MAX`
- `RETRIEVAL_DOC_DIVERSITY_MAX_CHUNKS`
- `ENABLE_RERANK`
- `RERANK_TOP_CANDIDATES`
- `GENERATION_MAX_EVIDENCE_NODES`
- `GENERATION_DOC_DIVERSITY_MAX_CHUNKS`
- `SUMMARIZE_MAP_MAX_DOCS`
- `SUMMARIZE_MAP_CHARS_PER_DOC`

## 16) Phase 1 Exit Gate (Before Phase 2)
Run this once before starting Phase 2 to freeze and verify the baseline.

### A) Repeatable verification (PowerShell)
```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_phase1.ps1 `
  -ApiUrl http://localhost:8000 `
  -DriveFolderId <your_folder_id> `
  -DriveEmail <your_google_email> `
  -DriveDomain <your_domain> `
  -DriveGroups HR
```

If you only want local checks:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_phase1.ps1 -SkipDrive
```

### B) Create backups before auth changes
```bash
python scripts/backup_restore.py backup
```
Backup artifacts are written to `backups/<timestamp>/`:
- `postgres.sql`
- `manifest.json`
- Qdrant snapshot files (`qdrant_<collection>_<name>.snapshot` when available)

### C) Tag the Phase 1 baseline
```bash
git tag phase1-mvp
git push origin phase1-mvp
```

### D) Secret hygiene quick check
```bash
git status --short --ignored
```
Confirm `.env` and `data/` remain ignored (`!!`).
