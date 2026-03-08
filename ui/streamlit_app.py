"""Streamlit UI for secure RAG MVP."""

from __future__ import annotations

import json
import os
from typing import Any

import requests
import streamlit as st


API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")

st.set_page_config(page_title="Secure RAG MVP", layout="wide")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "last_response" not in st.session_state:
    st.session_state.last_response = None
if "last_run" not in st.session_state:
    st.session_state.last_run = None
if "admin_mapping_text" not in st.session_state:
    st.session_state.admin_mapping_text = "{}"


with st.sidebar:
    st.header("Connection")
    api_url = st.text_input("API URL", value=API_BASE_URL)
    token = st.text_input("Bearer token", value="", type="password")

    st.header("User Context")
    user_email = st.text_input("email", value="")
    user_domain = st.text_input("domain", value="")
    groups_text = st.text_area("groups (one per line)", value="")
    user_groups = [g.strip() for g in groups_text.splitlines() if g.strip()]

    st.header("Ingestion")
    folder_id = st.text_input("Google Drive folder id", value="")
    auth_mode = st.selectbox("Drive auth mode", options=["oauth", "service_account"], index=0)
    local_path = st.text_input("Local path", value="./tests/data/sample_docs")
    local_acl = st.text_input("Local ACL sidecar", value="./tests/data/sample_docs/acl_map.yaml")


def _headers() -> dict[str, str]:
    out = {"Content-Type": "application/json"}
    if token:
        out["Authorization"] = f"Bearer {token}"
    return out


def _api_request(method: str, path: str, *, payload: dict[str, Any] | None = None, params: dict[str, Any] | None = None, timeout: int = 60):
    url = f"{api_url}{path}"
    response = requests.request(method, url, json=payload, params=params, headers=_headers(), timeout=timeout)
    data = response.json() if response.content else {}
    return response.status_code, data


st.title("Secure Multimodal RAG MVP")
st.caption("Grounded Q&A with retrieval-time ACL filtering, citations, and audit run IDs")

chat_tab, admin_tab = st.tabs(["Chat", "Admin"])

with chat_tab:
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Ingest Google Drive", use_container_width=True):
            payload = {
                "folder_id": folder_id,
                "auth_mode": auth_mode,
                "dry_run": False,
                "dataset_source": "google_drive",
            }
            try:
                status, data = _api_request("POST", "/ingest/gdrive", payload=payload, timeout=180)
                st.caption(f"status={status}")
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
                status, data = _api_request("POST", "/ingest/local", payload=payload, timeout=180)
                st.caption(f"status={status}")
                st.json(data)
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    question = st.chat_input("Ask a question")
    if question:
        st.session_state.messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)

        payload = {
            "query": question,
            "mode": "qa",
            "include_images": True,
            "user_context": {
                "email": user_email,
                "domain": user_domain,
                "groups": user_groups,
            },
        }

        with st.chat_message("assistant"):
            with st.spinner("Querying..."):
                try:
                    status, data = _api_request("POST", "/query", payload=payload, timeout=120)
                    if status >= 400:
                        st.error(f"status={status} {data}")
                    else:
                        answer = data.get("answer", "")
                        st.markdown(answer)
                        st.session_state.messages.append({"role": "assistant", "content": answer})
                        st.session_state.last_response = data
                        st.session_state.last_run = data.get("run_id")
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))

    if st.session_state.last_response:
        result: dict[str, Any] = st.session_state.last_response
        st.subheader("Citations")
        citations = result.get("citations") or []
        if not citations:
            st.info("No citations returned.")
        else:
            for idx, citation in enumerate(citations, start=1):
                st.markdown(
                    f"{idx}. **{citation.get('doc_name') or citation.get('doc_id')}** "
                    f"(page={citation.get('page')}, node={citation.get('node_id')}, chunk={citation.get('chunk_id')})"
                )

        run_id = result.get("run_id")
        if run_id:
            st.subheader("Evidence Viewer")
            try:
                status, run_data = _api_request("GET", f"/runs/{run_id}", timeout=30)
                if status < 400:
                    evidence = run_data.get("retrieved_evidence", [])
                    if evidence:
                        selected = st.selectbox("Evidence node", options=[row.get("node_id") for row in evidence])
                        row = next((r for r in evidence if r.get("node_id") == selected), None)
                        if row:
                            st.json(row)
                            payload = row.get("payload", {}) if isinstance(row.get("payload"), dict) else {}
                            image_path = payload.get("image_path")
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
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Thumb Up", use_container_width=True):
                    _api_request(
                        "POST",
                        "/feedback",
                        payload={"run_id": run_id, "thumb": "up", "reason": feedback_reason or None},
                        timeout=15,
                    )
                    st.success("Feedback stored.")
            with c2:
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
        status, data = _api_request("POST", "/admin/sync/gdrive", payload=payload, timeout=180)
        if status < 400:
            st.success("Drive sync completed")
            st.json(data)
        else:
            st.error(f"status={status} data={data}")
