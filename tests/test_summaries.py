from uuid import UUID

from sqlalchemy import select

from app.models.summary import Summary
from app.schemas.summary import SummaryCreate
from app.services.summaries import create_summaries_bulk
from tests.test_notes import create_category, notes_client


def create_note(client, category_id: str) -> dict:
    response = client.post(
        "/api/v1/notes",
        data={"category_id": category_id},
        files={"file": ("source.txt", b"note source", "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_summary(client, note_id: str, content: str = "Summary") -> dict:
    response = client.post(f"/api/v1/notes/{note_id}/summaries", json={"content": content})
    assert response.status_code == 201, response.text
    return response.json()


def test_foreign_note_card_and_category_listing_are_hidden(notes_client):
    client = notes_client.client
    category = create_category(client)
    note = create_note(client, category["id"])

    notes_client.selected_user["value"] = notes_client.user_two
    assert client.get(f"/api/v1/notes/{note['id']}/summaries").status_code == 404
    assert client.post(
        f"/api/v1/notes/{note['id']}/summaries", json={"content": "private"}
    ).status_code == 404
    assert client.get("/api/v1/summaries", params={"category_id": category["id"]}).status_code == 404


def test_foreign_summary_cannot_be_updated_or_deleted(notes_client):
    client = notes_client.client
    category = create_category(client)
    note = create_note(client, category["id"])
    summary = create_summary(client, note["id"])

    notes_client.selected_user["value"] = notes_client.user_two
    assert client.patch(
        f"/api/v1/summaries/{summary['id']}", json={"content": "hijack"}
    ).status_code == 404
    assert client.delete(f"/api/v1/summaries/{summary['id']}").status_code == 404

    with notes_client.session_factory() as db:
        stored = db.scalar(select(Summary).where(Summary.id == UUID(summary["id"])))
        assert stored.content == "Summary"


def test_source_is_server_assigned_manual(notes_client):
    client = notes_client.client
    category = create_category(client)
    note = create_note(client, category["id"])
    created = client.post(
        f"/api/v1/notes/{note['id']}/summaries",
        json={"content": "  trimmed card  "},
    )
    assert created.status_code == 201, created.text
    assert created.json()["content"] == "trimmed card"
    assert created.json()["source"] == "manual"

    with_source = client.post(
        f"/api/v1/notes/{note['id']}/summaries",
        json={"content": "card", "source": "ai"},
    )
    assert with_source.status_code == 422


def test_summary_character_limit_and_blank_content(notes_client, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.schemas.summary.get_settings",
        lambda: SimpleNamespace(summary_max_chars=4),
    )
    client = notes_client.client
    category = create_category(client)
    note = create_note(client, category["id"])
    assert client.post(
        f"/api/v1/notes/{note['id']}/summaries", json={"content": "  abcde "}
    ).status_code == 422
    assert client.post(
        f"/api/v1/notes/{note['id']}/summaries", json={"content": "   "}
    ).status_code == 422
    valid = client.post(
        f"/api/v1/notes/{note['id']}/summaries", json={"content": " abcd "}
    )
    assert valid.status_code == 201
    assert valid.json()["content"] == "abcd"


def test_bulk_summaries_preserve_input_order(notes_client):
    category = create_category(notes_client.client)
    note = create_note(notes_client.client, category["id"])
    with notes_client.session_factory() as db:
        created = create_summaries_bulk(
            db,
            notes_client.user_one.id,
            UUID(note["id"]),
            [" first ", "second"],
            "ai",
        )
    assert [item.content for item in created] == ["first", "second"]
    assert [item.position for item in created] == [0, 1]


def test_deleting_note_cascades_to_summaries(notes_client):
    client = notes_client.client
    category = create_category(client)
    note = create_note(client, category["id"])
    summary = create_summary(client, note["id"])
    assert client.delete(f"/api/v1/notes/{note['id']}").status_code == 204
    with notes_client.session_factory() as db:
        assert db.scalar(select(Summary).where(Summary.id == UUID(summary["id"]))) is None


def test_summary_schema_trims_content():
    assert SummaryCreate(content="  card  ").content == "card"
