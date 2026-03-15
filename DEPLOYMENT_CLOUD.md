# Cloud Deployment Runbook

This runbook describes how to deploy `secure_rag_enterprise` in cloud environments safely.

## Recommended Deployment Path
1. Pilot / capstone demo: single private VM with Docker Compose.
2. Production hardening: managed Kubernetes + external secrets + managed observability.

Start with path `1` unless you already require multi-node HA.

## What "Cloud Ready" Means Here
1. Security and regression checks are automated (`verify_phase5.ps1`).
2. AuthN/AuthZ path works (Keycloak + OPA + retrieval-time ACL).
3. Backup/restore scripts are available.
4. Stateful data must be persisted in cloud volumes (`postgres`, `qdrant`, `ollama`, `data/google`).

## Option A: Fast Pilot on One VM (Recommended First)
Use this for stakeholder demos and initial pilots.

### A1) VM baseline
1. Create Ubuntu 22.04/24.04 VM (8 vCPU, 32 GB RAM minimum; more if using bigger local models).
2. Attach persistent disk for application state.
3. Open inbound ports:
   - `22` (SSH)
   - `80/443` (reverse proxy)
4. Keep `5432`, `6333`, `8181`, `8080` private (no public exposure).

### A2) Install runtime
1. Install Docker + Docker Compose plugin.
2. Install `git`.
3. Clone repo:
   - `git clone https://github.com/jburguillos/secure_rag_enterprise.git`
   - `cd secure_rag_enterprise`

### A3) Configure environment
1. Copy env template:
   - `cp .env.example .env`
2. Set strong secrets:
   - `POSTGRES_PASSWORD`
   - `KEYCLOAK_ADMIN_PASSWORD`
3. Enforce secure flags:
   - `APP_MODE=prod`
   - `ALLOW_OUTBOUND=false`
   - `ALLOW_PUBLIC_LLM=false`
   - `AUTH_ENABLED=true`
4. For public domain auth, set:
   - `KEYCLOAK_ISSUER=https://<your-domain>/realms/secure-rag`
   - `KEYCLOAK_ISSUER_ALIASES=http://keycloak:8080/realms/secure-rag`
5. Add Google OAuth credentials file to mounted path:
   - host path `./data/google/credentials.json`
6. Keep `token.json` in mounted storage (`./data/google/token.json`), never commit.

### A4) TLS and reverse proxy
1. Put Nginx or Traefik in front.
2. Route:
   - `/` -> `ui:8501`
   - `/api` (or direct subdomain) -> `api:8000`
   - `/auth` (or Keycloak subdomain) -> `keycloak:8080`
3. Enable HTTPS certificates (Let's Encrypt or enterprise PKI).

### A5) Launch
1. Pull/build:
   - `docker compose up -d --build`
2. Optional Ollama profile:
   - `docker compose --profile ollama up -d ollama`
3. Pull models:
   - `docker compose exec ollama ollama pull nomic-embed-text`
   - `docker compose exec ollama ollama pull llama3.1:8b`

### A6) Post-deploy verification
1. API health:
   - `curl http://localhost:8000/health/liveness`
2. Full hardening check:
   - `powershell -ExecutionPolicy Bypass -File scripts/verify_phase5.ps1 -ApiUrl http://localhost:8000 -Username <user> -Password <pass>`
3. Ingest smoke test (local and Drive).
4. Query with citations and ACL isolation test.

## Option B: Production-Grade Kubernetes (Next Step)
Use this when moving from pilot to enterprise rollout.

### B1) Core architecture
1. Deploy API and UI as separate Deployments.
2. Use managed Postgres and managed vector DB if policy permits, otherwise stateful sets with backup policy.
3. Keep Keycloak and OPA internal services.
4. Use ingress controller + TLS + WAF.

### B2) Secrets and config
1. Put all secrets in cloud secret manager (or Vault).
2. Inject as env vars at runtime.
3. Do not store `.env` with real secrets in repo or image.

### B3) Ops requirements
1. Horizontal autoscaling for API.
2. Centralized logs + metrics + alerts.
3. Scheduled backups + periodic restore drills.
4. Rolling deployment strategy with health probes.

## Google Drive in Cloud
1. OAuth app must include the public callback host you use.
2. `credentials.json` must match that OAuth client.
3. Keep token storage in persistent volume.
4. For enterprise rollout, migrate from user-consent OAuth to service account + domain delegation when governance requires it.

## Persistence and Re-Ingestion Behavior
You do not need to re-ingest on every restart if persistent storage is preserved.

Required persistent paths/volumes:
1. Postgres data volume.
2. Qdrant data volume.
3. Ollama model volume (if local inference).
4. `./data/google` (credentials and token).
5. `./artifacts` (optional but recommended for evidence artifacts).

If these are ephemeral, you will lose state and need to re-ingest.

## Cloud Go-Live Checklist
1. All default passwords replaced.
2. Auth enabled and verified (`AUTH_ENABLED=true`).
3. HTTPS only, no public DB/vector ports.
4. `verify_phase5.ps1` passes.
5. Backup created and restore test completed.
6. Red-team suite passes.
7. Load test baseline captured (p50/p95/throughput).
8. Audit logs retained per policy.

## Suggested Next Actions
1. Deploy pilot on one VM first (Option A).
2. Run 200-doc ingestion benchmark and collect latency + failure metrics.
3. Tune model/inference profile for your cloud GPU/CPU budget.
4. Promote to Kubernetes only after pilot acceptance.
