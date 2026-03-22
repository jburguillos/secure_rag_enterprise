# Cloud Deployment Runbook

This runbook is the operational path for moving `secure_rag_enterprise` from a validated local baseline to a cloud VM without mixing code versions, auth states, or partially rebuilt services.

The key rule is simple:

1. Local is the source of truth.
2. Cloud deploys an exact validated commit or tag.
3. Validate by terminal first, UI second.

Do not deploy by repeatedly pulling `main` on the VM and trying fixes live. That creates mixed state and makes failures much harder to diagnose.

## Recommended Deployment Path
1. Build and validate a local release candidate.
2. Capture local baseline artifacts.
3. Freeze the exact commit with a tag.
4. Deploy that exact tag/commit to the VM.
5. Rebuild containers on the VM.
6. Validate health, auth, retrieval, and citations from terminal.
7. Only then validate the Streamlit UI.

This is the recommended path for:
- capstone demos
- pilot VM deployment
- reproducible handoff to a professor, reviewer, or teammate

## What "Cloud Ready" Means Here
The project is cloud ready when all of the following are true for the exact deployed commit:

1. `docker compose ps` shows all required services healthy/running.
2. API health and readiness return `{"status":"ok"}`.
3. Keycloak issues valid tokens for the intended user.
4. OPA allows authorized requests and blocks unauthorized ones.
5. Drive or local corpus is ingested and visible in both Postgres and Qdrant.
6. A grounded query returns citations from the expected authorized document.
7. Phase 5 hardening artifacts are captured and retained.

## Golden Rule: Deploy a Frozen Baseline
Before touching the VM, freeze the local state that works.

Required baseline facts:
1. git commit hash
2. branch name
3. `.env` values relevant to auth and models
4. installed Ollama models
5. validation artifacts under `artifacts/capstone/<timestamp>/local_baseline`
6. deploy manifest describing the exact VM target state (`09_deploy_manifest.json`)

Recommended freeze workflow:
1. Validate locally.
2. Create a tag, for example `vm-baseline-20260322`.
3. Push commit + tag to GitHub.
4. Deploy that tag in the VM.

## Local Baseline Checklist
Run this before every VM deployment attempt.

1. Containers are up:
   - `docker compose up -d`
2. Local health is clean:
   - `http://localhost:8000/health/liveness`
   - `http://localhost:8000/health/readiness`
3. Ollama models exist:
   - `nomic-embed-text`
   - chat model used by UI/API
4. Keycloak login works locally.
5. At least one authenticated Drive query returns:
   - `policy_decision.allow = true`
   - non-empty `citations`
6. Local UI behaves correctly after login.

To capture baseline artifacts, use:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/capture_local_baseline.ps1 `
  -ApiUrl http://localhost:8000 `
  -UiUrl http://localhost:8501 `
  -Username jburguillos.drive `
  -Password ChangeMe123!
```

This writes a reproducible bundle under:

- `artifacts/capstone/<timestamp>/local_baseline/`

Minimum contents expected in that bundle:
1. exact git commit and branch
2. local compose status/config/images
3. liveness/readiness/metrics snapshots + UI reachability snapshot
4. decoded JWT claims for the deployment user
5. at least one successful authenticated Drive query
6. at least one successful authenticated tabular query
7. `09_deploy_manifest.json` with expected services, local source-of-truth commit, and critical auth/model settings
8. Phase 5 hardening capture output with its output folder path linked from `summary.json`

After a good capture:

```powershell
git rev-parse --short HEAD
git tag local-baseline-$(Get-Date -Format yyyyMMdd_HHmm)
git push origin main --tags
```

Deploy that exact commit or tag to the VM. Use `summary.json` and `09_deploy_manifest.json` as the handoff bundle. If VM behavior diverges, compare against the local baseline bundle instead of applying live patches first.

## Pilot VM Deployment (Single VM, Recommended First)
Use this for stakeholder demos and capstone presentation deployment.

### A1) VM baseline
1. Ubuntu 22.04/24.04 VM.
2. Persistent disk attached.
3. Open inbound ports:
   - `22` for SSH
   - `80/443` for reverse proxy
4. Keep internal services private:
   - `5432`
   - `6333`
   - `6334`
   - `8080`
   - `8181`
   - `11434`

### A2) Install runtime
1. Docker Engine.
2. Docker Compose plugin.
3. `git`.
4. Optional reverse proxy (`nginx` or `traefik`).

### A3) Clone exact code
Do not use floating `main` for deployment.

Recommended:

```bash
git clone https://github.com/jburguillos/secure_rag_enterprise.git
cd secure_rag_enterprise
git fetch --tags
git checkout <exact-tag-or-commit>
```

