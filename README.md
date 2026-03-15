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

Supported content types in the current MVP:
- local: `txt`, `md`, `pdf`, `docx`, `xlsx`
- Google Drive: Google Docs, Google Sheets, `pdf`, `docx`, `txt`, `xlsx`
- excluded in this iteration: `csv`, legacy `xls`, spreadsheet charts/embedded objects, macros/VBA

## Synthetic VC Dataset (Committed)
This repository now includes a synthetic venture-capital dataset ZIP for reproducible demos and capstone validation:
- [tests/data/synthetic/vc_drive_venture_fund_sintetico.zip](tests/data/synthetic/vc_drive_venture_fund_sintetico.zip)
- Dataset scope: 204 files total
- File types inside ZIP: `pdf` (40), `docx` (46), `txt` (27), `md` (27), `xlsx` (42), `csv` (21), `json` (1)
- Important: `csv` files are intentionally present in the synthetic corpus but are skipped by current ingestion rules

Usage:
1. Unzip locally or upload extracted folders to Google Drive.
2. Ingest from the Drive root folder ID (recursive).
3. Validate retrieval/citations with Phase 3/4 query prompts.

## Implemented Scope
This repository is scaffolded and implemented across the planned phases in a single MVP baseline:
1. Phase 0 scaffold + compose stack
2. Phase 1 Google Drive first vertical slice (ingest -> index -> query -> citations)
3. Phase 2 auth/policy/audit wiring
4. Phase 3 multimodal PDF page image extraction + optional OCR
5. Phase 4 multimodal retrieval fusion + optional VLM routing hook (disabled by default)
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
For large recursive ingests, the API uses the native Drive downloader by default (`GOOGLE_DRIVE_USE_READER=false`) because the LlamaHub `GoogleDriveReader` path is significantly slower on big folder trees. The reader path remains available as an explicit compatibility option.

## 5) Ingest Google Drive
Drive ingestion is recursive from the provided root folder:
- supported files inside nested subfolders are ingested automatically
- unsupported files are skipped with logs
- document metadata preserves relative Drive path via `drive_path`, `folder_path`, `root_folder_id`
- ingestion stores `folder_ancestors` and `path_ancestors` for retrieval-time folder/path filtering in Qdrant
- Google Sheets are exported to `xlsx` through Drive API and parsed through the same tabular pipeline as Excel workbooks
- Streamlit UI uses async ingestion + polling so long-running Drive jobs do not fail on request timeout

### Make target
```bash
make ingest-gdrive FOLDER_ID=<drive_folder_id>
```

### PowerShell / direct Python equivalent
```powershell
python scripts/ingest_gdrive.py --folder-id <drive_folder_id> --auth-mode oauth --api-url http://localhost:8000
```

### Async ingestion endpoints
For UI or custom clients that should not wait on a long-running HTTP request:
- `POST /ingest/gdrive/async`
- `POST /ingest/local/async`
- `GET /ingest/runs/{ingestion_run_id}`

The async start endpoint returns immediately with `ingestion_run_id`; clients should poll the run-status endpoint until `status != running`.

## 6) Local Folder Fallback Ingestion
```bash
make ingest-local PATH_ARG=./tests/data/sample_docs ACL_SIDECAR=./tests/data/sample_docs/acl_map.yaml
```

or
```powershell
python scripts/ingest_local.py --path ./tests/data/sample_docs --acl-sidecar ./tests/data/sample_docs/acl_map.yaml --api-url http://localhost:8000
```

Local `xlsx` workbooks are parsed into:
- one workbook summary node
- one sheet summary node per non-empty sheet
- row-block nodes (`rows_per_block=25` by default)

Hidden sheets are indexed and marked with `sheet_hidden=true`. Empty sheets are skipped.

## 7) Query API Example
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "query":"Summarize the public handbook",
    "mode":"qa",
    "retrieval_mode":"auto",
    "include_images":true,
    "user_context":{"email":"hr.user@example.com","domain":"example.com","groups":["hr"]}
  }'
