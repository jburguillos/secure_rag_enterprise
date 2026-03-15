from __future__ import annotations

from typing import Any

from llama_index.core import Document

from app.config import get_settings
from app.ingestion.gdrive_connector import load_drive_documents, list_drive_files


class _FakeRequest:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def execute(self) -> dict[str, Any]:
        return self._response


class _FakeFilesResource:
    def __init__(self, listing_by_folder: dict[str, list[dict[str, Any]]]) -> None:
        self._listing_by_folder = listing_by_folder

    def list(self, *, q: str, **_: Any) -> _FakeRequest:
        folder_id = q.split("'")[1]
        return _FakeRequest({"files": self._listing_by_folder.get(folder_id, [])})


class _FakeService:
    def __init__(self, listing_by_folder: dict[str, list[dict[str, Any]]]) -> None:
        self._files = _FakeFilesResource(listing_by_folder)

    def files(self) -> _FakeFilesResource:
        return self._files


def test_list_drive_files_recurses_into_subfolders() -> None:
    service = _FakeService(
        {
            "root": [
                {"id": "sub-a", "name": "theme-a", "mimeType": "application/vnd.google-apps.folder"},
                {"id": "root-file", "name": "root.txt", "mimeType": "text/plain"},
                {"id": "ignore-me", "name": "raw.csv", "mimeType": "text/csv"},
            ],
            "sub-a": [
                {"id": "nested-file", "name": "notes.txt", "mimeType": "text/plain"},
                {"id": "sub-b", "name": "deep", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "sub-b": [
                {"id": "deep-file", "name": "paper.pdf", "mimeType": "application/pdf"},
            ],
        }
    )

    supported, skipped = list_drive_files("root", service)

    assert {item.file_id for item in supported} == {"root-file", "nested-file", "deep-file"}
    assert {item.drive_path for item in supported} == {
        "root.txt",
        "theme-a/notes.txt",
        "theme-a/deep/paper.pdf",
    }
    assert {item.folder_path for item in supported} == {"", "theme-a", "theme-a/deep"}
    assert skipped == [{"file_id": "ignore-me", "name": "raw.csv", "mimeType": "text/csv", "path": "raw.csv"}]


def test_load_drive_documents_preserves_nested_drive_path_metadata(monkeypatch) -> None:
    service = _FakeService(
        {
            "root": [
                {"id": "sub-a", "name": "vc", "mimeType": "application/vnd.google-apps.folder"},
            ],
            "sub-a": [
                {"id": "nested-file", "name": "paper.txt", "mimeType": "text/plain", "webViewLink": "https://drive/file"},
            ],
        }
    )

    monkeypatch.setattr("app.ingestion.gdrive_connector._reader_instance", lambda auth_mode: object())
    monkeypatch.setattr("app.ingestion.gdrive_connector._reader_load", lambda reader, *, folder_id, file_ids, auth_mode: [])
    monkeypatch.setattr(
        "app.ingestion.gdrive_connector._download_file_content",
        lambda *, file_id, mime_type, service, file_name: ("nested drive content", {}),
    )
    monkeypatch.setattr(
        "app.ingestion.gdrive_connector.fetch_permissions",
        lambda file_id, service: {
            "allowed_emails": [],
            "allowed_domains": ["example.com"],
            "allowed_users": [],
            "allowed_groups": [],
            "is_public": False,
            "permissions_raw": [],
        },
    )

    docs, skipped = load_drive_documents(folder_id="root", auth_mode="oauth", service=service)

    assert skipped == []
    assert len(docs) == 1
    doc = docs[0]
    assert isinstance(doc, Document)
    assert doc.text == "nested drive content"
    assert doc.id_ == "nested-file"
    assert doc.metadata["root_folder_id"] == "root"
    assert doc.metadata["parent_folder_id"] == "sub-a"
    assert doc.metadata["folder_path"] == "vc"
    assert doc.metadata["drive_path"] == "vc/paper.txt"
    assert "vc" in (doc.metadata.get("folder_ancestors") or [])
    assert "vc/paper.txt" in (doc.metadata.get("path_ancestors") or [])
    assert doc.metadata["allowed_domains"] == ["example.com"]


def test_load_drive_documents_uses_native_path_by_default(monkeypatch) -> None:
    get_settings.cache_clear()
    service = _FakeService(
        {
            "root": [
                {"id": "file-a", "name": "native.txt", "mimeType": "text/plain", "webViewLink": "https://drive/file"},
            ],
        }
    )

    def _reader_should_not_run(auth_mode: str) -> object:
        raise AssertionError("GoogleDriveReader should be disabled by default for bulk ingest")

    monkeypatch.setattr("app.ingestion.gdrive_connector._reader_instance", _reader_should_not_run)
    monkeypatch.setattr(
        "app.ingestion.gdrive_connector._download_file_content",
        lambda *, file_id, mime_type, service, file_name: ("native content", {}),
    )
    monkeypatch.setattr(
        "app.ingestion.gdrive_connector.fetch_permissions",
        lambda file_id, service: {
            "allowed_emails": [],
            "allowed_domains": [],
            "allowed_users": [],
            "allowed_groups": [],
            "is_public": False,
            "permissions_raw": [],
        },
    )

    docs, skipped = load_drive_documents(folder_id="root", auth_mode="oauth", service=service)

    assert skipped == []
    assert len(docs) == 1
    assert docs[0].text == "native content"
