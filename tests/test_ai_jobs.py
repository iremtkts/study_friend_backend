from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from app.api.v1.endpoints import ai_jobs as ai_jobs_endpoint
from app.api.v1.endpoints import conversations as conversations_endpoint
from app.models.ai_job import AIJob
from app.models.conversation import Conversation, ConversationMessage
from app.models.membership import Membership
from app.models.note import Note
from app.models.quiz import Quiz, QuizQuestion
from app.models.summary import Summary
from app.services import ai_jobs as ai_jobs_service
from app.services import n8n_client
from app.services import note_indexing as note_indexing_service
from tests.test_notes import create_category, notes_client


def create_note(client, category_id: str, text: bytes = b"Some note text") -> dict:
    response = client.post(
        "/api/v1/notes",
        data={"category_id": category_id},
        files={"file": ("note.txt", text, "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def ai_client(notes_client, monkeypatch):
    settings = SimpleNamespace(
        n8n_base_url="http://localhost:5678",
        n8n_webhook_secret=SecretStr("unit-test-secret"),
        n8n_timeout_seconds=2,
        ai_max_input_chars=30000,
        ai_daily_limit_free=3,
        ai_daily_limit_plus=30,
        quiz_questions_min_count=1,
        quiz_questions_max_count=30,
        quiz_question_text_max_chars=1000,
        quiz_options_min_count=2,
        quiz_options_max_count=6,
        quiz_option_max_chars=300,
        quiz_explanation_max_chars=1500,
        ai_chat_daily_limit_free=50,
        ai_chat_daily_limit_plus=500,
        chat_history_limit=10,
        chat_top_k=4,
        chat_max_note_ids=200,
        ai_summary_max_cards=15,
        ai_card_target_chars=400,
        ai_output_language="tr",
        summary_max_chars=1000,
        index_max_input_chars=20000,
    )
    monkeypatch.setattr(ai_jobs_endpoint, "get_settings", lambda: settings)
    monkeypatch.setattr(conversations_endpoint, "get_settings", lambda: settings)
    monkeypatch.setattr(n8n_client, "get_settings", lambda: settings)
    monkeypatch.setattr(
        n8n_client,
        "is_configured",
        lambda: bool(
            n8n_client.get_settings().n8n_base_url
            and n8n_client.get_settings().n8n_webhook_secret.get_secret_value()
        ),
    )
    monkeypatch.setattr(ai_jobs_service, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_jobs_service, "SessionLocal", notes_client.session_factory)
    monkeypatch.setattr(note_indexing_service, "get_settings", lambda: settings)
    monkeypatch.setattr(note_indexing_service, "SessionLocal", notes_client.session_factory)

    original_post_webhook = n8n_client.post_webhook
    state = {
        "calls": [],
        "cards": ["AI card one", "AI card two"],
        "model_name": "test-model",
        "response_override": None,
        "index_response_override": None,
        "exception": None,
        "index_exception": None,
        "chat_response_override": None,
        "chat_answer": "Chat answer",
        "chat_sources": [],
        "chat_model_name": "chat-test-model",
        "chat_exception": None,
        "quiz_response_override": None,
        "quiz_model_name": "quiz-test-model",
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        state["calls"].append((request, body))
        if request.url.path.endswith("/generate-quiz"):
            result = state["quiz_response_override"]
            if result is None:
                result = {
                    "job_id": body["job_id"],
                    "questions": [
                        {
                            "question_text": f"Question {index}?",
                            "options": ["First", "Second"],
                            "correct_index": 0,
                            "explanation": "Reason",
                        }
                        for index in range(body["question_count"])
                    ],
                    "model_name": state["quiz_model_name"],
                }
            elif result.get("job_id") == "$request":
                result = {**result, "job_id": body["job_id"]}
            return httpx.Response(200, json=result)
        if request.url.path.endswith("/chat"):
            if state["chat_exception"] is not None:
                raise state["chat_exception"]
            result = state["chat_response_override"]
            if result is None:
                result = {
                    "job_id": body["job_id"],
                    "answer": state["chat_answer"],
                    "sources": list(state["chat_sources"]),
                    "model": state["chat_model_name"],
                }
            elif result.get("job_id") == "$request":
                result = {**result, "job_id": body["job_id"]}
            return httpx.Response(200, json=result)
        if request.url.path.endswith("/index-note"):
            if state["index_exception"] is not None:
                raise state["index_exception"]
            index_result = state["index_response_override"]
            if index_result is None:
                index_result = {"job_id": body["job_id"], "chunk_count": 3}
            elif index_result.get("job_id") == "$request":
                index_result = {**index_result, "job_id": body["job_id"]}
            return httpx.Response(200, json=index_result)
        if state["exception"] is not None:
            raise state["exception"]
        result = state["response_override"]
        if result is None:
            result = {
                "job_id": body["job_id"],
                "cards": list(state["cards"]),
                "model_name": state["model_name"],
            }
        elif result.get("job_id") == "$request":
            result = {**result, "job_id": body["job_id"]}
        return httpx.Response(200, json=result)

    def mocked_post(path: str, payload: dict) -> dict:
        return original_post_webhook(path, payload, transport=httpx.MockTransport(mock_handler))

    monkeypatch.setattr(n8n_client, "post_webhook", mocked_post)
    return SimpleNamespace(**notes_client.__dict__, settings=settings, n8n=state)


def create_note_for_ai(ai_client, text: bytes = b"Some note text") -> tuple[dict, dict]:
    category = create_category(ai_client.client)
    note = create_note(ai_client.client, category["id"], text)
    return category, note


def create_conversation(client, category_id: str, note_id: str | None = None) -> dict:
    response = client.post(
        "/api/v1/conversations",
        json={"category_id": category_id, "note_id": note_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def get_job(client, job_id: str) -> dict:
    response = client.get(f"/api/v1/ai-jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_success_replaces_only_ai_cards_and_sends_minimal_payload(ai_client):
    client = ai_client.client
    category, note = create_note_for_ai(ai_client)
    manual = client.post(
        f"/api/v1/notes/{note['id']}/summaries", json={"content": "Keep this manual card"}
    )
    assert manual.status_code == 201

    first = client.post(f"/api/v1/notes/{note['id']}/summaries/generate")
    assert first.status_code == 202, first.text
    first_job = get_job(client, first.json()["job_id"])
    assert first_job["status"] == "done"
    assert first_job["result_count"] == 2
    with ai_client.session_factory() as db:
        stored_job = db.scalar(select(AIJob).where(AIJob.id == UUID(first.json()["job_id"])))
        assert stored_job.model_name == "test-model"

    request, body = next(
        call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/summarize-note")
    )
    assert str(request.url) == "http://localhost:5678/webhook/summarize-note"
    assert request.headers["X-Webhook-Secret"] == "unit-test-secret"
    assert set(body) == {"job_id", "text", "language", "max_cards", "target_chars_per_card"}
    assert body["text"] == "Some note text"
    assert "user_id" not in body and "email" not in body

    ai_client.n8n["cards"] = ["Replacement card"]
    second = client.post(f"/api/v1/notes/{note['id']}/summaries/generate")
    assert second.status_code == 202
    assert get_job(client, second.json()["job_id"])["status"] == "done"

    with ai_client.session_factory() as db:
        summaries = db.scalars(
            select(Summary).where(Summary.user_id == ai_client.user_one.id, Summary.note_id == UUID(note["id"]))
        ).all()
    assert [(summary.source, summary.content) for summary in summaries] == [
        ("manual", "Keep this manual card"),
        ("ai", "Replacement card"),
    ]


@pytest.mark.parametrize(
    ("response_override", "expected_code"),
    [
        ({"job_id": "wrong-id", "cards": ["ok"]}, "n8n_bad_response"),
        ({"job_id": "$request", "cards": []}, "n8n_bad_response"),
        ({"job_id": "$request", "cards": ["   "]}, "n8n_bad_response"),
    ],
)
def test_invalid_n8n_results_fail_without_storing_cards(ai_client, response_override, expected_code):
    client = ai_client.client
    _, note = create_note_for_ai(ai_client)
    ai_client.n8n["response_override"] = dict(response_override)
    started = client.post(f"/api/v1/notes/{note['id']}/summaries/generate")
    job = get_job(client, started.json()["job_id"])
    assert job["status"] == "failed"
    assert job["error_code"] == expected_code
    with ai_client.session_factory() as db:
        assert db.scalar(
            select(func.count(Summary.id)).where(
                Summary.user_id == ai_client.user_one.id,
                Summary.note_id == UUID(note["id"]),
                Summary.source == "ai",
            )
        ) == 0


@pytest.mark.parametrize(
    ("exception", "error_code"),
    [
        (httpx.ReadTimeout("sensitive timeout text"), "n8n_timeout"),
        (httpx.ConnectError("sensitive connection text"), "n8n_unreachable"),
    ],
)
def test_timeout_and_unreachable_fail_without_raw_error(ai_client, exception, error_code):
    _, note = create_note_for_ai(ai_client)
    ai_client.n8n["exception"] = exception
    started = ai_client.client.post(f"/api/v1/notes/{note['id']}/summaries/generate")
    job = get_job(ai_client.client, started.json()["job_id"])
    assert job["status"] == "failed"
    assert job["error_code"] == error_code
    assert "sensitive" not in str(job)


def test_free_quota_plus_quota_and_failed_jobs_not_counted(ai_client):
    client = ai_client.client
    ai_client.settings.ai_daily_limit_free = 1
    category, note = create_note_for_ai(ai_client)
    with ai_client.session_factory() as db:
        db.add(
            AIJob(
                user_id=ai_client.user_one.id,
                job_type="summarize_note",
                note_id=UUID(note["id"]),
                status="done",
            )
        )
        db.commit()
    assert client.post(f"/api/v1/notes/{note['id']}/summaries/generate").status_code == 429

    # Failed jobs are excluded from the quota count.
    with ai_client.session_factory() as db:
        existing = db.scalar(
            select(AIJob).where(AIJob.job_type == "summarize_note", AIJob.status == "done")
        )
        existing.status = "failed"
        existing.error_code = "n8n_error"
        db.commit()
    assert client.post(f"/api/v1/notes/{note['id']}/summaries/generate").status_code == 202

    # A fresh plus account gets the separate plus quota.
    ai_client.settings.ai_daily_limit_plus = 1
    ai_client.selected_user["value"] = ai_client.user_two
    category_plus = create_category(client, "Plus course")
    note_plus = create_note(client, category_plus["id"])
    with ai_client.session_factory() as db:
        db.add(
            Membership(
                user_id=ai_client.user_two.id,
                plan="plus",
                status="active",
                started_at=datetime.now(timezone.utc),
                expires_at=None,
            )
        )
        db.commit()
        db.add(
            AIJob(
                user_id=ai_client.user_two.id,
                job_type="summarize_note",
                note_id=UUID(note_plus["id"]),
                status="done",
            )
        )
        db.commit()
    assert client.post(f"/api/v1/notes/{note_plus['id']}/summaries/generate").status_code == 429


def test_generate_quiz_success_persists_result_and_uses_minimal_payload(ai_client):
    category, note = create_note_for_ai(ai_client)
    with ai_client.session_factory() as db:
        stored_note = db.scalar(select(Note).where(Note.id == UUID(note["id"])))
        stored_note.title = "Biology"
        db.commit()

    response = ai_client.client.post(
        f"/api/v1/notes/{note['id']}/quizzes/generate",
        json={"question_count": 3},
    )
    assert response.status_code == 202, response.text
    job = get_job(ai_client.client, response.json()["job_id"])
    assert job["status"] == "done"
    assert job["result_count"] == 3
    assert job["result_id"] is not None

    request, payload = next(
        call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/generate-quiz")
    )
    assert str(request.url) == "http://localhost:5678/webhook/generate-quiz"
    assert set(payload) == {"job_id", "text", "question_count", "language"}
    assert payload["text"] == "Some note text"
    assert payload["question_count"] == 3
    assert payload["language"] == "tr"
    assert "user_id" not in payload and "email" not in payload

    with ai_client.session_factory() as db:
        quiz = db.scalar(select(Quiz).where(Quiz.id == UUID(job["result_id"])))
        questions = db.scalars(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_id == quiz.id)
            .order_by(QuizQuestion.position)
        ).all()
        stored_job = db.scalar(select(AIJob).where(AIJob.id == UUID(job["job_id"])))
    assert quiz.user_id == ai_client.user_one.id
    assert quiz.category_id == UUID(category["id"])
    assert quiz.note_id == UUID(note["id"])
    assert quiz.source == "ai"
    assert quiz.title == "Biology Testi"
    assert len(questions) == 3
    assert all(question.correct_index == 0 and question.explanation == "Reason" for question in questions)
    assert stored_job.model_name == "quiz-test-model"
    assert stored_job.result_id == quiz.id

    detail = ai_client.client.get(f"/api/v1/quizzes/{quiz.id}")
    assert detail.status_code == 200
    assert "correct_index" not in detail.text
    assert "explanation" not in detail.text


def test_generate_quiz_discards_invalid_questions(ai_client):
    _, note = create_note_for_ai(ai_client)
    valid = {
        "question_text": "Valid question?",
        "options": ["A", "B"],
        "correct_index": 1,
        "explanation": "Valid explanation",
    }
    ai_client.n8n["quiz_response_override"] = {
        "job_id": "$request",
        "questions": [
            valid,
            {**valid, "options": ["Only one"]},
            {**valid, "correct_index": 2},
        ],
        "model_name": "filtered-model",
    }

    response = ai_client.client.post(
        f"/api/v1/notes/{note['id']}/quizzes/generate",
        json={"question_count": 3},
    )
    job = get_job(ai_client.client, response.json()["job_id"])
    assert job["status"] == "done"
    assert job["result_count"] == 1
    with ai_client.session_factory() as db:
        questions = db.scalars(
            select(QuizQuestion).where(QuizQuestion.quiz_id == UUID(job["result_id"]))
        ).all()
    assert len(questions) == 1
    assert questions[0].question_text == "Valid question?"


@pytest.mark.parametrize(
    "response_override",
    [
        {"job_id": "wrong", "questions": [{"question_text": "q"}]},
        {"job_id": "$request", "questions": []},
        {
            "job_id": "$request",
            "questions": [{"question_text": "Invalid", "options": ["one"], "correct_index": 0}],
        },
    ],
)
def test_generate_quiz_invalid_results_fail_without_quiz(ai_client, response_override):
    _, note = create_note_for_ai(ai_client)
    ai_client.n8n["quiz_response_override"] = response_override

    response = ai_client.client.post(
        f"/api/v1/notes/{note['id']}/quizzes/generate",
        json={"question_count": 3},
    )
    assert response.status_code == 202
    job = get_job(ai_client.client, response.json()["job_id"])
    assert job["status"] == "failed"
    assert job["error_code"] == "n8n_bad_response"
    assert job["result_id"] is None
    with ai_client.session_factory() as db:
        assert db.scalar(select(func.count(Quiz.id))) == 0


def test_generate_quiz_quota_is_shared_with_summaries_and_failed_jobs_are_ignored(ai_client):
    ai_client.settings.ai_daily_limit_free = 1
    _, note = create_note_for_ai(ai_client)
    with ai_client.session_factory() as db:
        db.add(
            AIJob(
                user_id=ai_client.user_one.id,
                job_type="summarize_note",
                note_id=UUID(note["id"]),
                status="done",
            )
        )
        db.commit()
    over_quota = ai_client.client.post(
        f"/api/v1/notes/{note['id']}/quizzes/generate",
        json={},
    )
    assert over_quota.status_code == 429

    with ai_client.session_factory() as db:
        summary_job = db.scalar(
            select(AIJob).where(
                AIJob.user_id == ai_client.user_one.id,
                AIJob.job_type == "summarize_note",
            )
        )
        summary_job.status = "failed"
        db.commit()
    accepted = ai_client.client.post(
        f"/api/v1/notes/{note['id']}/quizzes/generate",
        json={},
    )
    assert accepted.status_code == 202
    accepted_job = get_job(ai_client.client, accepted.json()["job_id"])
    assert accepted_job["result_count"] == 5
    assert len(
        [call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/generate-quiz")]
    ) == 1
    summary_category = create_category(ai_client.client, "Quota summary course")
    summary_note = create_note(ai_client.client, summary_category["id"])
    assert (
        ai_client.client.post(
            f"/api/v1/notes/{summary_note['id']}/summaries/generate"
        ).status_code
        == 429
    )


def test_generate_quiz_existing_active_job_is_returned(ai_client):
    _, note = create_note_for_ai(ai_client)
    existing = AIJob(
        user_id=ai_client.user_one.id,
        job_type="generate_quiz",
        note_id=UUID(note["id"]),
        status="running",
    )
    with ai_client.session_factory() as db:
        db.add(existing)
        db.commit()
        db.refresh(existing)
        existing_id = existing.id
    before = len(ai_client.n8n["calls"])

    response = ai_client.client.post(
        f"/api/v1/notes/{note['id']}/quizzes/generate",
        json={},
    )
    assert response.status_code == 202
    assert response.json() == {"job_id": str(existing_id), "status": "running"}
    assert len(ai_client.n8n["calls"]) == before


def test_generate_quiz_rejects_foreign_note_and_missing_n8n(ai_client, monkeypatch):
    _, note = create_note_for_ai(ai_client)
    ai_client.selected_user["value"] = ai_client.user_two
    foreign = ai_client.client.post(
        f"/api/v1/notes/{note['id']}/quizzes/generate",
        json={},
    )
    assert foreign.status_code == 404

    ai_client.selected_user["value"] = ai_client.user_one
    monkeypatch.setattr(n8n_client, "is_configured", lambda: False)
    missing = ai_client.client.post(
        f"/api/v1/notes/{note['id']}/quizzes/generate",
        json={},
    )
    assert missing.status_code == 503


@pytest.mark.parametrize("question_count", [2, 11])
def test_generate_quiz_rejects_out_of_range_question_count(ai_client, question_count):
    _, note = create_note_for_ai(ai_client)
    response = ai_client.client.post(
        f"/api/v1/notes/{note['id']}/quizzes/generate",
        json={"question_count": question_count},
    )
    assert response.status_code == 422


def test_duplicate_active_job_is_returned_without_scheduling_another(ai_client, monkeypatch):
    _, note = create_note_for_ai(ai_client)
    scheduled = []
    monkeypatch.setattr(ai_jobs_endpoint, "run_ai_summary_job", lambda *args: scheduled.append(args))
    first = ai_client.client.post(f"/api/v1/notes/{note['id']}/summaries/generate")
    second = ai_client.client.post(f"/api/v1/notes/{note['id']}/summaries/generate")
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert len(scheduled) == 1


def test_foreign_note_and_job_are_hidden(ai_client):
    _, note = create_note_for_ai(ai_client)
    created = ai_client.client.post(f"/api/v1/notes/{note['id']}/summaries/generate")
    job_id = created.json()["job_id"]
    ai_client.selected_user["value"] = ai_client.user_two
    assert ai_client.client.post(f"/api/v1/notes/{note['id']}/summaries/generate").status_code == 404
    assert ai_client.client.get(f"/api/v1/ai-jobs/{job_id}").status_code == 404


def test_missing_n8n_configuration_returns_503_without_creating_job(ai_client, monkeypatch):
    _, note = create_note_for_ai(ai_client)
    monkeypatch.setattr(
        n8n_client,
        "get_settings",
        lambda: SimpleNamespace(n8n_base_url="", n8n_webhook_secret=SecretStr("")),
    )
    response = ai_client.client.post(f"/api/v1/notes/{note['id']}/summaries/generate")
    assert response.status_code == 503
    with ai_client.session_factory() as db:
        assert db.scalar(
            select(func.count(AIJob.id)).where(AIJob.job_type == "summarize_note")
        ) == 0


def test_long_note_text_is_sampled_for_summaries_only(ai_client):
    original_text = "FIRST\n\nSECOND\n\nTHIRD\n\nLAST"
    _, note = create_note_for_ai(ai_client, original_text.encode())
    ai_client.settings.ai_max_input_chars = 24
    response = ai_client.client.post(f"/api/v1/notes/{note['id']}/summaries/generate")
    assert response.status_code == 202
    _, payload = next(
        call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/summarize-note")
    )
    sampled = payload["text"]
    assert len(sampled) <= ai_client.settings.ai_max_input_chars
    assert sampled.startswith("FIRS")
    assert sampled.endswith("LAST")
    assert sampled.index("FIRST") < sampled.index("THIRD") < sampled.index("LAST")
    with ai_client.session_factory() as db:
        stored_note = db.scalar(select(Note).where(Note.id == UUID(note["id"])))
        assert stored_note.extracted_text == original_text


def test_long_note_text_is_sampled_for_quiz_generation_only(ai_client):
    original_text = "FIRST\n\nSECOND\n\nTHIRD\n\nLAST"
    _, note = create_note_for_ai(ai_client, original_text.encode())
    ai_client.settings.ai_max_input_chars = 24
    response = ai_client.client.post(
        f"/api/v1/notes/{note['id']}/quizzes/generate",
        json={"question_count": 3},
    )
    assert response.status_code == 202
    _, payload = next(
        call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/generate-quiz")
    )
    sampled = payload["text"]
    assert len(sampled) <= ai_client.settings.ai_max_input_chars
    assert sampled.startswith("FIRS")
    assert sampled.endswith("LAST")
    assert sampled.index("FIRST") < sampled.index("THIRD") < sampled.index("LAST")
    with ai_client.session_factory() as db:
        stored_note = db.scalar(select(Note).where(Note.id == UUID(note["id"])))
        assert stored_note.extracted_text == original_text


@pytest.mark.parametrize("endpoint", ["summaries/generate", "quizzes/generate"])
def test_blank_note_text_remains_unprocessable_for_ai_jobs(ai_client, endpoint):
    _, note = create_note_for_ai(ai_client, b"   ")
    response = ai_client.client.post(f"/api/v1/notes/{note['id']}/{endpoint}", json={})
    assert response.status_code == 422


@pytest.mark.parametrize("status,age_field", [("pending", "created_at"), ("running", "started_at")])
def test_stale_jobs_are_marked_failed(ai_client, status, age_field):
    _, note = create_note_for_ai(ai_client)
    now = datetime.now(timezone.utc)
    stale_value = now - timedelta(seconds=61 if status == "pending" else 123)
    job = AIJob(
        user_id=ai_client.user_one.id,
        job_type="summarize_note",
        note_id=UUID(note["id"]),
        status=status,
        created_at=stale_value if age_field == "created_at" else now,
        started_at=stale_value if age_field == "started_at" else None,
    )
    with ai_client.session_factory() as db:
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id
    result = get_job(ai_client.client, str(job_id))
    assert result["status"] == "failed"
    assert result["error_code"] == "timeout"
    assert result["finished_at"] is not None


def _index_jobs(db, user_id, note_id):
    return db.scalars(
        select(AIJob).where(
            AIJob.user_id == user_id,
            AIJob.note_id == note_id,
            AIJob.job_type == "index_note",
        )
    ).all()


def test_note_upload_automatically_indexes_and_returns_index_status(ai_client):
    category = create_category(ai_client.client)
    response = ai_client.client.post(
        "/api/v1/notes",
        data={"category_id": category["id"]},
        files={"file": ("indexed.txt", b"Text to index", "text/plain")},
    )
    assert response.status_code == 201, response.text
    note_id = UUID(response.json()["id"])
    assert response.json()["index_status"] == "pending"
    index_calls = [call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/index-note")]
    assert len(index_calls) == 1
    request, payload = index_calls[0]
    assert set(payload) == {"job_id", "text", "user_id", "note_id", "category_id"}
    assert payload["text"] == "Text to index"
    assert payload["user_id"] == str(ai_client.user_one.id)
    assert payload["note_id"] == str(note_id)
    assert payload["category_id"] == category["id"]
    assert "email" not in payload and "name" not in payload
    assert request.url.path.endswith("/index-note")
    with ai_client.session_factory() as db:
        note = db.get(Note, note_id)
        jobs = _index_jobs(db, ai_client.user_one.id, note_id)
        assert note.index_status == "indexed"
        assert note.indexed_at is not None
        assert note.chunk_count == 3
        assert len(jobs) == 1 and jobs[0].status == "done"


def test_index_n8n_error_marks_note_and_job_failed(ai_client):
    ai_client.n8n["index_exception"] = httpx.ConnectError("private connection details")
    category = create_category(ai_client.client)
    response = ai_client.client.post(
        "/api/v1/notes",
        data={"category_id": category["id"]},
        files={"file": ("failed.txt", b"Text to index", "text/plain")},
    )
    assert response.status_code == 201
    note_id = UUID(response.json()["id"])
    with ai_client.session_factory() as db:
        note = db.get(Note, note_id)
        jobs = _index_jobs(db, ai_client.user_one.id, note_id)
        assert note.index_status == "failed"
        assert len(jobs) == 1
        assert jobs[0].status == "failed"
        assert jobs[0].error_code == "n8n_unreachable"


def test_too_large_index_input_is_failed_without_n8n_call(ai_client):
    ai_client.settings.index_max_input_chars = 4
    category = create_category(ai_client.client)
    response = ai_client.client.post(
        "/api/v1/notes",
        data={"category_id": category["id"]},
        files={"file": ("large.txt", b"five!", "text/plain")},
    )
    assert response.status_code == 201
    note_id = UUID(response.json()["id"])
    assert not [call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/index-note")]
    with ai_client.session_factory() as db:
        note = db.get(Note, note_id)
        job = _index_jobs(db, ai_client.user_one.id, note_id)[0]
        assert note.index_status == "failed"
        assert job.status == "failed" and job.error_code == "too_large"


@pytest.mark.parametrize(
    "response_override",
    [
        {"job_id": "wrong", "chunk_count": 2},
        {"job_id": "$request", "chunk_count": 0},
        {"job_id": "$request", "chunk_count": 2001},
        {"job_id": "$request", "chunk_count": True},
    ],
)
def test_invalid_index_response_fails_job_and_note(ai_client, response_override):
    ai_client.n8n["index_response_override"] = response_override
    category = create_category(ai_client.client)
    response = ai_client.client.post(
        "/api/v1/notes",
        data={"category_id": category["id"]},
        files={"file": ("bad-response.txt", b"Text", "text/plain")},
    )
    assert response.status_code == 201
    note_id = UUID(response.json()["id"])
    with ai_client.session_factory() as db:
        note = db.get(Note, note_id)
        job = _index_jobs(db, ai_client.user_one.id, note_id)[0]
        assert note.index_status == "failed"
        assert job.status == "failed" and job.error_code == "n8n_bad_response"


def test_retry_index_job_failed_indexed_and_foreign_note(ai_client):
    ai_client.n8n["index_exception"] = httpx.ConnectError("unreachable")
    category = create_category(ai_client.client)
    response = ai_client.client.post(
        "/api/v1/notes",
        data={"category_id": category["id"]},
        files={"file": ("retry.txt", b"retry me", "text/plain")},
    )
    note_id = response.json()["id"]
    ai_client.n8n["index_exception"] = None
    retry = ai_client.client.post(f"/api/v1/notes/{note_id}/index")
    assert retry.status_code == 202
    assert retry.json()["status"] == "pending"
    with ai_client.session_factory() as db:
        note = db.get(Note, UUID(note_id))
        jobs = _index_jobs(db, ai_client.user_one.id, UUID(note_id))
        assert note.index_status == "indexed"
        assert len(jobs) == 2 and jobs[1].status == "done"
    assert ai_client.client.post(f"/api/v1/notes/{note_id}/index").status_code == 409

    ai_client.selected_user["value"] = ai_client.user_two
    assert ai_client.client.post(f"/api/v1/notes/{note_id}/index").status_code == 404


def test_active_index_job_is_returned_without_creating_another(ai_client):
    category = create_category(ai_client.client)
    response = ai_client.client.post(
        "/api/v1/notes",
        data={"category_id": category["id"]},
        files={"file": ("active.txt", b"active job", "text/plain")},
    )
    note_id = UUID(response.json()["id"])
    with ai_client.session_factory() as db:
        note = db.get(Note, note_id)
        note.index_status = "pending"
        existing = AIJob(
            user_id=ai_client.user_one.id,
            job_type="index_note",
            note_id=note_id,
            status="pending",
        )
        db.add(existing)
        db.commit()
        db.refresh(existing)
        existing_id = existing.id
    before = len([call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/index-note")])
    result = ai_client.client.post(f"/api/v1/notes/{note_id}/index")
    after = len([call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/index-note")])
    assert result.status_code == 202
    assert result.json() == {"job_id": str(existing_id), "status": "pending"}
    assert before == after
    with ai_client.session_factory() as db:
        assert len(_index_jobs(db, ai_client.user_one.id, note_id)) == 2


def test_index_jobs_do_not_consume_summary_quota(ai_client):
    ai_client.settings.ai_daily_limit_free = 1
    category, note = create_note_for_ai(ai_client)
    with ai_client.session_factory() as db:
        assert len(_index_jobs(db, ai_client.user_one.id, UUID(note["id"]))) == 1
    response = ai_client.client.post(f"/api/v1/notes/{note['id']}/summaries/generate")
    assert response.status_code == 202, response.text
    with ai_client.session_factory() as db:
        summary_jobs = db.scalars(
            select(AIJob).where(
                AIJob.user_id == ai_client.user_one.id,
                AIJob.job_type == "summarize_note",
            )
        ).all()
        assert len(summary_jobs) == 1


def test_chat_success_persists_assistant_and_filters_sources(ai_client):
    category, note = create_note_for_ai(ai_client)
    conversation = create_conversation(ai_client.client, category["id"])
    ai_client.n8n["chat_sources"] = [note["id"], str(uuid4())]

    response = ai_client.client.post(
        f"/api/v1/conversations/{conversation['id']}/chat",
        json={"content": "  What does this note say?  "},
    )
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["status"] == "pending"

    job = get_job(ai_client.client, result["job_id"])
    assert job["status"] == "done"
    assert job["conversation_id"] == conversation["id"]
    request, payload = next(
        call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/chat")
    )
    assert str(request.url) == "http://localhost:5678/webhook/chat"
    assert set(payload) == {
        "job_id",
        "question",
        "history",
        "user_id",
        "category_id",
        "note_ids",
        "language",
        "top_k",
    }
    assert payload["question"] == "What does this note say?"
    assert payload["user_id"] == str(ai_client.user_one.id)
    assert payload["category_id"] == category["id"]
    assert payload["note_ids"] == [note["id"]]
    assert payload["language"] == "tr"
    assert payload["top_k"] == 4
    assert "email" not in payload and "name" not in payload

    with ai_client.session_factory() as db:
        messages = db.scalars(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == UUID(conversation["id"]))
            .order_by(ConversationMessage.created_at, ConversationMessage.id)
        ).all()
        stored_job = db.scalar(select(AIJob).where(AIJob.id == UUID(result["job_id"])))
        stored_conversation = db.scalar(
            select(Conversation).where(Conversation.id == UUID(conversation["id"]))
        )
    assert [(item.role, item.content) for item in messages] == [
        ("user", "What does this note say?"),
        ("assistant", "Chat answer"),
    ]
    assert messages[-1].model_name == "chat-test-model"
    assert messages[-1].sources == [note["id"]]
    assert stored_job.result_count == 1
    assert stored_job.conversation_id == UUID(conversation["id"])
    assert stored_conversation.updated_at is not None

    detail = ai_client.client.get(f"/api/v1/conversations/{conversation['id']}")
    assert detail.status_code == 200
    assistant_response = next(
        item for item in detail.json()["messages"] if item["role"] == "assistant"
    )
    assert assistant_response["model_name"] == "chat-test-model"
    assert assistant_response["sources"] == [note["id"]]


def test_chat_note_ids_are_limited_to_owners_indexed_notes_in_category(ai_client):
    category, own_note = create_note_for_ai(ai_client)
    own_note_two = create_note(ai_client.client, category["id"])
    other_category = create_category(ai_client.client, "Other category")
    other_category_note = create_note(ai_client.client, other_category["id"])

    ai_client.selected_user["value"] = ai_client.user_two
    foreign_category = create_category(ai_client.client, "Foreign category")
    foreign_note = create_note(ai_client.client, foreign_category["id"])
    ai_client.selected_user["value"] = ai_client.user_one

    conversation = create_conversation(ai_client.client, category["id"])
    ai_client.settings.chat_max_note_ids = 1
    response = ai_client.client.post(
        f"/api/v1/conversations/{conversation['id']}/chat",
        json={"content": "Question"},
    )
    assert response.status_code == 202, response.text
    _, payload = next(
        call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/chat")
    )
    assert len(payload["note_ids"]) == 1
    assert payload["note_ids"][0] in {own_note["id"], own_note_two["id"]}
    assert other_category_note["id"] not in payload["note_ids"]
    assert foreign_note["id"] not in payload["note_ids"]


def test_chat_conversation_note_id_restricts_context(ai_client):
    category, pinned_note = create_note_for_ai(ai_client)
    another_note = create_note(ai_client.client, category["id"])
    conversation = create_conversation(ai_client.client, category["id"], pinned_note["id"])

    response = ai_client.client.post(
        f"/api/v1/conversations/{conversation['id']}/chat",
        json={"content": "Question"},
    )
    assert response.status_code == 202
    _, payload = next(
        call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/chat")
    )
    assert payload["note_ids"] == [pinned_note["id"]]
    assert another_note["id"] not in payload["note_ids"]

    with ai_client.session_factory() as db:
        note = db.scalar(select(Note).where(Note.id == UUID(pinned_note["id"])))
        note.index_status = "pending"
        db.commit()
    unindexed_conversation = create_conversation(
        ai_client.client, category["id"], pinned_note["id"]
    )
    unindexed_response = ai_client.client.post(
        f"/api/v1/conversations/{unindexed_conversation['id']}/chat",
        json={"content": "Question"},
    )
    assert unindexed_response.status_code == 202
    chat_calls = [
        call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/chat")
    ]
    assert chat_calls[-1][1]["note_ids"] == []


def test_chat_history_is_chronological_and_excludes_current_message(ai_client):
    category = create_category(ai_client.client)
    conversation = create_conversation(ai_client.client, category["id"])
    now = datetime.now(timezone.utc)
    with ai_client.session_factory() as db:
        db.add_all(
            [
                ConversationMessage(
                    conversation_id=UUID(conversation["id"]),
                    role="user",
                    content="Earlier question",
                    created_at=now - timedelta(minutes=2),
                ),
                ConversationMessage(
                    conversation_id=UUID(conversation["id"]),
                    role="assistant",
                    content="Earlier answer",
                    created_at=now - timedelta(minutes=1),
                ),
            ]
        )
        db.commit()

    response = ai_client.client.post(
        f"/api/v1/conversations/{conversation['id']}/chat",
        json={"content": "Current question"},
    )
    assert response.status_code == 202
    _, payload = next(
        call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/chat")
    )
    assert payload["history"] == [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
    ]
    assert all(item["content"] != "Current question" for item in payload["history"])


@pytest.mark.parametrize(
    "response_override",
    [
        {"job_id": "wrong-id", "answer": "Valid answer"},
        {"job_id": "$request", "answer": "   "},
        {"job_id": "$request", "answer": "x" * 4001},
        {"job_id": "$request", "answer": "Valid answer", "sources": ["ok", 1]},
    ],
)
def test_invalid_chat_response_fails_without_assistant_message(ai_client, response_override):
    category = create_category(ai_client.client)
    conversation = create_conversation(ai_client.client, category["id"])
    ai_client.n8n["chat_response_override"] = response_override

    response = ai_client.client.post(
        f"/api/v1/conversations/{conversation['id']}/chat",
        json={"content": "Question"},
    )
    assert response.status_code == 202
    job = get_job(ai_client.client, response.json()["job_id"])
    assert job["status"] == "failed"
    assert job["error_code"] == "n8n_bad_response"
    with ai_client.session_factory() as db:
        messages = db.scalars(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == UUID(conversation["id"]),
                ConversationMessage.role == "assistant",
            )
        ).all()
    assert messages == []


def test_chat_active_job_returns_409_without_adding_user_message(ai_client):
    category = create_category(ai_client.client)
    conversation = create_conversation(ai_client.client, category["id"])
    with ai_client.session_factory() as db:
        db.add(
            AIJob(
                user_id=ai_client.user_one.id,
                job_type="chat_message",
                conversation_id=UUID(conversation["id"]),
                status="pending",
            )
        )
        db.commit()

    response = ai_client.client.post(
        f"/api/v1/conversations/{conversation['id']}/chat",
        json={"content": "Question"},
    )
    assert response.status_code == 409
    with ai_client.session_factory() as db:
        assert db.scalar(
            select(func.count(ConversationMessage.id)).where(
                ConversationMessage.conversation_id == UUID(conversation["id"])
            )
        ) == 0


def test_chat_quota_is_separate_for_free_and_plus_users(ai_client):
    ai_client.settings.ai_chat_daily_limit_free = 1
    ai_client.settings.ai_chat_daily_limit_plus = 1
    category = create_category(ai_client.client)
    conversation = create_conversation(ai_client.client, category["id"])
    with ai_client.session_factory() as db:
        db.add(
            AIJob(
                user_id=ai_client.user_one.id,
                job_type="summarize_note",
                status="done",
            )
        )
        db.commit()
    accepted = ai_client.client.post(
        f"/api/v1/conversations/{conversation['id']}/chat",
        json={"content": "Counts separately"},
    )
    assert accepted.status_code == 202
    second_conversation = create_conversation(ai_client.client, category["id"])
    over_free_quota = ai_client.client.post(
        f"/api/v1/conversations/{second_conversation['id']}/chat",
        json={"content": "Over free quota"},
    )
    assert over_free_quota.status_code == 429

    ai_client.settings.ai_daily_limit_free = 2
    summary_category = create_category(ai_client.client, "Summary course")
    summary_note = create_note(ai_client.client, summary_category["id"])
    summary_response = ai_client.client.post(
        f"/api/v1/notes/{summary_note['id']}/summaries/generate"
    )
    assert summary_response.status_code == 202

    ai_client.selected_user["value"] = ai_client.user_two
    plus_category = create_category(ai_client.client, "Plus course")
    plus_conversation = create_conversation(ai_client.client, plus_category["id"])
    with ai_client.session_factory() as db:
        db.add(
            Membership(
                user_id=ai_client.user_two.id,
                plan="plus",
                status="active",
                started_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            AIJob(
                user_id=ai_client.user_two.id,
                job_type="chat_message",
                conversation_id=UUID(plus_conversation["id"]),
                status="done",
            )
        )
        db.commit()
    rejected = ai_client.client.post(
        f"/api/v1/conversations/{plus_conversation['id']}/chat",
        json={"content": "Over quota"},
    )
    assert rejected.status_code == 429


def test_chat_hides_foreign_conversation_and_missing_n8n_configuration(ai_client, monkeypatch):
    category = create_category(ai_client.client)
    conversation = create_conversation(ai_client.client, category["id"])
    ai_client.selected_user["value"] = ai_client.user_two
    foreign = ai_client.client.post(
        f"/api/v1/conversations/{conversation['id']}/chat",
        json={"content": "Question"},
    )
    assert foreign.status_code == 404

    ai_client.selected_user["value"] = ai_client.user_one
    monkeypatch.setattr(n8n_client, "is_configured", lambda: False)
    missing = ai_client.client.post(
        f"/api/v1/conversations/{conversation['id']}/chat",
        json={"content": "Question"},
    )
    assert missing.status_code == 503
    with ai_client.session_factory() as db:
        assert db.scalar(
            select(func.count(AIJob.id)).where(
                AIJob.user_id == ai_client.user_one.id,
                AIJob.job_type == "chat_message",
            )
        ) == 0
        assert db.scalar(
            select(func.count(ConversationMessage.id)).where(
                ConversationMessage.conversation_id == UUID(conversation["id"])
            )
        ) == 0


def test_chat_job_is_hidden_from_other_user(ai_client):
    category = create_category(ai_client.client)
    conversation = create_conversation(ai_client.client, category["id"])
    job = AIJob(
        user_id=ai_client.user_one.id,
        job_type="chat_message",
        conversation_id=UUID(conversation["id"]),
        status="done",
    )
    with ai_client.session_factory() as db:
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

    ai_client.selected_user["value"] = ai_client.user_two
    assert ai_client.client.get(f"/api/v1/ai-jobs/{job_id}").status_code == 404


def test_chat_request_rejects_extra_fields_and_trims_content(ai_client):
    category = create_category(ai_client.client)
    conversation = create_conversation(ai_client.client, category["id"])
    invalid = ai_client.client.post(
        f"/api/v1/conversations/{conversation['id']}/chat",
        json={"content": "Question", "role": "assistant", "sources": []},
    )
    assert invalid.status_code == 422
    blank = ai_client.client.post(
        f"/api/v1/conversations/{conversation['id']}/chat",
        json={"content": "   "},
    )
    assert blank.status_code == 422
    accepted = ai_client.client.post(
        f"/api/v1/conversations/{conversation['id']}/chat",
        json={"content": "  Trim me  "},
    )
    assert accepted.status_code == 202
    _, payload = next(
        call for call in ai_client.n8n["calls"] if call[0].url.path.endswith("/chat")
    )
    assert payload["question"] == "Trim me"


def test_chat_sets_empty_conversation_title_from_first_question(ai_client):
    category = create_category(ai_client.client)
    conversation = create_conversation(ai_client.client, category["id"])
    with ai_client.session_factory() as db:
        stored = db.scalar(select(Conversation).where(Conversation.id == UUID(conversation["id"])))
        stored.title = "   "
        db.commit()
    content = "A" * 60 + " and more"

    response = ai_client.client.post(
        f"/api/v1/conversations/{conversation['id']}/chat",
        json={"content": content},
    )
    assert response.status_code == 202
    updated = ai_client.client.get(f"/api/v1/conversations/{conversation['id']}")
    assert updated.json()["title"] == "A" * 60


@pytest.mark.parametrize("status,age_field", [("pending", "created_at"), ("running", "started_at")])
def test_stale_chat_jobs_include_conversation_id(ai_client, status, age_field):
    category = create_category(ai_client.client)
    conversation = create_conversation(ai_client.client, category["id"])
    now = datetime.now(timezone.utc)
    stale_value = now - timedelta(seconds=61 if status == "pending" else 123)
    job = AIJob(
        user_id=ai_client.user_one.id,
        job_type="chat_message",
        conversation_id=UUID(conversation["id"]),
        status=status,
        created_at=stale_value if age_field == "created_at" else now,
        started_at=stale_value if age_field == "started_at" else None,
    )
    with ai_client.session_factory() as db:
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id
    result = get_job(ai_client.client, str(job_id))
    assert result["status"] == "failed"
    assert result["error_code"] == "timeout"
    assert result["conversation_id"] == conversation["id"]
