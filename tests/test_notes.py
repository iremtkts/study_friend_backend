from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.dependencies import get_current_user
from app.db.session import Base, get_db
from app.main import app
from app.models import Category, Note, User
import app.services.storage as storage


@pytest.fixture
def notes_client(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with TestingSession() as db:
        user_one = User(
            email="one@example.com",
            password_hash="hash-one",
            is_active=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        user_two = User(
            email="two@example.com",
            password_hash="hash-two",
            is_active=True,
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add_all([user_one, user_two])
        db.commit()
        db.refresh(user_one)
        db.refresh(user_two)

    selected_user = {"value": user_one}

    def override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: selected_user["value"]
    from app.services import n8n_client

    monkeypatch.setattr(n8n_client, "is_configured", lambda: False)
    monkeypatch_settings = SimpleNamespace(upload_dir=tmp_path, max_upload_mb=10)

    with patch.object(storage, "get_settings", return_value=monkeypatch_settings):
        with TestClient(app) as client:
            yield SimpleNamespace(
                client=client,
                user_one=user_one,
                user_two=user_two,
                selected_user=selected_user,
                session_factory=TestingSession,
                upload_dir=tmp_path,
            )

    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def create_category(client: TestClient, name: str = "Course") -> dict:
    response = client.post("/api/v1/categories", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


def test_other_users_categories_and_notes_are_hidden(notes_client):
    client = notes_client.client
    category = create_category(client)
    created_note = client.post(
        "/api/v1/notes",
        data={"category_id": category["id"]},
        files={"file": ("lecture.txt", b"private note text", "application/octet-stream")},
    )
    assert created_note.status_code == 201, created_note.text
    note_id = created_note.json()["id"]
    assert "stored_filename" not in created_note.json()
    assert "extracted_text" not in created_note.json()

    listed = client.get("/api/v1/notes", params={"category_id": category["id"]})
    assert listed.status_code == 200
    assert "extracted_text" not in listed.json()[0]

    notes_client.selected_user["value"] = notes_client.user_two
    assert client.get(f"/api/v1/notes/{note_id}").status_code == 404
    assert client.get("/api/v1/notes", params={"category_id": category["id"]}).status_code == 404
    assert client.patch(f"/api/v1/categories/{category['id']}", json={"name": "Other"}).status_code == 404
    assert client.delete(f"/api/v1/categories/{category['id']}").status_code == 404

    # A foreign category is rejected before the upload storage layer reads or writes the file.
    with patch("app.api.v1.endpoints.content.store_upload") as save_upload:
        response = client.post(
            "/api/v1/notes",
            data={"category_id": category["id"]},
            files={"file": ("unused.txt", b"must not be read", "text/plain")},
        )
    assert response.status_code == 404
    save_upload.assert_not_called()


def test_notes_requires_category_id(notes_client):
    response = notes_client.client.post(
        "/api/v1/notes",
        files={"file": ("note.txt", b"content", "text/plain")},
    )
    assert response.status_code == 422
    assert list(notes_client.upload_dir.iterdir()) == []


def test_rejects_fake_pdf_extension(notes_client):
    category = create_category(notes_client.client)
    response = notes_client.client.post(
        "/api/v1/notes",
        data={"category_id": category["id"]},
        files={"file": ("fake.pdf", b"this is only plain text", "application/pdf")},
    )
    assert response.status_code == 422
    assert list(notes_client.upload_dir.iterdir()) == []


def test_rejects_upload_over_configured_size_limit(notes_client, monkeypatch):
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: SimpleNamespace(upload_dir=notes_client.upload_dir, max_upload_mb=0.00001),
    )
    category = create_category(notes_client.client)
    response = notes_client.client.post(
        "/api/v1/notes",
        data={"category_id": category["id"]},
        files={"file": ("large.txt", b"this text exceeds ten bytes", "text/plain")},
    )
    assert response.status_code == 413
    assert list(notes_client.upload_dir.iterdir()) == []


def test_category_with_notes_cannot_be_deleted_and_note_delete_removes_file(notes_client):
    client = notes_client.client
    category = create_category(client)
    created = client.post(
        "/api/v1/notes",
        data={"category_id": category["id"]},
        files={"file": ("lecture.txt", b"private note text", "text/plain")},
    )
    assert created.status_code == 201, created.text
    note_id = created.json()["id"]
    with notes_client.session_factory() as db:
        note = db.scalar(select(Note).where(Note.id == uuid.UUID(note_id)))
        stored_filename = note.stored_filename
        assert (notes_client.upload_dir / stored_filename).exists()

    assert client.delete(f"/api/v1/categories/{category['id']}").status_code == 409
    deleted_note = client.delete(f"/api/v1/notes/{note_id}")
    assert deleted_note.status_code == 204
    assert not (notes_client.upload_dir / stored_filename).exists()
