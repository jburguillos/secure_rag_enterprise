"""Streamlit UI for secure RAG MVP."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import requests
import streamlit as st


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
DEFAULT_DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "")
DEFAULT_LOCAL_PATH = os.getenv("UI_DEFAULT_LOCAL_PATH", "./tests/data/sample_docs")
DEFAULT_LOCAL_ACL = os.getenv("UI_DEFAULT_LOCAL_ACL", "./tests/data/sample_docs/acl_map.yaml")
UI_QUERY_TIMEOUT_SEC = int(os.getenv("UI_QUERY_TIMEOUT_SEC", "300"))
UI_INGEST_POLL_INTERVAL_SEC = float(os.getenv("UI_INGEST_POLL_INTERVAL_SEC", "2"))
UI_INGEST_POLL_TIMEOUT_SEC = int(os.getenv("UI_INGEST_POLL_TIMEOUT_SEC", "1800"))
UI_TOKEN_REFRESH_SKEW_SEC = int(os.getenv("UI_TOKEN_REFRESH_SKEW_SEC", "30"))
KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER", "http://keycloak:8080/realms/secure-rag").rstrip("/")
KEYCLOAK_TOKEN_URL = os.getenv("KEYCLOAK_TOKEN_URL", f"{KEYCLOAK_ISSUER}/protocol/openid-connect/token")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "secure-rag-api")
DEFAULT_AUTH_USERNAME = os.getenv("UI_DEFAULT_USERNAME", "")
DEFAULT_AUTH_PASSWORD = os.getenv("UI_DEFAULT_PASSWORD", "")
AUTH_ENABLED = _bool_env("AUTH_ENABLED", False)
UI_REQUIRE_LOGIN = _bool_env("UI_REQUIRE_LOGIN", AUTH_ENABLED)

st.set_page_config(page_title="Catalyst Iberia Ventures | Secure Copilot", layout="wide", initial_sidebar_state="expanded")


def _inject_css() -> None:
    # Palette extracted from pluspartners_brandguidelines_2024.pdf
    st.markdown(
        """
