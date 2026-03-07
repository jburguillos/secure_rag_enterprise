.PHONY: up down logs test ingest-gdrive ingest-local load-test security-test backup restore verify-phase1

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

test:
	pytest -q

ingest-gdrive:
	python scripts/ingest_gdrive.py --folder-id "$(FOLDER_ID)"

ingest-local:
	python scripts/ingest_local.py --path "$(PATH_ARG)" --acl-sidecar "$(ACL_SIDECAR)"

load-test:
	python scripts/load_test.py --url http://localhost:8000/query --requests 200 --concurrency 8

security-test:
	python scripts/security_regression.py --url http://localhost:8000 --cases tests/redteam/prompts.yaml

backup:
	python scripts/backup_restore.py backup

restore:
	python scripts/backup_restore.py restore

verify-phase1:
	powershell -ExecutionPolicy Bypass -File scripts/verify_phase1.ps1