Examples:
- `git checkout vm-baseline-20260322`
- `git checkout <commit-sha>`

### A4) Configure environment
1. Copy:
   - `cp .env.example .env`
2. Set secrets:
   - `POSTGRES_PASSWORD`
   - `KEYCLOAK_ADMIN_PASSWORD`
3. Enforce secure defaults:
   - `APP_MODE=prod`
   - `ALLOW_OUTBOUND=false`
   - `ALLOW_PUBLIC_LLM=false`
   - `AUTH_ENABLED=true`
4. Set Keycloak auth variables for VM usage:
   - `KEYCLOAK_ISSUER=http://keycloak:8080/realms/secure-rag`
   - `KEYCLOAK_ISSUER_ALIASES=http://localhost:8080/realms/secure-rag,http://<public-ip-or-domain>/realms/secure-rag`
   - `KEYCLOAK_AUDIENCE=secure-rag-api,account`
5. Place Google OAuth files on persistent storage:
   - `./data/google/credentials.json`
   - `./data/google/token.json`

Never commit those files.

### A5) Stateful paths that must persist
If these do not persist, you may lose models, metadata, or auth state and need to reconfigure/reingest.

Persist:
1. Postgres data volume
2. Qdrant data volume
3. Ollama model volume
4. `./data/google`
5. optionally `./artifacts`

### A6) Launch
Bring up the platform in a predictable order:

```bash
docker compose up -d
docker compose --profile ollama up -d ollama
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull llama3.2:3b
```

If you use a different chat model locally, pull that exact model in the VM as well.

### A7) Validate terminal-first
Do not start with the UI.

Run these checks in this order:

1. health
   - `curl http://localhost:8000/health/liveness`
   - `curl http://localhost:8000/health/readiness`
2. Keycloak token issuance
3. authenticated `/query`
4. citation-bearing response on a known authorized document
5. only then open the UI

### A8) UI validation
After terminal validation passes:
1. open the deployed UI
2. log in with Keycloak
3. verify token refresh works
4. re-run the same known query used in terminal

If terminal works but UI does not, the issue is in the UI session/auth flow, not in retrieval or ACL.

## Reverse Proxy / Public Access
If the VM is meant to be opened from a browser directly:

1. put Nginx or Traefik in front
2. route:
   - `/` -> `ui:8501`
   - `/api` or dedicated subdomain -> `api:8000`
   - `/realms` or `/keycloak` -> `keycloak:8080`
3. enable HTTPS certificates

Directly exposing every container port publicly is not recommended.

## Google Drive in Cloud
The VM can use the same Drive integration as local, but the credentials and token must exist on the VM.

Required:
1. `credentials.json` copied to `./data/google/credentials.json`
2. valid `token.json` copied to `./data/google/token.json`
3. Drive API access enabled for the same Google project

For large recursive ingests:
- the API uses the native Drive downloader by default
- supported nested files are ingested recursively
- unsupported files are skipped with recorded errors

## Persistence and Re-Ingestion Behavior
You do not need to re-ingest on every restart if persistent storage is preserved.

You will need to re-ingest when:
1. Postgres/Qdrant data volumes are lost
2. Drive permissions change and you want fresh ACL payloads
3. you intentionally clear and rebuild indexes

## Cloud Validation Bundle
After successful VM deployment, capture evidence under:

- `artifacts/capstone/<timestamp>/vm_validation/`

Recommended contents:
1. `docker compose ps`
2. health outputs
3. token claims
4. one successful authenticated query
5. one ACL negative check
6. one load test result
7. one security regression result

This is the material you will use for:
- capstone analysis
- deployment appendix
- demo reproducibility

## Production Hygiene Rules
1. Never debug by changing multiple layers at once.
2. Never deploy unvalidated `main` directly to the VM.
3. Always write down:
   - deployed commit
   - deployed tag
   - `.env` deltas from local
4. Validate by terminal before UI.
5. If the VM behaves strangely, compare against local baseline rather than continuing to patch blindly.

## Go-Live Checklist
1. local baseline captured
2. exact commit or tag frozen
3. VM checked out to exact commit/tag
4. secrets placed locally on VM, not committed
5. Keycloak auth validated
6. one known authorized query returns citations
7. security regression suite captured
8. load test baseline captured
9. backup/restore script exercised
10. README and deployment notes updated

## Suggested Next Actions
1. Finalize the local baseline candidate.
2. Capture local baseline artifacts.
3. Create and push a deployment tag.
4. Rebuild the VM from that exact tag only.
5. Validate terminal-first, then UI.
