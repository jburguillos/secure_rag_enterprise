"""Streamlit UI for secure RAG MVP."""

from __future__ import annotations

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


with st.sidebar:
    st.header("Connection")
    api_url = st.text_input("API URL", value=API_BASE_URL)
    token = st.text_input("Bearer token (optional in Phase 1)", value="", type="password")

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

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if st.button("Ingest Google Drive", use_container_width=True):
        payload = {
            "folder_id": folder_id,
            "auth_mode": auth_mode,
            "dry_run": False,
            "dataset_source": "google_drive",
        }
        try:
            response = requests.post(f"{api_url}/ingest/gdrive", json=payload, headers=headers, timeout=120)
            st.json(response.json())
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

    if st.button("Ingest Local Folder", use_container_width=True):
        payload = {
            "path": local_path,
            "acl_sidecar_path": local_acl,
            "dry_run": False,
            "dataset_source": "local_folder",
        }
        try:
            response = requests.post(f"{api_url}/ingest/local", json=payload, headers=headers, timeout=120)
            st.json(response.json())
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))

st.title("Secure Multimodal RAG MVP")
st.caption("Grounded Q&A with retrieval-time ACL filtering, citations, and audit run IDs")

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
                response = requests.post(f"{api_url}/query", json=payload, headers=headers, timeout=120)
                data = response.json()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
                data = None

        if data:
            answer = data.get("answer", "")
            st.markdown(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})
            st.session_state.last_response = data
            st.session_state.last_run = data.get("run_id")

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
            run_resp = requests.get(f"{api_url}/runs/{run_id}", headers=headers, timeout=30)
            run_data = run_resp.json()
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
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Unable to load run evidence: {exc}")

        st.subheader("Feedback")
        feedback_reason = st.text_input("Reason (optional)", value="", key="feedback_reason")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Thumb Up", use_container_width=True):
                requests.post(
                    f"{api_url}/feedback",
                    json={"run_id": run_id, "thumb": "up", "reason": feedback_reason or None},
                    headers=headers,
                    timeout=15,
                )
                st.success("Feedback stored.")
        with c2:
            if st.button("Thumb Down", use_container_width=True):
                requests.post(
                    f"{api_url}/feedback",
                    json={"run_id": run_id, "thumb": "down", "reason": feedback_reason or None},
                    headers=headers,
                    timeout=15,
                )
                st.success("Feedback stored.")