<style>
:root {
    --pp-ink: #203044;
    --pp-ink-soft: #30485f;
    --pp-slate: #8898A8;
    --pp-slate-light: #a8bcc8;
    --pp-accent: #E0582C;
    --pp-bg: #f0f0e8;
    --pp-surface: #ffffff;
    --pp-border: #d7dee5;
}
html, body, [class*="css"] {
    font-family: "Avenir Next", "Trebuchet MS", "Gill Sans", "Segoe UI", sans-serif;
}
.stApp {
    background: radial-gradient(circle at top right, #f8f6ef 0%, #f0f0e8 45%, #edf2f6 100%);
    color: var(--pp-ink);
}
[data-testid="stSidebar"] {
    background: linear-gradient(185deg, #1d2d3f 0%, #223549 60%, #2c4258 100%);
    border-right: 1px solid rgba(255, 255, 255, 0.12);
}
[data-testid="stSidebar"] * {
    color: #eef2f7;
}
[data-testid="stSidebar"] hr {
    border-color: rgba(255, 255, 255, 0.18);
}
.pp-brand-title {
    font-size: 1.2rem;
    font-weight: 700;
    letter-spacing: 0.03em;
    color: #ffffff;
    margin-bottom: 0.2rem;
}
.pp-brand-subtitle {
    font-size: 0.86rem;
    color: #d8e3ed;
    margin-bottom: 0.9rem;
}
.pp-page-title {
    font-size: 2rem;
    font-weight: 750;
    color: var(--pp-ink);
    margin-bottom: 0.1rem;
}
.pp-page-subtitle {
    color: var(--pp-ink-soft);
    margin-bottom: 1rem;
}
.pp-kpi {
    border: 1px solid var(--pp-border);
    border-radius: 14px;
    padding: 0.75rem 0.9rem;
    background: rgba(255, 255, 255, 0.82);
}
.pp-kpi-label {
    color: var(--pp-ink-soft);
    font-size: 0.82rem;
    margin-bottom: 0.2rem;
}
.pp-kpi-value {
    color: var(--pp-ink);
    font-size: 1.02rem;
    font-weight: 700;
}
div[data-testid="stChatMessage"] {
    border-radius: 14px;
    border: 1px solid var(--pp-border);
    box-shadow: 0 4px 14px rgba(18, 39, 57, 0.06);
    padding-top: 0.5rem;
    padding-bottom: 0.5rem;
}
div[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
    background: #eef4fb;
}
div[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) {
    background: #fffefb;
}
button[kind="primary"] {
    background: linear-gradient(125deg, var(--pp-accent) 0%, #f07248 100%) !important;
    border: none !important;
    color: #ffffff !important;
    font-weight: 700 !important;
}
button[kind="secondary"] {
    border: 1px solid var(--pp-border) !important;
}
.stTextInput input, .stTextArea textarea, .stSelectbox [data-baseweb="select"] > div {
    border-radius: 12px !important;
}
[data-testid="stSidebar"] .stTextInput input,
[data-testid="stSidebar"] .stTextArea textarea,
[data-testid="stSidebar"] [data-baseweb="input"] > div > input,
[data-testid="stSidebar"] [data-baseweb="select"] input {
    color: var(--pp-ink) !important;
    -webkit-text-fill-color: var(--pp-ink) !important;
    background: #ffffff !important;
}
[data-testid="stSidebar"] .stTextInput input::placeholder,
[data-testid="stSidebar"] .stTextArea textarea::placeholder,
[data-testid="stSidebar"] [data-baseweb="input"] > div > input::placeholder,
[data-testid="stSidebar"] [data-baseweb="select"] input::placeholder {
    color: #6f8193 !important;
    -webkit-text-fill-color: #6f8193 !important;
    opacity: 1 !important;
}
[data-testid="stSidebar"] input:-webkit-autofill,
[data-testid="stSidebar"] input:-webkit-autofill:hover,
[data-testid="stSidebar"] input:-webkit-autofill:focus {
    -webkit-text-fill-color: var(--pp-ink) !important;
    box-shadow: 0 0 0 1000px #ffffff inset !important;
}
.pp-login-wrap {
    min-height: calc(100vh - 2.5rem);
    display: flex;
    align-items: center;
    justify-content: center;
}
.pp-login-card {
    width: min(460px, 92vw);
    border-radius: 20px;
    border: 1px solid var(--pp-border);
    background: rgba(255, 255, 255, 0.95);
    box-shadow: 0 16px 40px rgba(17, 38, 55, 0.12);
    padding: 1.2rem 1.2rem 1rem 1.2rem;
    margin: 0 auto;
}
.pp-login-title {
    font-size: 1.55rem;
    font-weight: 750;
    color: var(--pp-ink);
    margin-bottom: 0.2rem;
}
.pp-login-subtitle {
    color: var(--pp-ink-soft);
    margin-bottom: 0.9rem;
}
</style>
""",
        unsafe_allow_html=True,
    )


_inject_css()

if "messages_by_mode" not in st.session_state:
    st.session_state.messages_by_mode = {"auto": [], "rag": [], "chat": []}
if "messages" in st.session_state and isinstance(st.session_state.messages, list):
    # Backward compatibility: keep previous mixed history in auto mode.
    if st.session_state.messages and not any(st.session_state.messages_by_mode.values()):
        st.session_state.messages_by_mode["auto"] = list(st.session_state.messages)
if "last_response_by_mode" not in st.session_state:
    st.session_state.last_response_by_mode = {"auto": None, "rag": None, "chat": None}
if "last_run_by_mode" not in st.session_state:
    st.session_state.last_run_by_mode = {"auto": None, "rag": None, "chat": None}
if "admin_mapping_text" not in st.session_state:
    st.session_state.admin_mapping_text = "{}"
if "api_url" not in st.session_state:
    st.session_state.api_url = API_BASE_URL
if "manual_token" not in st.session_state:
    st.session_state.manual_token = ""
if "auth_access_token" not in st.session_state:
    st.session_state.auth_access_token = ""
if "auth_refresh_token" not in st.session_state:
    st.session_state.auth_refresh_token = ""
if "auth_expires_at" not in st.session_state:
    st.session_state.auth_expires_at = 0
if "auth_username" not in st.session_state:
    st.session_state.auth_username = DEFAULT_AUTH_USERNAME
if "auth_password" not in st.session_state:
    st.session_state.auth_password = DEFAULT_AUTH_PASSWORD
if "auth_session_warning" not in st.session_state:
    st.session_state.auth_session_warning = ""
if "chat_retrieval_mode" not in st.session_state:
    st.session_state.chat_retrieval_mode = "auto"
if "navigation" not in st.session_state:
    st.session_state.navigation = "Workspace"
if "user_email" not in st.session_state:
    st.session_state.user_email = ""
if "user_domain" not in st.session_state:
    st.session_state.user_domain = ""
if "groups_text" not in st.session_state:
    st.session_state.groups_text = ""
if "folder_id" not in st.session_state:
    st.session_state.folder_id = DEFAULT_DRIVE_FOLDER_ID
if "drive_auth_mode" not in st.session_state:
    st.session_state.drive_auth_mode = "oauth"
if "local_path" not in st.session_state:
    st.session_state.local_path = DEFAULT_LOCAL_PATH
if "local_acl" not in st.session_state:
    st.session_state.local_acl = DEFAULT_LOCAL_ACL
if "dev_mode_bypass" not in st.session_state:
    st.session_state.dev_mode_bypass = not UI_REQUIRE_LOGIN


def _safe_response_data(response: requests.Response) -> dict[str, Any]:
    if not response.content:
        return {}
    try:
        data = response.json()
        return data if isinstance(data, dict) else {"value": data}
    except ValueError:
        return {"raw_text": response.text}


def _decode_jwt_exp(access_token: str) -> int | None:
    try:
        parts = access_token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(payload.encode("utf-8"))
        claims = json.loads(raw.decode("utf-8"))
        exp = claims.get("exp")
        if isinstance(exp, (int, float)):
            return int(exp)
        return None
    except Exception:
        return None


def _store_token_payload(payload: dict[str, Any]) -> None:
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        return

    st.session_state.auth_access_token = access_token

    refresh_token = str(payload.get("refresh_token") or "").strip()
    if refresh_token:
        st.session_state.auth_refresh_token = refresh_token

    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)):
        st.session_state.auth_expires_at = int(time.time()) + int(expires_in)
    else:
        st.session_state.auth_expires_at = _decode_jwt_exp(access_token) or 0

    st.session_state.auth_session_warning = ""


def _token_request(form_data: dict[str, str]) -> tuple[int, dict[str, Any]]:
    response = requests.post(
        KEYCLOAK_TOKEN_URL,
        data=form_data,
        timeout=20,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return response.status_code, _safe_response_data(response)


def _token_error(status: int, data: dict[str, Any]) -> str:
    detail = data.get("error_description") or data.get("detail") or data.get("error") or data
    return f"status={status} detail={detail}"


def _login_with_password(username: str, password: str) -> tuple[bool, str]:
    if not username or not password:
        return False, "Username and password are required."

    status, data = _token_request(
        {
            "grant_type": "password",
            "client_id": KEYCLOAK_CLIENT_ID,
            "username": username,
            "password": password,
        }
    )
    if status >= 400:
        return False, _token_error(status, data)

    _store_token_payload(data)
    return True, "Authenticated."


def _refresh_access_token() -> tuple[bool, str]:
    refresh_token = str(st.session_state.auth_refresh_token or "").strip()
    if not refresh_token:
        return False, "No refresh token available."

    status, data = _token_request(
        {
            "grant_type": "refresh_token",
            "client_id": KEYCLOAK_CLIENT_ID,
            "refresh_token": refresh_token,
        }
    )
    if status >= 400:
        return False, _token_error(status, data)

    _store_token_payload(data)
    return True, "Token refreshed."


def _clear_auth_session() -> None:
    st.session_state.auth_access_token = ""
    st.session_state.auth_refresh_token = ""
    st.session_state.auth_expires_at = 0
    st.session_state.auth_session_warning = ""


def _token_needs_refresh() -> bool:
    access_token = str(st.session_state.auth_access_token or "").strip()
    if not access_token:
        return True

    expires_at = int(st.session_state.auth_expires_at or 0)
    if expires_at <= 0:
        return False

    return int(time.time()) + UI_TOKEN_REFRESH_SKEW_SEC >= expires_at


def _ensure_access_token(*, force_refresh: bool = False) -> str:
    manual_token = str(st.session_state.manual_token or "").strip()
    if manual_token:
        return manual_token

    access_token = str(st.session_state.auth_access_token or "").strip()

    if force_refresh or _token_needs_refresh():
        refreshed, _ = _refresh_access_token()
        if not refreshed:
            username = str(st.session_state.auth_username or "").strip()
            password = str(st.session_state.auth_password or "")
            if username and password:
                _login_with_password(username, password)
        access_token = str(st.session_state.auth_access_token or "").strip()

    return access_token


def _headers(*, force_refresh: bool = False) -> dict[str, str]:
    out = {"Content-Type": "application/json"}
    access_token = _ensure_access_token(force_refresh=force_refresh)
    if access_token:
        out["Authorization"] = f"Bearer {access_token}"
    return out


def _api_request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 60,
) -> tuple[int, dict[str, Any]]:
    base_url = str(st.session_state.api_url or API_BASE_URL).rstrip("/")
    url = f"{base_url}{path}"

    response = requests.request(method, url, json=payload, params=params, headers=_headers(), timeout=timeout)
    data = _safe_response_data(response)

    # One silent retry after forced refresh for auto-auth mode.
    if response.status_code == 401 and not str(st.session_state.manual_token or "").strip():
        retry_headers = _headers(force_refresh=True)
        response = requests.request(method, url, json=payload, params=params, headers=retry_headers, timeout=timeout)
        data = _safe_response_data(response)

    if response.status_code == 401 and not str(st.session_state.manual_token or "").strip():
        st.session_state.auth_session_warning = "Session expired. Please login again."
        _clear_auth_session()
    elif response.status_code < 400:
        st.session_state.auth_session_warning = ""

    return response.status_code, data


def _infer_query_mode(question: str) -> str:
    lowered = (question or "").strip().lower()
    summarize_hints = (
        "summarize",
        "summary",
        "summarise",
        "resumen",
        "resumir",
        "sumariza",
        "sumarice",
        "for each file",
        "for each document",
        "each file",
        "each document",
        "cada archivo",
        "cada documento",
        "cada fichero",
        "documents you have",
        "files you have",
    )
    if any(hint in lowered for hint in summarize_hints):
        return "summarize"
    return "qa"


def _auth_status_text() -> str:
    if str(st.session_state.manual_token or "").strip():
        return "Using manual bearer token override."

    access_token = str(st.session_state.auth_access_token or "").strip()
    if not access_token:
        return "Not authenticated."

    expires_at = int(st.session_state.auth_expires_at or 0)
    if expires_at <= 0:
        return "Authenticated (expiry unknown)."

    remaining = expires_at - int(time.time())
    if remaining <= 0:
        return "Authenticated token expired."
    return f"Authenticated. Token expires in ~{remaining}s."


def _run_ingest_job_with_poll(*, endpoint: str, payload: dict[str, Any], label: str) -> tuple[int, dict[str, Any]]:
    start_status, start_data = _api_request("POST", endpoint, payload=payload, timeout=30)
    if start_status >= 400:
        return start_status, start_data

    run_id = str(start_data.get("ingestion_run_id") or "").strip()
    if not run_id:
        return 502, {"detail": f"{label} start response did not include ingestion_run_id"}

    status_box = st.empty()
    deadline = time.time() + UI_INGEST_POLL_TIMEOUT_SEC

    while time.time() < deadline:
        poll_status, poll_data = _api_request("GET", f"/ingest/runs/{run_id}", timeout=30)
        if poll_status >= 400:
            status_box.warning(f"{label} polling failed: status={poll_status}")
            return poll_status, poll_data

        run_status = str(poll_data.get("status") or "").strip()
        added = int(poll_data.get("added") or 0)
        skipped = int(poll_data.get("skipped") or 0)
        text_nodes = int(poll_data.get("text_nodes_indexed") or 0)
        image_nodes = int(poll_data.get("image_nodes_indexed") or 0)
        status_box.info(
            f"{label} run {run_id}: status={run_status} added={added} skipped={skipped} "
            f"text_nodes={text_nodes} image_nodes={image_nodes}"
        )
        if run_status != "running":
            return poll_status, poll_data
        time.sleep(UI_INGEST_POLL_INTERVAL_SEC)

    return 504, {"detail": f"{label} polling timed out", "ingestion_run_id": run_id}


def _format_citation_label(citation: dict[str, Any]) -> str:
    doc_name = citation.get("doc_name") or citation.get("doc_id") or "unknown_doc"
    sheet_name = citation.get("sheet_name")
    row_start = citation.get("row_start")
    row_end = citation.get("row_end")
    cell_range = citation.get("cell_range")
    page = citation.get("page")

    parts = [f"**{doc_name}**"]
    if sheet_name:
        parts.append(f"sheet={sheet_name}")
    if row_start is not None and row_end is not None:
        parts.append(f"rows={row_start}-{row_end}")
    elif cell_range:
        parts.append(f"range={cell_range}")
    elif page is not None:
        parts.append(f"page={page}")
    parts.append(f"node={citation.get('node_id')}")
    parts.append(f"chunk={citation.get('chunk_id')}")
    return f"**{doc_name}** ({', '.join(parts[1:])})"


def _is_authenticated() -> bool:
    return bool(str(st.session_state.manual_token or "").strip() or str(st.session_state.auth_access_token or "").strip())


def _render_login_page() -> None:
    st.markdown(
        """
<style>
[data-testid="stSidebar"] { display: none !important; }
[data-testid="collapsedControl"] { display: none !important; }
[data-testid="stMainBlockContainer"] {
    max-width: 1280px !important;
    padding-top: 6vh !important;
    padding-bottom: 0.5rem !important;
}
</style>
""",
        unsafe_allow_html=True,
    )
    left, center, right = st.columns([1.0, 1.8, 1.0])
    with center:
        with st.container(border=True):
            st.markdown("### Catalyst Iberia Ventures Secure Copilot")
            st.caption("Sign in to access secure venture workflows, governed retrieval, and audit-ready citations.")
            st.session_state.auth_username = st.text_input("Username", value=st.session_state.auth_username, key="login_username")
            st.session_state.auth_password = st.text_input("Password", value=st.session_state.auth_password, type="password", key="login_password")
            s1, s2 = st.columns([1.2, 1.0])
            with s1:
                if st.button("Sign In", type="primary", use_container_width=True, key="login_submit"):
                    ok, message = _login_with_password(st.session_state.auth_username, st.session_state.auth_password)
                    if ok:
                        st.success(message)
                        st.rerun()
                    else:
                        st.error(message)
            with s2:
                if st.button("Use Session", use_container_width=True, key="login_use_session"):
                    if _is_authenticated():
                        st.success("Session found.")
                        st.rerun()
                    else:
                        st.warning("No active token in session.")
            with st.expander("Advanced: manual bearer token", expanded=False):
                st.session_state.manual_token = st.text_input(
                    "Manual bearer token (optional)",
                    value=st.session_state.manual_token,
                    type="password",
                    key="login_manual_token",
                ).strip()
                if st.session_state.manual_token and st.button(
                    "Continue with Token",
                    type="primary",
                    use_container_width=True,
                    key="login_continue_token",
                ):
                    st.rerun()
                if not UI_REQUIRE_LOGIN and st.button("Continue in Dev Mode", use_container_width=True, key="login_dev_mode"):
                    st.session_state.dev_mode_bypass = True
                    st.rerun()


if UI_REQUIRE_LOGIN and not (_is_authenticated() or st.session_state.dev_mode_bypass):
    _render_login_page()
    st.stop()

with st.sidebar:
    st.markdown('<div class="pp-brand-title">CATALYST IBERIA VENTURES</div>', unsafe_allow_html=True)
    st.markdown('<div class="pp-brand-subtitle">Secure Multimodal Intelligence Workspace</div>', unsafe_allow_html=True)
    st.session_state.navigation = st.radio(
        "Navigation",
        options=["Workspace", "Ingestion", "Runs", "Admin"],
        index=["Workspace", "Ingestion", "Runs", "Admin"].index(st.session_state.navigation),
    )
    st.markdown("---")
    st.caption("Connection")
    st.session_state.api_url = st.text_input("API URL", value=st.session_state.api_url)
    st.caption("Auth")
    st.session_state.manual_token = st.text_input(
        "Manual bearer token (optional override)",
        value=st.session_state.manual_token,
        type="password",
    ).strip()
    auth_username_input = st.text_input("Keycloak username", value=st.session_state.auth_username, key="auth_username_input").strip()
    auth_password_input = st.text_input("Keycloak password", value=st.session_state.auth_password, type="password", key="auth_password_input")
    st.session_state.auth_username = auth_username_input
    st.session_state.auth_password = auth_password_input

    a1, a2 = st.columns(2)
    with a1:
        if st.button("Login", use_container_width=True):
            ok, message = _login_with_password(auth_username_input, auth_password_input)
            if ok:
                st.success(message)
            else:
                st.error(message)
    with a2:
        if st.button("Logout", use_container_width=True):
            _clear_auth_session()
            st.success("Session token cleared.")

    st.caption(_auth_status_text())
    if st.session_state.auth_session_warning:
        st.warning(st.session_state.auth_session_warning)

    st.markdown("---")
    st.caption("User context")
    st.session_state.user_email = st.text_input("email", value=st.session_state.user_email)
    st.session_state.user_domain = st.text_input("domain", value=st.session_state.user_domain)
    st.session_state.groups_text = st.text_area("groups (one per line)", value=st.session_state.groups_text)
    user_groups = [g.strip() for g in st.session_state.groups_text.splitlines() if g.strip()]

    st.markdown("---")
    st.caption("Ingestion defaults")
    st.session_state.folder_id = st.text_input("Google Drive folder id", value=st.session_state.folder_id)
    st.session_state.drive_auth_mode = st.selectbox(
        "Drive auth mode",
        options=["oauth", "service_account"],
        index=0 if st.session_state.drive_auth_mode == "oauth" else 1,
    )
    st.session_state.local_path = st.text_input("Local path", value=st.session_state.local_path)
    st.session_state.local_acl = st.text_input("Local ACL sidecar", value=st.session_state.local_acl)

st.markdown('<div class="pp-page-title">Secure Multimodal RAG Workspace</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="pp-page-subtitle">Grounded Q&A for venture operations with retrieval-time ACL filtering and audit-ready citations.</div>',
    unsafe_allow_html=True,
)

if st.session_state.navigation == "Ingestion":
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("### Google Drive")
        st.caption("Recursive ingestion over folders and nested subfolders.")
        folder_id = st.text_input("Drive folder id", value=st.session_state.folder_id, key="ingest_folder_id")
        auth_mode = st.selectbox(
            "Auth mode",
            options=["oauth", "service_account"],
            index=0 if st.session_state.drive_auth_mode == "oauth" else 1,
            key="ingest_auth_mode",
        )
        if st.button("Start Google Drive Ingestion", type="primary", use_container_width=True):
            if not folder_id.strip():
                st.error("Google Drive folder id is required.")
            else:
                payload = {
                    "folder_id": folder_id,
                    "auth_mode": auth_mode,
                    "dry_run": False,
                    "dataset_source": "google_drive",
                }
                status, data = _run_ingest_job_with_poll(
                    endpoint="/ingest/gdrive/async",
                    payload=payload,
                    label="Google Drive ingest",
                )
                st.caption(f"status={status}")
                if status >= 400:
                    st.error(f"status={status} data={data}")
                else:
                    st.json(data)
    with c2:
        st.markdown("### Local Folder")
        st.caption("Use ACL sidecar mapping for local corpus permissions.")
        local_path = st.text_input("Local path", value=st.session_state.local_path, key="ingest_local_path")
        local_acl = st.text_input("ACL sidecar", value=st.session_state.local_acl, key="ingest_local_acl")
        if st.button("Start Local Ingestion", type="primary", use_container_width=True):
            payload = {
                "path": local_path,
                "acl_sidecar_path": local_acl,
                "dry_run": False,
                "dataset_source": "local_folder",
            }
            status, data = _run_ingest_job_with_poll(
                endpoint="/ingest/local/async",
                payload=payload,
                label="Local ingest",
            )
            st.caption(f"status={status}")
            if status >= 400:
                st.error(f"status={status} data={data}")
            else:
                st.json(data)
elif st.session_state.navigation == "Runs":
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            f'<div class="pp-kpi"><div class="pp-kpi-label">Auto last run</div><div class="pp-kpi-value">{st.session_state.last_run_by_mode.get("auto") or "-"}</div></div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div class="pp-kpi"><div class="pp-kpi-label">RAG last run</div><div class="pp-kpi-value">{st.session_state.last_run_by_mode.get("rag") or "-"}</div></div>',
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f'<div class="pp-kpi"><div class="pp-kpi-label">Chat last run</div><div class="pp-kpi-value">{st.session_state.last_run_by_mode.get("chat") or "-"}</div></div>',
            unsafe_allow_html=True,
        )
    run_id = st.text_input("Inspect run_id", value=st.session_state.last_run_by_mode.get(st.session_state.chat_retrieval_mode) or "")
    if st.button("Load run details", type="primary"):
        if not run_id.strip():
            st.error("run_id is required")
        else:
            status, data = _api_request("GET", f"/runs/{run_id}", timeout=30)
            st.caption(f"status={status}")
            if status >= 400:
                st.error(data)
            else:
                st.json(data)

elif st.session_state.navigation == "Admin":
    st.subheader("Admin Console")
    st.caption("Requires admin bearer token and AUTH_ENABLED=true")

    if st.button("Load Drive Group Mapping"):
        status, data = _api_request("GET", "/admin/settings/drive-group-map")
        if status < 400:
            st.session_state.admin_mapping_text = json.dumps(data.get("mapping", {}), indent=2)
            st.success(f"Loaded mapping from {data.get('source')}")
        else:
            st.error(f"status={status} data={data}")

    st.session_state.admin_mapping_text = st.text_area(
        "Drive group mapping JSON",
        value=st.session_state.admin_mapping_text,
        height=180,
    )
    if st.button("Save Drive Group Mapping", type="primary"):
        try:
            mapping = json.loads(st.session_state.admin_mapping_text or "{}")
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")
            mapping = None
        if mapping is not None:
            status, data = _api_request("PUT", "/admin/settings/drive-group-map", payload={"mapping": mapping})
            if status < 400:
                st.success("Drive mapping saved to DB")
                st.json(data)
            else:
                st.error(f"status={status} data={data}")

    st.markdown("### Access Preview")
    p_email = st.text_input("preview email", value=st.session_state.user_email)
    p_domain = st.text_input("preview domain", value=st.session_state.user_domain)
    p_groups = st.text_input("preview groups (comma separated)", value=",".join(user_groups))
    p_sources = st.text_input("sources filter (comma separated, optional)", value="")
    p_limit = st.number_input("preview limit", min_value=1, max_value=500, value=100)
    if st.button("Run Access Preview", type="primary"):
        payload = {
            "principal": {
                "email": p_email or None,
                "domain": p_domain or None,
                "groups": [g.strip() for g in p_groups.split(",") if g.strip()],
            },
            "sources": [s.strip() for s in p_sources.split(",") if s.strip()],
            "limit": int(p_limit),
        }
        status, data = _api_request("POST", "/admin/access/preview", payload=payload)
        if status < 400:
            st.json(data)
        else:
            st.error(f"status={status} data={data}")

else:
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            f'<div class="pp-kpi"><div class="pp-kpi-label">Active API</div><div class="pp-kpi-value">{st.session_state.api_url}</div></div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            f'<div class="pp-kpi"><div class="pp-kpi-label">Auth Status</div><div class="pp-kpi-value">{_auth_status_text()}</div></div>',
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            f'<div class="pp-kpi"><div class="pp-kpi-label">User Groups</div><div class="pp-kpi-value">{len(user_groups)}</div></div>',
            unsafe_allow_html=True,
        )

    retrieval_modes = ["auto", "rag", "chat"]
    default_mode_index = retrieval_modes.index(st.session_state.chat_retrieval_mode) if st.session_state.chat_retrieval_mode in retrieval_modes else 0
    st.session_state.chat_retrieval_mode = st.selectbox(
        "Workspace mode",
        options=retrieval_modes,
        index=default_mode_index,
        help="auto routes between chat and retrieval, rag enforces grounded retrieval, chat uses conversational mode only.",
    )
    active_mode = st.session_state.chat_retrieval_mode
    active_messages = st.session_state.messages_by_mode[active_mode]
    for msg in active_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    question = st.chat_input("Ask a question about your venture corpus")
    if question:
        history_payload = [
            {"role": str(m.get("role", "")).strip(), "content": str(m.get("content", "")).strip()}
            for m in active_messages[-8:]
            if str(m.get("content", "")).strip() and str(m.get("role", "")).strip() in {"user", "assistant", "system"}
        ]
        active_messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        payload = {
            "query": question,
            "mode": _infer_query_mode(question),
            "retrieval_mode": active_mode,
            "include_images": True,
            "chat_history": history_payload,
            "user_context": {
                "email": st.session_state.user_email,
                "domain": st.session_state.user_domain,
                "groups": user_groups,
            },
        }
        with st.chat_message("assistant"):
            with st.spinner("Running retrieval and generation..."):
                status, data = _api_request("POST", "/query", payload=payload, timeout=UI_QUERY_TIMEOUT_SEC)
                if status == 401:
                    st.error("Session expired or invalid token. Refresh login in the sidebar.")
                elif status >= 400:
                    st.error(f"status={status} {data}")
                else:
                    answer = str(data.get("answer") or "").strip()
                    st.markdown(answer)
                    active_messages.append({"role": "assistant", "content": answer})
                    st.session_state.last_response_by_mode[active_mode] = data
                    st.session_state.last_run_by_mode[active_mode] = data.get("run_id")

    result: dict[str, Any] | None = st.session_state.last_response_by_mode.get(active_mode)
    if result:
        st.subheader("Citations")
        citations = result.get("citations") or []
        if not citations:
            st.info("No citations returned.")
        else:
            for idx, citation in enumerate(citations, start=1):
                st.markdown(f"{idx}. {_format_citation_label(citation)}")

        run_id = result.get("run_id")
        if run_id:
            st.subheader("Evidence Viewer")
            status, run_data = _api_request("GET", f"/runs/{run_id}", timeout=30)
            if status == 401:
                st.warning("Session expired while loading evidence viewer.")
            elif status < 400:
                evidence = run_data.get("retrieved_evidence", [])
                if evidence:
                    selected = st.selectbox("Evidence node", options=[row.get("node_id") for row in evidence])
                    row = next((r for r in evidence if r.get("node_id") == selected), None)
                    if row:
                        evidence_payload = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
                        if evidence_payload.get("source_kind") == "tabular":
                            st.markdown(
                                f"**Document:** {evidence_payload.get('name') or evidence_payload.get('doc_id')}\n\n"
                                f"**Sheet:** {evidence_payload.get('sheet_name') or 'n/a'}\n\n"
                                f"**Rows:** {evidence_payload.get('row_start')} - {evidence_payload.get('row_end')}\n\n"
                                f"**Range:** {evidence_payload.get('cell_range') or 'n/a'}"
                            )
                            if evidence_payload.get("table_preview"):
                                st.code(str(evidence_payload.get("table_preview")), language="text")
                        else:
                            st.json(row)
                        image_path = evidence_payload.get("image_path")
                        if image_path and os.path.exists(image_path):
                            st.image(image_path, caption=f"Evidence image: {selected}")
                else:
                    st.info("No evidence rows found for run.")
            else:
                st.warning(f"Unable to load run: status={status} data={run_data}")

            st.subheader("Feedback")
            feedback_reason = st.text_input("Reason (optional)", value="", key="feedback_reason")
            f1, f2 = st.columns(2)
            with f1:
                if st.button("Thumb Up", use_container_width=True):
                    _api_request(
                        "POST",
                        "/feedback",
                        payload={"run_id": run_id, "thumb": "up", "reason": feedback_reason or None},
                        timeout=15,
                    )
                    st.success("Feedback stored.")
            with f2:
                if st.button("Thumb Down", use_container_width=True):
                    _api_request(
                        "POST",
                        "/feedback",
                        payload={"run_id": run_id, "thumb": "down", "reason": feedback_reason or None},
                        timeout=15,
                    )
                    st.success("Feedback stored.")
