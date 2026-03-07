"""Google Drive connector using LlamaHub (primary) with metadata/ACL enrichment."""

from __future__ import annotations

import inspect
import io
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document as DocxDocument
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from llama_index.core import Document
from llama_index.readers.google import GoogleDriveReader
from pypdf import PdfReader

from app.config import get_settings
from app.ingestion.drive_auth import oauth_credentials, service_account_credentials

logger = logging.getLogger(__name__)

SUPPORTED_MIME_TYPES = {
    "application/vnd.google-apps.document",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
}


@dataclass
class DriveFile:
    file_id: str
    name: str
    mime_type: str
    web_view_link: str | None
    modified_time: str | None


def _parse_modified_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_drive_service(auth_mode: str):
    settings = get_settings()
    if auth_mode == "service_account":
        if not settings.google_service_account_json:
            raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON is required for service_account mode")
        creds = service_account_credentials(settings.google_service_account_json)
    else:
        creds = oauth_credentials(settings.google_credentials_path, settings.google_token_path)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_drive_files(folder_id: str, auth_mode: str) -> tuple[list[DriveFile], list[dict[str, Any]]]:
    service = _build_drive_service(auth_mode)
    query = f"'{folder_id}' in parents and trashed=false"
    page_token = None
    supported: list[DriveFile] = []
    skipped: list[dict[str, Any]] = []

    while True:
        response = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id,name,mimeType,webViewLink,modifiedTime)",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                corpora="allDrives",
                pageToken=page_token,
                pageSize=1000,
            )
            .execute()
        )

        for item in response.get("files", []):
            mime = item.get("mimeType", "")
            entry = DriveFile(
                file_id=item.get("id", ""),
                name=item.get("name", ""),
                mime_type=mime,
                web_view_link=item.get("webViewLink"),
                modified_time=item.get("modifiedTime"),
            )
            if mime in SUPPORTED_MIME_TYPES and entry.file_id:
                supported.append(entry)
            else:
                skipped.append({"file_id": entry.file_id, "name": entry.name, "mimeType": mime})

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return supported, skipped


def fetch_permissions(auth_mode: str, file_id: str) -> dict[str, Any]:
    service = _build_drive_service(auth_mode)
    default = {
        "allowed_emails": [],
        "allowed_domains": [],
        "allowed_users": [],
        "allowed_groups": [],
        "is_public": False,
        "permissions_raw": [],
    }
    try:
        response = (
            service.permissions()
            .list(
                fileId=file_id,
                fields="permissions(id,type,emailAddress,domain,role,allowFileDiscovery)",
                supportsAllDrives=True,
            )
            .execute()
        )
    except Exception:  # noqa: BLE001
        return default

    emails: set[str] = set()
    domains: set[str] = set()
    allowed_users: set[str] = set()
    allowed_groups: set[str] = set()
    is_public = False

    permissions = response.get("permissions", [])
    for perm in permissions:
        p_type = perm.get("type")
        email = str(perm.get("emailAddress") or "").lower()
        domain = str(perm.get("domain") or "").lower()

        if p_type == "user" and email:
            emails.add(email)
            allowed_users.add(email)
        elif p_type == "group" and email:
            emails.add(email)
            allowed_groups.add(email)
        elif p_type == "domain" and domain:
            domains.add(domain)
        elif p_type == "anyone":
            is_public = True

    return {
        "allowed_emails": sorted(emails),
        "allowed_domains": sorted(domains),
        "allowed_users": sorted(allowed_users),
        "allowed_groups": sorted(allowed_groups),
        "is_public": is_public,
        "permissions_raw": permissions,
    }


def _extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    chunks: list[str] = []
    for page in reader.pages:
        chunks.append(page.extract_text() or "")
    return "\n\n".join(chunks).strip()


def _extract_docx_text(content: bytes) -> str:
    doc = DocxDocument(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs if p.text).strip()