```

Expected:
- `run_id`
- grounded `answer` (with citation markers like `[1]` when factual)
- `citations[]` filtered to only citations actually referenced in the answer
- `policy_decision`

### Conversational turns (no retrieval)
Short acknowledgements (for example `Sounds good!`, `Thanks`, `Hola`) are handled as chat turns.
- no vector retrieval
- no evidence/citation requirement
- still audited with policy reason `auto_smalltalk`

Retrieval mode options in `/query`:
- `auto` (default): route between chat and grounded retrieval based on the current turn plus recent chat history
- `rag`: always retrieve evidence and enforce grounding/citations
- `chat`: never retrieve; respond with the local LLM (Ollama), using chat history context
- UI keeps a separate conversation context per mode (`auto`, `rag`, `chat`) to avoid cross-mode contamination

Follow-up handling:
- send `chat_history` (recent `{role, content}` turns) and API rewrites short follow-ups (for example `And in 2008?`) into standalone retrieval queries.

Evidence sufficiency gate:
- after retrieval, the API runs an answerability check over the top evidence candidates
- it prefers a local LLM judge and falls back to heuristics if the judge is disabled or unavailable
- only judged supporting nodes are sent to generation
- if the evidence is not sufficient to answer with citations, the API either refuses or returns a clarification prompt with likely document candidates when it can narrow the request safely
- clarification fallback is metadata-driven: it suggests likely authorized files (for example commitment registers, schedules, or FAQs) and proposes narrower follow-up prompts instead of fabricating an answer
- natural-language constraints such as `Drive PDFs` are converted into structured retrieval filters (`sources=["google_drive"]`, `mime_types=["application/pdf",".pdf"]`) before search
- path constraints are supported in natural language (for example `use only 03_Portfolio/CliniFlow`) and translated into retrieval-time filters using indexed `folder_ancestors` / `path_ancestors`
- low-value image hits without useful OCR/extracted text are dropped before answerability/generation so the model does not cite placeholder visual content
- spreadsheet/excel/google-sheet hints are translated into structured retrieval filters before search
- tabular citations include `sheet_name`, `row_start`, `row_end`, and `cell_range` when the answer is grounded in workbook evidence
- inventory-style questions (`what files do you have`, `list exact indexed file names`) are answered from indexed document metadata instead of semantic chunk content

## 8) Keycloak Login (Phase 2)
Imported realm: `secure-rag`
- `hr.user / ChangeMe123!`
- `finance.user / ChangeMe123!`
Groups:
- `HR`
- `Finance`

Set `AUTH_ENABLED=true` in `.env` to enforce JWT validation.
### Streamlit Auto Token Refresh
The UI now supports Keycloak login directly in the sidebar (`Auth (Keycloak)`):
- click `Login` with Keycloak username/password
- access token + refresh token are stored in Streamlit session state
- token refresh is attempted automatically before expiry and retried once on `401`
- optional manual override remains available in `Manual bearer token`

Relevant env vars:
- `KEYCLOAK_CLIENT_ID` (default `secure-rag-api`)
- `KEYCLOAK_TOKEN_URL` (default `<issuer>/protocol/openid-connect/token`)
- `UI_QUERY_TIMEOUT_SEC` (default `300`)
- `UI_TOKEN_REFRESH_SKEW_SEC` (default `30`)
- `UI_DEFAULT_USERNAME` / `UI_DEFAULT_PASSWORD` (optional local defaults)
- `DOMAIN_CONTEXT_HINT` (optional domain orientation injected into system prompts; factual answers still require retrieved evidence + citations)
- `VLM_ROUTER` (`disabled` by default; set non-disabled value to activate VLM router hook)
- `VLM_ROUTER_MAX_IMAGES` (max image paths passed to VLM router per answer)

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
- Prompt-level security blocks for injection, auth-bypass, and outbound exfiltration requests (`refusal_reason=policy_violation`)
- Append-only audit tables in Postgres (triggers deny update/delete)
- Public LLM disabled by default; outbound disabled by default in prod mode

## 11) Testing
### Unit + integration subset
```bash
pytest -q
```

### Security regression prompts
```bash
python scripts/security_regression.py --url http://localhost:8000 --cases tests/redteam/prompts.json
```

### Load test
```bash
python scripts/load_test.py --url http://localhost:8000/query --requests 200 --concurrency 8 --max-failure-rate 0.05
```

Auth-enabled load test (token auto-refresh):
```bash
python scripts/load_test.py \
  --url http://localhost:8000/query \
  --requests 40 \
  --concurrency 2 \
  --timeout 240 \
  --max-failure-rate 0.20 \
  --token-url http://localhost:8080/realms/secure-rag/protocol/openid-connect/token \
  --client-id secure-rag-api \
  --username hr.user \
  --password ChangeMe123!
