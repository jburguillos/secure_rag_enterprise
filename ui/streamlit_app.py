"""Streamlit UI for secure RAG MVP."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import requests
import streamlit as st


API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
DEFAULT_DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "")
UI_QUERY_TIMEOUT_SEC = int(os.getenv("UI_QUERY_TIMEOUT_SEC", "300"))
UI_INGEST_POLL_INTERVAL_SEC = float(os.getenv("UI_INGEST_POLL_INTERVAL_SEC", "2"))
UI_INGEST_POLL_TIMEOUT_SEC = int(os.getenv("UI_INGEST_POLL_TIMEOUT_SEC", "1800"))
UI_TOKEN_REFRESH_SKEW_SEC = int(os.getenv("UI_TOKEN_REFRESH_SKEW_SEC", "30"))
KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER", "http://keycloak:8080/realms/secure-rag").rstrip("/")
KEYCLOAK_TOKEN_URL = os.getenv("KEYCLOAK_TOKEN_URL", f"{KEYCLOAK_ISSUER}/protocol/openid-connect/token")
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "secure-rag-api")
DEFAULT_AUTH_USERNAME = os.getenv("UI_DEFAULT_USERNAME", "")
DEFAULT_AUTH_PASSWORD = os.getenv("UI_DEFAULT_PASSWORD", "")

st.set_page_config(page_title="Secure RAG MVP", layout="wide")

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


with st.sidebar:
    st.header("Connection")
    st.session_state.api_url = st.text_input("API URL", value=st.session_state.api_url)

    st.subheader("Auth (Keycloak)")
    st.caption("Use Login for auto-refresh, or paste a manual bearer token override.")

    st.session_state.manual_token = st.text_input(
        "Manual bearer token (optional override)",
        value=st.session_state.manual_token,
        type="password",
    ).strip()

    auth_username_input = st.text_input(
        "Keycloak username",
        value=st.session_state.auth_username,
        key="auth_username_input",
    ).strip()
    auth_password_input = st.text_input(
        "Keycloak password",
        value=st.session_state.auth_password,
        type="password",
        key="auth_password_input",
    )
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

    st.header("User Context")
    user_email = st.text_input("email", value="")
    user_domain = st.text_input("domain", value="")
    groups_text = st.text_area("groups (one per line)", value="")
    user_groups = [g.strip() for g in groups_text.splitlines() if g.strip()]

    st.header("Ingestion")
    folder_id = st.text_input("Google Drive folder id", value=DEFAULT_DRIVE_FOLDER_ID)
    auth_mode = st.selectbox("Drive auth mode", options=["oauth", "service_account"], index=0)
    local_path = st.text_input("Local path", value="./tests/data/sample_docs")
    local_acl = st.text_input("Local ACL sidecar", value="./tests/data/sample_docs/acl_map.yaml")


st.title("Secure Multimodal RAG MVP")
st.caption("Grounded Q&A with retrieval-time ACL filtering, citations, and audit run IDs")

chat_tab, admin_tab = st.tabs(["Chat", "Admin"])

with chat_tab:
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Ingest Google Drive", use_container_width=True):
            if not folder_id.strip():
                st.error("Google Drive folder id is required.")
            else:
                payload = {
                    "folder_id": folder_id,
                    "auth_mode": auth_mode,
                    "dry_run": False,
                    "dataset_source": "google_drive",
                }
                try:
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
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))

    with c2:
        if st.button("Ingest Local Folder", use_container_width=True):
            payload = {
                "path": local_path,
                "acl_sidecar_path": local_acl,
                "dry_run": False,
                "dataset_source": "local_folder",
            }
            try:
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
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    retrieval_modes = ["auto", "rag", "chat"]
    default_mode_index = retrieval_modes.index(st.session_state.chat_retrieval_mode) if st.session_state.chat_retrieval_mode in retrieval_modes else 0
    st.session_state.chat_retrieval_mode = st.selectbox(
        "Retrieval mode",
        options=retrieval_modes,
        index=default_mode_index,
        help="auto = detect chat acknowledgements, rag = always retrieve evidence, chat = never retrieve. Context is isolated per mode.",
    )

    active_mode = st.session_state.chat_retrieval_mode
    if active_mode not in st.session_state.messages_by_mode:
        st.session_state.messages_by_mode[active_mode] = []
    if active_mode not in st.session_state.last_response_by_mode:
        st.session_state.last_response_by_mode[active_mode] = None
    if active_mode not in st.session_state.last_run_by_mode:
        st.session_state.last_run_by_mode[active_mode] = None

    active_messages = st.session_state.messages_by_mode[active_mode]

    for msg in active_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    question = st.chat_input("Ask a question")
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
                "email": user_email,
                "domain": user_domain,
                "groups": user_groups,
            },
        }

        with st.chat_message("assistant"):
            with st.spinner("Querying..."):
                try:
                    status, data = _api_request("POST", "/query", payload=payload, timeout=UI_QUERY_TIMEOUT_SEC)
                    if status == 401:
                        st.error("Session expired or invalid token. Click Login in the sidebar to refresh your session.")
                    elif status >= 400:
                        st.error(f"status={status} {data}")
                    else:
                        answer = data.get("answer", "")
                        st.markdown(answer)
                        active_messages.append({"role": "assistant", "content": answer})
                        st.session_state.last_response_by_mode[active_mode] = data
                        st.session_state.last_run_by_mode[active_mode] = data.get("run_id")
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
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
            try:
                status, run_data = _api_request("GET", f"/runs/{run_id}", timeout=30)
                if status == 401:
                    st.warning("Session expired while loading evidence viewer. Click Login in the sidebar.")
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
            except Exception as exc:  # noqa: BLE001
                st.warning(f"Unable to load run evidence: {exc}")

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

with admin_tab:
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
        height=160,
    )

    if st.button("Save Drive Group Mapping"):
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

    st.markdown("### Keycloak Groups and Users")
    if st.button("Refresh Keycloak Data"):
        g_status, g_data = _api_request("GET", "/admin/keycloak/groups", params={"max": 200})
        u_status, u_data = _api_request("GET", "/admin/keycloak/users", params={"max": 200})
        if g_status < 400:
            st.write("Groups")
            st.json(g_data)
        else:
            st.error(f"groups status={g_status} data={g_data}")
        if u_status < 400:
            st.write("Users")
            st.json(u_data)
        else:
            st.error(f"users status={u_status} data={u_data}")

    with st.form("create_user_form"):
        st.markdown("### Create User")
        new_username = st.text_input("username", value="")
        new_email = st.text_input("email", value="")
        new_first = st.text_input("first_name", value="")
        new_last = st.text_input("last_name", value="")
        new_password = st.text_input("password", value="", type="password")
        new_groups_text = st.text_input("groups (comma separated)", value="")
        submit_create = st.form_submit_button("Create User")

        if submit_create:
            payload = {
                "username": new_username,
                "email": new_email,
                "password": new_password,
                "first_name": new_first or None,
                "last_name": new_last or None,
                "groups": [g.strip() for g in new_groups_text.split(",") if g.strip()],
                "enabled": True,
            }
            status, data = _api_request("POST", "/admin/keycloak/users", payload=payload)
            if status < 400:
                st.success("User created")
                st.json(data)
            else:
                st.error(f"status={status} data={data}")

    with st.form("set_groups_form"):
        st.markdown("### Set User Groups")
        target_user_id = st.text_input("user_id", value="")
        target_groups = st.text_input("target groups (comma separated)", value="")
        submit_groups = st.form_submit_button("Apply Groups")

        if submit_groups:
            payload = {"groups": [g.strip() for g in target_groups.split(",") if g.strip()]}
            status, data = _api_request("PUT", f"/admin/keycloak/users/{target_user_id}/groups", payload=payload)
            if status < 400:
                st.success("User groups updated")
                st.json(data)
            else:
                st.error(f"status={status} data={data}")

    st.markdown("### Access Preview")
    p_email = st.text_input("preview email", value=user_email)
    p_domain = st.text_input("preview domain", value=user_domain)
    p_groups = st.text_input("preview groups (comma separated)", value=",".join(user_groups))
    p_sources = st.text_input("sources filter (comma separated, optional)", value="")
    p_limit = st.number_input("preview limit", min_value=1, max_value=500, value=100)

    if st.button("Run Access Preview"):
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

    st.markdown("### Trigger Drive Sync")
    sync_folder = st.text_input("sync folder id", value=folder_id)
    sync_auth_mode = st.selectbox("sync auth mode", options=["oauth", "service_account"], index=0, key="sync_auth_mode")
    sync_dry_run = st.checkbox("sync dry run", value=False)
    if st.button("Run Drive Sync"):
        payload = {
            "folder_id": sync_folder,
            "auth_mode": sync_auth_mode,
            "dry_run": sync_dry_run,
            "dataset_source": "google_drive",
        }
        status, data = _run_ingest_job_with_poll(
            endpoint="/admin/sync/gdrive/async",
            payload=payload,
            label="Admin Drive sync",
        )
        if status < 400:
            st.success("Drive sync completed")
            st.json(data)
        else:
            st.error(f"status={status} data={data}")



















