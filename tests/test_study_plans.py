from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import select

from app.models.study_plan import StudyPlanItem
from tests.test_notes import create_category, notes_client


MONDAY = "2026-11-02"


def item_payload(topic: str = "Algebra", **overrides) -> dict:
    return {
        "day_of_week": 1,
        "start_time": "09:00:00",
        "duration_minutes": 45,
        "topic": topic,
        **overrides,
    }


def create_plan(client, *, week_start: str = MONDAY, items: list[dict] | None = None) -> dict:
    response = client.post(
        "/api/v1/study-plans",
        json={"week_start": week_start, "title": "Study week", "items": items or []},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_non_owner_cannot_read_plan_or_update_delete_item(notes_client):
    client = notes_client.client
    plan = create_plan(client, items=[item_payload()])
    item_id = plan["items"][0]["id"]

    notes_client.selected_user["value"] = notes_client.user_two
    assert client.get(f"/api/v1/study-plans/{plan['id']}").status_code == 404
    assert client.patch(
        f"/api/v1/study-plan-items/{item_id}", json={"topic": "Changed"}
    ).status_code == 404
    assert client.delete(f"/api/v1/study-plan-items/{item_id}").status_code == 404


def test_non_monday_week_and_duplicate_week_are_rejected(notes_client):
    client = notes_client.client
    bad_week = client.post("/api/v1/study-plans", json={"week_start": "2026-11-03"})
    assert bad_week.status_code == 422
    create_plan(client)
    duplicate = client.post("/api/v1/study-plans", json={"week_start": MONDAY})
    assert duplicate.status_code == 409


def test_one_hundred_item_limit_applies_on_create_and_add(notes_client):
    client = notes_client.client
    too_many = client.post(
        "/api/v1/study-plans",
        json={"week_start": MONDAY, "items": [item_payload(str(i)) for i in range(101)]},
    )
    assert too_many.status_code == 422

    plan = create_plan(client, items=[item_payload(str(i)) for i in range(100)])
    over_limit = client.post(
        f"/api/v1/study-plans/{plan['id']}/items", json=item_payload("Extra")
    )
    assert over_limit.status_code == 409


def test_plan_progress_is_computed_from_items(notes_client):
    client = notes_client.client
    plan = create_plan(client, items=[item_payload("First"), item_payload("Second", day_of_week=2)])
    first_item = plan["items"][0]
    changed = client.patch(
        f"/api/v1/study-plan-items/{first_item['id']}", json={"is_completed": True}
    )
    assert changed.status_code == 200

    detail = client.get(f"/api/v1/study-plans/{plan['id']}")
    assert detail.status_code == 200
    assert detail.json()["progress"] == {
        "total_items": 2,
        "completed_items": 1,
        "progress_percent": 50.0,
    }


def test_completed_at_is_server_managed_and_cleared_when_uncompleted(notes_client):
    client = notes_client.client
    plan = create_plan(client, items=[item_payload()])
    item_id = plan["items"][0]["id"]
    forged = client.patch(
        f"/api/v1/study-plan-items/{item_id}",
        json={"is_completed": True, "completed_at": "2000-01-01T00:00:00Z"},
    )
    assert forged.status_code == 422

    completed = client.patch(
        f"/api/v1/study-plan-items/{item_id}", json={"is_completed": True}
    )
    assert completed.status_code == 200
    assert completed.json()["completed_at"] is not None
    assert completed.json()["completed_at"].startswith("202")

    reopened = client.patch(
        f"/api/v1/study-plan-items/{item_id}", json={"is_completed": False}
    )
    assert reopened.status_code == 200
    assert reopened.json()["completed_at"] is None


def test_other_users_category_or_note_cannot_be_linked(notes_client):
    client = notes_client.client
    category = create_category(client, "Foreign")
    note_response = client.post(
        "/api/v1/notes",
        data={"category_id": category["id"]},
        files={"file": ("source.txt", b"source", "text/plain")},
    )
    assert note_response.status_code == 201
    note_id = note_response.json()["id"]
    plan = create_plan(client)

    notes_client.selected_user["value"] = notes_client.user_two
    foreign_category = client.post(
        f"/api/v1/study-plans/{plan['id']}/items",
        json=item_payload("Foreign category", category_id=category["id"]),
    )
    assert foreign_category.status_code == 404  # The plan itself is foreign.

    own_plan = create_plan(client, week_start="2026-11-09")
    foreign_category = client.post(
        f"/api/v1/study-plans/{own_plan['id']}/items",
        json=item_payload("Foreign category", category_id=category["id"]),
    )
    assert foreign_category.status_code == 404
    foreign_note = client.post(
        f"/api/v1/study-plans/{own_plan['id']}/items",
        json=item_payload("Foreign note", note_id=note_id),
    )
    assert foreign_note.status_code == 404


def test_category_and_note_deletion_nulls_links_but_keeps_items(notes_client):
    client = notes_client.client
    linked_category = create_category(client, "Category link")
    note_category = create_category(client, "Note parent")
    note_response = client.post(
        "/api/v1/notes",
        data={"category_id": note_category["id"]},
        files={"file": ("source.txt", b"source", "text/plain")},
    )
    assert note_response.status_code == 201
    note = note_response.json()
    plan = create_plan(
        client,
        items=[
            item_payload("Category link", category_id=linked_category["id"]),
            item_payload("Note link", day_of_week=2, note_id=note["id"]),
        ],
    )
    category_item_id = UUID(plan["items"][0]["id"])
    note_item_id = UUID(plan["items"][1]["id"])

    assert client.delete(f"/api/v1/categories/{linked_category['id']}").status_code == 200
    assert client.delete(f"/api/v1/notes/{note['id']}").status_code == 204
    with notes_client.session_factory() as db:
        category_item = db.get(StudyPlanItem, category_item_id)
        note_item = db.get(StudyPlanItem, note_item_id)
        assert category_item is not None and category_item.category_id is None
        assert note_item is not None and note_item.note_id is None


@pytest.mark.parametrize("invalid_topic", ["", "   ", "x" * 301])
def test_topic_trimmed_and_length_validated(notes_client, invalid_topic):
    response = notes_client.client.post(
        "/api/v1/study-plans",
        json={"week_start": MONDAY, "items": [item_payload(invalid_topic)]},
    )
    assert response.status_code == 422


def test_valid_topic_is_trimmed(notes_client):
    plan = create_plan(notes_client.client, items=[item_payload("  Read algebra  ")])
    assert plan["items"][0]["topic"] == "Read algebra"