def _download_file_content(auth_mode: str, file_id: str, mime_type: str) -> str:
    service = _build_drive_service(auth_mode)
    if mime_type == "application/vnd.google-apps.document":
        request = service.files().export_media(fileId=file_id, mimeType="text/plain")
    else:
        request = service.files().get_media(fileId=file_id, supportsAllDrives=True)

    stream = io.BytesIO()
    downloader = MediaIoBaseDownload(stream, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    payload = stream.getvalue()
    if mime_type == "application/pdf":
        return _extract_pdf_text(payload)
    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return _extract_docx_text(payload)
    return payload.decode("utf-8", errors="ignore")


def _reader_instance(auth_mode: str):
    settings = get_settings()
    options: list[dict[str, Any]] = []
    if auth_mode == "service_account" and settings.google_service_account_json:
        options.extend(
            [
                {"service_account_key_file": settings.google_service_account_json},
                {"service_account_key": json.loads(Path(settings.google_service_account_json).read_text(encoding="utf-8"))},
            ]
        )
    else:
        options.extend(
            [
                {
                    "credentials_path": settings.google_credentials_path,
                    "token_path": settings.google_token_path,
                },
                {
                    "client_config": json.loads(Path(settings.google_credentials_path).read_text(encoding="utf-8")),
                    "authorized_user_info": json.loads(Path(settings.google_token_path).read_text(encoding="utf-8")),
                },
            ]
        )
    options.append({})

    errors: list[str] = []
    for kwargs in options:
        try:
            sig = inspect.signature(GoogleDriveReader)
            filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
            return GoogleDriveReader(**filtered)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    raise RuntimeError("Unable to initialize GoogleDriveReader: " + " | ".join(errors))


def _reader_load(reader: GoogleDriveReader, *, folder_id: str, file_ids: list[str], auth_mode: str) -> list[Document]:
    settings = get_settings()
    kwargs_options: list[dict[str, Any]] = [
        {"folder_id": folder_id, "file_ids": file_ids, "supportsAllDrives": True, "includeItemsFromAllDrives": True},
        {"folder_id": folder_id, "file_ids": file_ids},
    ]
    if auth_mode == "service_account" and settings.google_service_account_json:
        kwargs_options.insert(
            0,
            {
                "folder_id": folder_id,
                "file_ids": file_ids,
                "service_account_key_file": settings.google_service_account_json,
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
            },
        )

    errors: list[str] = []
    for kwargs in kwargs_options:
        try:
            sig = inspect.signature(reader.load_data)
            filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
            docs = reader.load_data(**filtered)
            return list(docs)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    logger.warning("GoogleDriveReader failed for folder=%s: %s", folder_id, " | ".join(errors))
    return []


def load_drive_documents(folder_id: str, auth_mode: str) -> tuple[list[Document], list[dict[str, Any]]]:
    """Load drive docs and enrich with ACL metadata.

    Returns documents and skipped-file metadata.
    """

    supported_files, skipped = list_drive_files(folder_id, auth_mode)
    if not supported_files:
        return [], skipped

    file_ids = [item.file_id for item in supported_files]
    try:
        reader = _reader_instance(auth_mode)
        reader_docs = _reader_load(reader, folder_id=folder_id, file_ids=file_ids, auth_mode=auth_mode)
    except Exception as exc:  # noqa: BLE001
        logger.warning("GoogleDriveReader init failed for folder=%s: %s", folder_id, exc)
        reader_docs = []

    doc_by_file_id: dict[str, Document] = {}
    for doc in reader_docs:
        md = dict(doc.metadata or {})
        file_id = md.get("file_id") or md.get("id") or md.get("source_id")
        if file_id:
            doc_by_file_id[str(file_id)] = doc

    output: list[Document] = []

    for file_meta in supported_files:
        doc = doc_by_file_id.get(file_meta.file_id)
        if doc is None:
            try:
                content = _download_file_content(auth_mode, file_meta.file_id, file_meta.mime_type)
                doc = Document(text=content)
            except Exception as exc:  # noqa: BLE001
                skipped.append(
                    {
                        "file_id": file_meta.file_id,
                        "name": file_meta.name,
                        "mimeType": file_meta.mime_type,
                        "reason": f"content_download_failed: {exc}",
                    }
                )
                continue

        metadata = dict(doc.metadata or {})
        permissions = fetch_permissions(auth_mode, file_meta.file_id)
        metadata.update(
            {
                "doc_id": file_meta.file_id,
                "file_id": file_meta.file_id,
                "name": file_meta.name,
                "title": metadata.get("title") or file_meta.name,
                "mimeType": file_meta.mime_type,
                "webViewLink": file_meta.web_view_link,
                "modifiedTime": file_meta.modified_time,
                "dataset_source": "google_drive",
                **permissions,
            }
        )
        doc.metadata = metadata
        doc.id_ = file_meta.file_id
        output.append(doc)

    return output, skipped


def modified_time_from_document(doc: Document) -> datetime | None:
    return _parse_modified_time((doc.metadata or {}).get("modifiedTime"))