```

### Multimodal benchmark subset
```bash
python scripts/benchmark_multimodal.py --api-url http://localhost:8000 --dataset tests/redteam/prompts.yaml
```

## 12) Backup / Restore
```bash
python scripts/backup_restore.py backup
python scripts/backup_restore.py restore --backup-dir backups --skip-postgres --skip-qdrant
```

To restore data from latest backup:
```bash
python scripts/backup_restore.py restore --backup-dir backups
```

To restore from a specific manifest:
```bash
python scripts/backup_restore.py restore --manifest backups/<timestamp>/manifest.json
```

## 12.1) Phase 5 End-to-End Verification
```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_phase5.ps1 `
  -ApiUrl http://localhost:8000 `
  -CasesPath tests/redteam/prompts.json `
  -LoadRequests 10 `
  -LoadConcurrency 1 `
  -MaxFailureRate 0.20 `
  -Username hr.user `
  -Password ChangeMe123!
```

This checks:
- health/readiness/metrics
- secure runtime toggles (`ALLOW_OUTBOUND=false`, `ALLOW_PUBLIC_LLM=false`)
- security regression suite
- load test (p50/p95/throughput and failure-rate gate)
- backup creation + restore-flow simulation using generated manifest

If `AUTH_ENABLED=true`, provide either:
- `-BearerToken <jwt>`
- or `-Username/-Password` (Keycloak direct grant) so security/load checks do not fail with `401`.

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
  docker compose exec ollama ollama pull llama3.2:3b
  docker compose exec ollama ollama pull nomic-embed-text
  ```
- `llama3.2:3b` is the default CPU-first chat model. Use a larger model such as `llama3.1:8b` only when local memory allows it.
- In production environments, pre-stage model artifacts and keep outbound egress disabled.



## 15) High-Scale Querying (Hundreds/Thousands of Docs)
The query pipeline now includes:
- broad candidate retrieval + optional local reranking (`ENABLE_RERANK=true`)
- doc-diversity caps to avoid one file dominating context
- map-reduce summarization path for explicit per-document summaries; otherwise summaries default to an integrated synthesis that only cites documents actually used
- metadata filters in `/query` (`source`, `mime_types`, `doc_ids`, `tags`, `modified_from`, `modified_to`)
- query-to-filter extraction for common hints like `Drive PDFs`, `local files`, `docx`, and `txt`
- query-to-filter extraction for `excel`, `xlsx`, `spreadsheet`, `workbook`, `sheet`, and `google sheets`
- page-aware PDF chunking plus title/page-enriched text embeddings for better recall
- workbook-aware chunking for `xlsx` / Google Sheets:
  - workbook summary
  - sheet summary
  - row blocks with headers and row ranges
- smaller default chunks to improve precision on fact-style questions
- merge of tiny trailing fragments to avoid low-value micro-chunks

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
- `ANSWERABILITY_ENABLED`
- `ANSWERABILITY_USE_LLM`
- `ANSWERABILITY_MAX_EVIDENCE_NODES`
- `ANSWERABILITY_MAX_CHARS_PER_NODE`

Chunking defaults live in `config/config.yml`:
- `chunk_size: 450`
- `chunk_overlap: 80`
- `min_chunk_chars: 180`

Tabular defaults:
- `tabular.rows_per_block: 25`
- `tabular.max_columns: 20`
- `tabular.max_cell_chars: 200`
- `tabular.max_blocks_per_sheet: 200`
- `tabular.max_sheets_per_workbook: 50`
- `summarization.tabular_max_blocks_per_sheet: 2`

After changing chunking settings, re-ingest documents so Qdrant stores the new node layout.

Tabular query examples:
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "query":"What spreadsheets about revenue or portfolio performance do you have?",
    "mode":"qa",
    "retrieval_mode":"rag",
    "include_images":false,
    "user_context":{"email":"hr.user@example.com","domain":"example.com","groups":["hr"]}
  }'
```

```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "query":"In pipeline_metrics.xlsx, summarize the Revenue sheet with citations.",
    "mode":"summarize",
    "retrieval_mode":"rag",
    "include_images":false,
    "user_context":{"email":"hr.user@example.com","domain":"example.com","groups":["hr"]}
  }'
```

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

