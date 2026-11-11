from uuid import UUID

import pytest
from sqlalchemy import select

from app.models.quiz import Quiz, QuizQuestion
from tests.test_notes import create_category, notes_client


def create_note(client, category_id: str) -> dict:
    response = client.post(
        "/api/v1/notes",
        data={"category_id": category_id},
        files={"file": ("quiz-source.txt", b"source material", "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def question(text: str = "Question?") -> dict:
    return {
        "question_text": text,
        "options": [" First ", "Second"],
        "correct_index": 0,
        "explanation": "Because",
    }


def create_quiz(client, category_id: str, **overrides) -> dict:
    payload = {
        "category_id": category_id,
        "title": "Study quiz",
        "questions": [question()],
        **overrides,
    }
    response = client.post("/api/v1/quizzes", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_other_users_cannot_access_quiz(notes_client):
    client = notes_client.client
    category = create_category(client)
    quiz = create_quiz(client, category["id"])

    notes_client.selected_user["value"] = notes_client.user_two
    assert client.get(f"/api/v1/quizzes/{quiz['id']}").status_code == 404
    assert client.delete(f"/api/v1/quizzes/{quiz['id']}").status_code == 404
    assert client.get("/api/v1/quizzes", params={"category_id": category["id"]}).status_code == 404


def test_quiz_detail_never_exposes_answer_or_explanation(notes_client):
    client = notes_client.client
    category = create_category(client)
    quiz = create_quiz(client, category["id"])

    detail = client.get(f"/api/v1/quizzes/{quiz['id']}")
    assert detail.status_code == 200, detail.text
    assert "correct_index" not in detail.text
    assert "explanation" not in detail.text

    listed = client.get("/api/v1/quizzes", params={"category_id": category["id"]})
    assert listed.status_code == 200
    assert listed.json()[0]["question_count"] == 1
    assert "correct_index" not in listed.text
    assert "explanation" not in listed.text


@pytest.mark.parametrize(
    "invalid_question",
    [
        {**question(), "options": ["only one"]},
        {**question(), "options": ["a", "b", "c", "d", "e", "f", "g"]},
        {**question(), "correct_index": 2},
        {**question(), "question_text": "   "},
        {**question(), "options": ["  ", "valid"]},
        {**question(), "options": ["x" * 301, "valid"]},
        {**question(), "question_text": "q" * 1001},
        {**question(), "explanation": "e" * 1501},
    ],
)
def test_quiz_question_validation_rejects_invalid_fields(notes_client, invalid_question):
    category = create_category(notes_client.client)
    response = notes_client.client.post(
        "/api/v1/quizzes",
        json={"category_id": category["id"], "title": "Quiz", "questions": [invalid_question]},
    )
    assert response.status_code == 422


def test_more_than_30_questions_is_rejected(notes_client):
    category = create_category(notes_client.client)
    response = notes_client.client.post(
        "/api/v1/quizzes",
        json={
            "category_id": category["id"],
            "title": "Quiz",
            "questions": [question() for _ in range(31)],
        },
    )
    assert response.status_code == 422


def test_quiz_source_is_server_assigned_and_client_cannot_set_it(notes_client):
    client = notes_client.client
    category = create_category(client)
    quiz = create_quiz(client, category["id"])
    assert quiz["source"] == "manual"

    rejected = client.post(
        "/api/v1/quizzes",
        json={"category_id": category["id"], "title": "Quiz", "questions": [question()], "source": "ai"},
    )
    assert rejected.status_code == 422


def test_quiz_rejects_note_from_another_category(notes_client):
    client = notes_client.client
    first_category = create_category(client, "First")
    second_category = create_category(client, "Second")
    note = create_note(client, second_category["id"])

    response = client.post(
        "/api/v1/quizzes",
        json={
            "category_id": first_category["id"],
            "note_id": note["id"],
            "title": "Quiz",
            "questions": [question()],
        },
    )
    assert response.status_code == 404


def test_quiz_delete_cascades_to_questions(notes_client):
    client = notes_client.client
    category = create_category(client)
    quiz = create_quiz(client, category["id"])
    with notes_client.session_factory() as db:
        question_row = db.scalar(select(QuizQuestion).where(QuizQuestion.quiz_id == UUID(quiz["id"])))
        question_id = question_row.id

    assert client.delete(f"/api/v1/quizzes/{quiz['id']}").status_code == 204
    with notes_client.session_factory() as db:
        assert db.scalar(select(Quiz).where(Quiz.id == UUID(quiz["id"]))) is None
        assert db.scalar(select(QuizQuestion).where(QuizQuestion.id == question_id)) is None
