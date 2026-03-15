"""Application configuration and settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Environment-backed settings."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_mode: str = "prod"
    allow_public_llm: bool = False
    allow_outbound: bool = False
    enable_ocr: bool = False
    enable_rerank: bool = False

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    qdrant_url: str = "http://localhost:6333"
    qdrant_text_collection: str = "text_nodes"
    qdrant_image_collection: str = "image_nodes"

    database_url: str = "sqlite+pysqlite:///./secure_rag.db"

    llm_provider: str = "ollama"
    embedding_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "llama3.2:3b"
    ollama_embed_model: str = "nomic-embed-text"
    embedding_batch_size: int = 16
    vlm_router: str = "disabled"
    vlm_router_max_images: int = 6

    public_llm_base_url: str | None = None
    public_llm_api_key: str | None = None

    drive_auth_mode: str = "oauth"
    google_credentials_path: str = "/data/google/credentials.json"
    google_token_path: str = "/data/google/token.json"
    google_service_account_json: str | None = None
    drive_folder_id: str = ""
    drive_group_map_json: str = "{}"
    google_drive_use_reader: bool = False

    auth_enabled: bool = False
    keycloak_issuer: str = "http://keycloak:8080/realms/secure-rag"
    keycloak_audience: str = "secure-rag-api"
    keycloak_issuer_aliases: str = ""
    admin_authorized_groups: str = "admin"

    keycloak_admin_url: str = "http://keycloak:8080"
    keycloak_realm: str = "secure-rag"
    keycloak_admin_user: str = "admin"
    keycloak_admin_password: str = "admin"
    keycloak_admin_client_id: str = "admin-cli"

    opa_url: str = "http://localhost:8181"
    opa_policy_path: str = "/v1/data/secure_rag/authz/allow"
    opa_fail_closed: bool = True

    top_k_dense: int = 8
    top_k_bm25: int = 8
    top_k_fused: int = 8
    retrieval_candidate_multiplier: int = 4
    retrieval_candidate_max: int = 80
    retrieval_doc_diversity_max_chunks: int = 2

    rerank_top_candidates: int = 40

    max_context_chars: int = 12000
    generation_max_evidence_nodes: int = 4
    generation_doc_diversity_max_chunks: int = 1
    enable_answerability_judge: bool = True
    answerability_use_llm: bool = True
    answerability_max_evidence_nodes: int = 6
    answerability_max_chars_per_node: int = 900

    summarize_map_max_docs: int = 6
    summarize_map_chars_per_doc: int = 700
    tabular_rows_per_block: int = 25
    tabular_max_columns: int = 20
    tabular_max_cell_chars: int = 200
    tabular_max_blocks_per_sheet: int = 200
    tabular_max_sheets_per_workbook: int = 50
    generation_tabular_max_blocks_per_sheet: int = 2

    require_citations: bool = True
    min_citations: int = 1
    refusal_text: str = "I do not have enough authorized evidence to answer that."
    llm_unavailable_text: str = (
        "The local chat model is unavailable on this machine. Switch to a smaller Ollama model or check local memory."
    )
    domain_context_hint: str = (
        "The indexed corpus often simulates an internal venture capital fund operating model, "
        "including fundraising, LP commitments, capital calls, due diligence, portfolio management, "
        "market research, legal/compliance, and board reporting workflows."
    )

    local_ingest_root: str = "./tests/data/sample_docs"
    local_acl_sidecar: str = "./tests/data/sample_docs/acl_map.yaml"
    pdf_image_root: str = "./artifacts/pdf_images"
    audit_raw_query: bool = False

    config_path: Path = Field(default=Path("/app/config/config.yml"))


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return cached settings instance."""

    return AppSettings()


@lru_cache(maxsize=1)
def get_yaml_config(config_path: str | None = None) -> dict[str, Any]:
    """Load YAML config from disk if available."""

    settings = get_settings()
    path = Path(config_path) if config_path else settings.config_path
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        return {}
    return data
