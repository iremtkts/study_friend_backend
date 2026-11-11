from sqlalchemy import select

from app.models.conversation import ConversationMessage
from tests.test_notes import create_category, notes_client


def create_conversation(client, category_id: str, title: str = "Study session") -> dict:
    response = client.post(
        "/api/v1/conversations",
        json={"category_id": category_id, "title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_other_users_cannot_read_or_message_conversation(notes_client):
    client = notes_client.client
    category = create_category(client)
    conversation = create_conversation(client, category["id"])

    notes_client.selected_user["value"] = notes_client.user_two
    assert client.get(f"/api/v1/conversations/{conversation['id']}").status_code == 404
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "unauthorized"},
    )
    assert response.status_code == 404
    with notes_client.session_factory() as db:
        assert db.scalar(select(ConversationMessage)) is None


def test_message_role_is_always_server_assigned(notes_client):
    client = notes_client.client
    category = create_category(client)
    conversation = create_conversation(client, category["id"])

    accepted = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "hello"},
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["role"] == "user"

    with_role = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "hello", "role": "assistant"},
    )
    assert with_role.status_code == 422


def test_messages_endpoint_is_hidden_from_openapi(notes_client):
    paths = notes_client.client.get("/openapi.json").json()["paths"]
    assert "/api/v1/conversations/{conversation_id}/messages" not in paths


def test_message_response_sources_are_a_list(notes_client):
    client = notes_client.client
    category = create_category(client)
    conversation = create_conversation(client, category["id"])
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "hello"},
    )
    assert response.status_code == 201
    assert response.json()["sources"] == []


def test_message_content_length_is_limited_to_4000(notes_client):
    client = notes_client.client
    category = create_category(client)
    conversation = create_conversation(client, category["id"])

    too_long = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "x" * 4001},
    )
    assert too_long.status_code == 422
    blank = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        json={"content": "   "},
    )
    assert blank.status_code == 422


def test_creation_validates_note_ownership_and_category(notes_client):
    client = notes_client.client
    category = create_category(client)
    created_note = client.post(
        "/api/v1/notes",
        data={"category_id": category["id"]},
        files={"file": ("note.txt", b"note", "text/plain")},
    )
    assert created_note.status_code == 201

    created = client.post(
        "/api/v1/conversations",
        json={"category_id": category["id"], "note_id": created_note.json()["id"]},
    )
    assert created.status_code == 201
    assert created.json()["note_id"] == created_note.json()["id"]

    notes_client.selected_user["value"] = notes_client.user_two
    foreign = client.post(
        "/api/v1/conversations",
        json={"category_id": category["id"], "note_id": created_note.json()["id"]},
    )
    assert foreign.status_code == 404


def test_conversation_detail_and_list_pagination(notes_client):
    client = notes_client.client
    category = create_category(client)
    conversation = create_conversation(client, category["id"])
    for value in ("first", "second"):
        result = client.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            json={"content": value},
        )
        assert result.status_code == 201

    detail = client.get(
        f"/api/v1/conversations/{conversation['id']}",
        params={"messages_limit": 1, "messages_offset": 1},
    )
    assert detail.status_code == 200
    assert detail.json()["messages_limit"] == 1
    assert len(detail.json()["messages"]) == 1

    listed = client.get(
        "/api/v1/conversations",
        params={"category_id": category["id"], "limit": 1},
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