## 17) Phase 2 Verification (JWT + OPA + Audit)
1. Enable auth in `.env`:
```bash
AUTH_ENABLED=true
```
2. Keep internal issuer for API cert fetch and add host alias for tokens minted from localhost:
```bash
KEYCLOAK_ISSUER=http://keycloak:8080/realms/secure-rag
KEYCLOAK_ISSUER_ALIASES=http://localhost:8080/realms/secure-rag
```
3. Recreate API:
```bash
docker compose up -d --force-recreate api
```
4. Run Phase 2 gate:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/verify_phase2.ps1
```

Expected checks:
- anonymous query blocked (`401`)
- Keycloak token issuance for HR/Finance users
- HR JWT cannot retrieve Finance-only doc
- Finance JWT cannot retrieve HR-only doc
- audit `run_id` generated for both

## 18) Phase 2.5 Admin Console (Users, Groups, Mapping, Access Preview)
The API now exposes secured admin endpoints under `/admin/*`.

Prerequisites:
- `AUTH_ENABLED=true`
- admin token belongs to a group listed in `ADMIN_AUTHORIZED_GROUPS` (default: `admin`)
- Keycloak admin service credentials configured:
  - `KEYCLOAK_ADMIN_URL`
  - `KEYCLOAK_REALM`
  - `KEYCLOAK_ADMIN_USER`
  - `KEYCLOAK_ADMIN_PASSWORD`
  - `KEYCLOAK_ADMIN_CLIENT_ID`

Default Admin test user (realm import):
- `admin.user / ChangeMe123!`

Admin endpoints:
- `GET /admin/settings/drive-group-map`
- `PUT /admin/settings/drive-group-map`
- `POST /admin/access/preview`
- `GET /admin/keycloak/groups`
- `GET /admin/keycloak/users`
- `POST /admin/keycloak/users`
- `PUT /admin/keycloak/users/{user_id}/groups`
- `POST /admin/sync/gdrive`

Streamlit now includes an **Admin** tab for these operations.

## 19) Drive ACL Sync Mode (Current vs Live)
Current behavior:
- Drive ACLs are captured at ingestion time and stored in payload metadata.
- Query ACL enforcement is strict, but ACL freshness depends on re-ingestion.

Production upgrade path for real-time ACL:
- use Drive Changes API/webhooks to detect permission changes
- enqueue delta re-index jobs for affected docs/chunks only
- optionally enforce live policy checks against an entitlement cache

## 20) Phase 3 Verification (Multimodal Ingestion)
Phase 3 adds PDF page-image plus embedded-image extraction for both:
- local PDF ingestion
- Google Drive PDF ingestion

Multimodal image nodes now also preserve:
- `visual_text_source` = `ocr` | `page_text` | `placeholder`
- OCR quality metadata (`ocr_char_count`, `ocr_token_count`, `has_useful_ocr`)
- page-level linkage back to text chunks (`linked_text_node_ids`, `linked_chunk_ids`, `linked_text_preview`)

Behavior notes:
- when OCR is disabled or empty, page images fall back to the PDF page text for indexing context
- low-value image hits with no useful OCR/page text are excluded before answerability/generation
- this keeps image retrieval usable on CPU while avoiding placeholder visual claims

Ingestion responses now include:
- `text_nodes_indexed`
- `image_nodes_indexed`

Run Phase 3 tests:
```bash
docker compose exec api pytest -q tests -k "multimodal_ingest or ocr"
```

Verify Drive PDF image indexing (folder must include at least one PDF):
```powershell
$body = @{
  folder_id = "<drive_folder_id>"
  auth_mode = "oauth"
  dry_run = $false
  dataset_source = "google_drive"
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/ingest/gdrive `
  -ContentType "application/json" `
  -Body $body
```

Expected:
- `added` >= 1
- `text_nodes_indexed` >= 1
- `image_nodes_indexed` >= 1 (if PDFs exist)

Optional multimodal query smoke test:
```powershell
$q = @{
  query = "Summarize visual and textual evidence from Drive PDFs with citations."
  mode = "summarize"
  include_images = $true
  top_k = 12
  filters = @{ sources = @("google_drive"); mime_types = @("application/pdf") }
  user_context = @{ email = "<your_email>"; domain = "<your_domain>"; groups = @("HR") }
}
Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/query `
  -ContentType "application/json" `
  -Body ($q | ConvertTo-Json -Depth 12)
```









