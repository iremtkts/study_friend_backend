from decimal import Decimal
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models.note import Note
from app.models.quiz import Quiz, QuizAttempt, QuizAnswer, QuizQuestion
from app.models.study_plan import StudyPlan, StudyPlanItem
from app.models.summary import Summary
from app.schemas.study_plan import StudyPlanCreate
from tests.test_notes import create_category, notes_client


def make_question(position: int) -> dict:
    return {
        "question_text": f"Question {position}?",
        "options": ["Correct", "Incorrect"],
        "correct_index": 0,
        "explanation": f"Explanation {position}",
    }


def create_quiz(client, category_id: str, count: int = 1) -> dict:
    response = client.post(
        "/api/v1/quizzes",
        json={
            "category_id": category_id,
            "title": "Attempt quiz",
            "questions": [make_question(i) for i in range(count)],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_note(client, category_id: str, text: bytes = b"Source note") -> dict:
    response = client.post(
        "/api/v1/notes",
        data={"category_id": category_id},
        files={"file": ("source.txt", text, "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def start_attempt(client, quiz_id: str) -> dict:
    response = client.post(f"/api/v1/quizzes/{quiz_id}/attempts")
    assert response.status_code in (200, 201), response.text
    return response.json()


def question_ids_from_quiz(client, quiz_id: str) -> list[str]:
    response = client.get(f"/api/v1/quizzes/{quiz_id}")
    assert response.status_code == 200, response.text
    return [question["id"] for question in response.json()["questions"]]


def test_other_user_cannot_start_or_access_attempt(notes_client):
    client = notes_client.client
    category = create_category(client)
    quiz = create_quiz(client, category["id"])
    attempt = start_attempt(client, quiz["id"])

    notes_client.selected_user["value"] = notes_client.user_two
    assert client.post(f"/api/v1/quizzes/{quiz['id']}/attempts").status_code == 404
    assert client.get(f"/api/v1/attempts/{attempt['id']}").status_code == 404
    assert client.post(
        f"/api/v1/attempts/{attempt['id']}/submit", json={"answers": []}
    ).status_code == 404


def test_in_progress_get_exposes_only_status(notes_client):
    client = notes_client.client
    category = create_category(client)
    quiz = create_quiz(client, category["id"])
    attempt = start_attempt(client, quiz["id"])

    response = client.get(f"/api/v1/attempts/{attempt['id']}")
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"
    for forbidden in ("correct_index", "explanation", "is_correct", "answers"):
        assert forbidden not in response.json()


def test_open_attempt_is_reused_for_same_quiz(notes_client):
    client = notes_client.client
    category = create_category(client)
    quiz = create_quiz(client, category["id"])
    first = start_attempt(client, quiz["id"])
    second_response = client.post(f"/api/v1/quizzes/{quiz['id']}/attempts")
    assert second_response.status_code == 200
    assert second_response.json()["id"] == first["id"]
    with notes_client.session_factory() as db:
        assert db.scalar(
            select(func.count(QuizAttempt.id)).where(
                QuizAttempt.quiz_id == UUID(quiz["id"]), QuizAttempt.status == "in_progress"
            )
        ) == 1


@pytest.mark.parametrize(
    ("selected", "expected_correct", "expected_score"),
    [([0, 0, 0], 3, Decimal("100.00")), ([1, 1, 1], 0, Decimal("0.00")), ([], 0, Decimal("0.00"))],
)
def test_submit_scores_correct_wrong_and_unanswered(
    notes_client, selected, expected_correct, expected_score
):
    client = notes_client.client
    category = create_category(client)
    quiz = create_quiz(client, category["id"], count=3)
    attempt = start_attempt(client, quiz["id"])
    question_ids = question_ids_from_quiz(client, quiz["id"])
    response = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        json={
            "answers": [
                {"question_id": question_id, "selected_index": selected_index}
                for question_id, selected_index in zip(question_ids, selected)
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["correct_count"] == expected_correct
    assert response.json()["total_questions"] == 3
    assert Decimal(response.json()["score_percent"]) == expected_score
    assert len(response.json()["answers"]) == 3
    if not selected:
        assert all(answer["selected_index"] is None for answer in response.json()["answers"])


def test_completed_attempt_cannot_be_submitted_twice(notes_client):
    client = notes_client.client
    category = create_category(client)
    quiz = create_quiz(client, category["id"])
    attempt = start_attempt(client, quiz["id"])
    question_id = question_ids_from_quiz(client, quiz["id"])[0]
    payload = {"answers": [{"question_id": question_id, "selected_index": 0}]}
    assert client.post(f"/api/v1/attempts/{attempt['id']}/submit", json=payload).status_code == 200
    assert client.post(f"/api/v1/attempts/{attempt['id']}/submit", json=payload).status_code == 409


@pytest.mark.parametrize(
    "answers_factory",
    [
        lambda question_id: [{"question_id": str(uuid4()), "selected_index": 0}],
        lambda question_id: [
            {"question_id": question_id, "selected_index": 0},
            {"question_id": question_id, "selected_index": 1},
        ],
        lambda question_id: [{"question_id": question_id, "selected_index": 2}],
    ],
)
def test_invalid_answer_question_duplicate_or_index_is_rejected(notes_client, answers_factory):
    client = notes_client.client
    category = create_category(client)
    quiz = create_quiz(client, category["id"])
    attempt = start_attempt(client, quiz["id"])
    question_id = question_ids_from_quiz(client, quiz["id"])[0]
    response = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        json={"answers": answers_factory(question_id)},
    )
    assert response.status_code == 422


def test_stats_are_scoped_to_current_user(notes_client):
    client = notes_client.client
    category = create_category(client, "Private category")
    quiz = create_quiz(client, category["id"])
    attempt = start_attempt(client, quiz["id"])
    question_id = question_ids_from_quiz(client, quiz["id"])[0]
    submitted = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        json={"answers": [{"question_id": question_id, "selected_index": 0}]},
    )
    assert submitted.status_code == 200

    own_stats = client.get("/api/v1/stats")
    assert own_stats.status_code == 200
    assert own_stats.json()["total_attempts"] == 1
    assert own_stats.json()["categories"][0]["category_name"] == "Private category"

    notes_client.selected_user["value"] = notes_client.user_two
    other_stats = client.get("/api/v1/stats")
    assert other_stats.status_code == 200
    assert other_stats.json()["total_attempts"] == 0
    assert other_stats.json()["recent_attempts"] == []
    assert other_stats.json()["categories"] == []
    assert client.get("/api/v1/stats", params={"category_id": category["id"]}).status_code == 404


def test_recommendation_requires_owned_completed_attempt(notes_client):
    client = notes_client.client
    category = create_category(client)
    quiz = create_quiz(client, category["id"])
    attempt = start_attempt(client, quiz["id"])
    assert client.get(
        f"/api/v1/attempts/{attempt['id']}/recommendation"
    ).status_code == 409

    notes_client.selected_user["value"] = notes_client.user_two
    assert client.get(
        f"/api/v1/attempts/{attempt['id']}/recommendation"
    ).status_code == 404


def test_recommendation_lists_wrong_and_unanswered_and_creates_compatible_plan(notes_client):
    client = notes_client.client
    category = create_category(client)
    note = create_note(client, category["id"])
    quiz = create_quiz(client, category["id"], count=3)
    with notes_client.session_factory() as db:
        db.scalar(select(Quiz).where(Quiz.id == UUID(quiz["id"]))).note_id = UUID(note["id"])
        questions = db.scalars(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_id == UUID(quiz["id"]))
            .order_by(QuizQuestion.position)
        ).all()
        questions[0].question_text = "İstanbul ışıkları ve deniz"
        questions[0].explanation = "Köprü bilgisi"
        questions[1].question_text = "Astronomi gezegen yıldız"
        questions[1].explanation = "Galaksi"
        questions[2].question_text = "Tam doğru soru"
        db.add(
            Summary(
                user_id=notes_client.user_one.id,
                note_id=UUID(note["id"]),
                content="İstanbul ışıklar köprü ve deniz",
                position=0,
                source="manual",
            )
        )
        db.commit()
        question_ids = [question.id for question in questions]

    attempt = start_attempt(client, quiz["id"])
    submitted = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        json={
            "answers": [
                {"question_id": str(question_ids[0]), "selected_index": 1},
                {"question_id": str(question_ids[2]), "selected_index": 0},
            ]
        },
    )
    assert submitted.status_code == 200

    with notes_client.session_factory() as db:
        counts_before = {
            model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (Quiz, QuizAttempt, QuizAnswer, Summary, StudyPlan, StudyPlanItem)
        }

    response = client.get(f"/api/v1/attempts/{attempt['id']}/recommendation")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["summary"] == {
        "score_percent": "33.33",
        "correct_count": 1,
        "total_questions": 3,
        "wrong_count": 2,
    }
    assert [topic["question_id"] for topic in result["weak_topics"]] == [
        str(question_ids[0]),
        str(question_ids[1]),
    ]
    assert result["weak_topics"][0]["selected_option_text"] == "Incorrect"
    assert result["weak_topics"][1]["selected_option_text"] is None
    assert result["weak_topics"][0]["correct_option_text"] == "Correct"
    assert result["weak_topics"][0]["related_summaries"][0]["content"] == (
        "İstanbul ışıklar köprü ve deniz"
    )
    assert result["weak_topics"][1]["related_summaries"] == []

    plan = result["suggested_plan"]
    plan_payload = {"week_start": plan["week_start"], "items": plan["items"]}
    validated_plan = StudyPlanCreate.model_validate(plan_payload)
    assert validated_plan.week_start.weekday() == 0
    assert len(validated_plan.items) == 3
    assert [item.duration_minutes for item in validated_plan.items] == [25, 25, 30]
    assert [item.topic for item in validated_plan.items] == [
        "Tekrar: İstanbul ışıkları ve deniz",
        "Tekrar: Astronomi gezegen yıldız",
        "Testi tekrar çöz",
    ]
    assert all(item.category_id == UUID(category["id"]) for item in validated_plan.items)
    assert all(item.note_id == UUID(note["id"]) for item in validated_plan.items)
    with notes_client.session_factory() as db:
        counts_after_recommendation = {
            model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (Quiz, QuizAttempt, QuizAnswer, Summary, StudyPlan, StudyPlanItem)
        }
    assert counts_after_recommendation == counts_before

    created_plan = client.post("/api/v1/study-plans", json=plan_payload)
    assert created_plan.status_code == 201, created_plan.text


def test_recommendation_summaries_are_scoped_to_the_quiz_note_and_owner(notes_client):
    client = notes_client.client
    category = create_category(client, "Owner category")
    own_note = create_note(client, category["id"])
    second_note = create_note(client, category["id"])
    quiz = create_quiz(client, category["id"])

    notes_client.selected_user["value"] = notes_client.user_two
    foreign_category = create_category(client, "Foreign category")
    foreign_note = create_note(client, foreign_category["id"])
    notes_client.selected_user["value"] = notes_client.user_one

    with notes_client.session_factory() as db:
        stored_quiz = db.scalar(select(Quiz).where(Quiz.id == UUID(quiz["id"])))
        stored_quiz.note_id = UUID(own_note["id"])
        question = db.scalar(
            select(QuizQuestion).where(QuizQuestion.quiz_id == UUID(quiz["id"]))
        )
        question.question_text = "Türkçe İstanbul ışıkları"
        db.add_all(
            [
                Summary(
                    user_id=notes_client.user_one.id,
                    note_id=UUID(own_note["id"]),
                    content="İstanbul ışıklar harika",
                    position=0,
                    source="manual",
                ),
                Summary(
                    user_id=notes_client.user_one.id,
                    note_id=UUID(second_note["id"]),
                    content="İstanbul ışıklar başka not",
                    position=0,
                    source="manual",
                ),
                Summary(
                    user_id=notes_client.user_two.id,
                    note_id=UUID(own_note["id"]),
                    content="İstanbul ışıklar foreign owner",
                    position=0,
                    source="manual",
                ),
            ]
        )
        db.commit()

    attempt = start_attempt(client, quiz["id"])
    submitted = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        json={"answers": []},
    )
    assert submitted.status_code == 200
    response = client.get(f"/api/v1/attempts/{attempt['id']}/recommendation")
    assert response.status_code == 200
    related = response.json()["weak_topics"][0]["related_summaries"]
    assert len(related) == 1
    assert related[0]["content"] == "İstanbul ışıklar harika"
    assert related[0]["content"] not in {
        "İstanbul ışıklar başka not",
        "İstanbul ışıklar foreign owner",
    }


def test_recommendation_all_correct_has_no_weak_topics_or_plan(notes_client):
    client = notes_client.client
    category = create_category(client)
    quiz = create_quiz(client, category["id"], count=2)
    attempt = start_attempt(client, quiz["id"])
    question_ids = question_ids_from_quiz(client, quiz["id"])
    response = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        json={
            "answers": [
                {"question_id": question_id, "selected_index": 0}
                for question_id in question_ids
            ]
        },
    )
    assert response.status_code == 200

    recommendation = client.get(f"/api/v1/attempts/{attempt['id']}/recommendation")
    assert recommendation.status_code == 200
    assert recommendation.json()["weak_topics"] == []
    assert recommendation.json()["suggested_plan"]["items"] == []
    assert recommendation.json()["message"].startswith("Tebrikler")


def test_recommendation_without_quiz_note_uses_owned_category_summaries(notes_client):
    client = notes_client.client
    category = create_category(client)
    note = create_note(client, category["id"])
    quiz = create_quiz(client, category["id"])
    with notes_client.session_factory() as db:
        question = db.scalar(
            select(QuizQuestion).where(QuizQuestion.quiz_id == UUID(quiz["id"]))
        )
        question.question_text = "Volkanik kayaç oluşumu"
        db.add(
            Summary(
                user_id=notes_client.user_one.id,
                note_id=UUID(note["id"]),
                content="Volkanik kayaçların oluşumu",
                position=0,
                source="manual",
            )
        )
        db.commit()

    attempt = start_attempt(client, quiz["id"])
    submitted = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        json={"answers": []},
    )
    assert submitted.status_code == 200
    recommendation = client.get(f"/api/v1/attempts/{attempt['id']}/recommendation")
    assert recommendation.status_code == 200
    assert recommendation.json()["weak_topics"][0]["related_summaries"][0]["content"] == (
        "Volkanik kayaçların oluşumu"
    )


def test_suggested_plan_starts_tomorrow_at_six_and_keeps_ten_minute_gaps(notes_client):
    client = notes_client.client
    category = create_category(client)
    quiz = create_quiz(client, category["id"], count=3)
    attempt = start_attempt(client, quiz["id"])
    submitted = client.post(
        f"/api/v1/attempts/{attempt['id']}/submit",
        json={"answers": []},
    )
    assert submitted.status_code == 200

    recommendation = client.get(f"/api/v1/attempts/{attempt['id']}/recommendation")
    assert recommendation.status_code == 200
    plan = recommendation.json()["suggested_plan"]
    tomorrow = date.today() + timedelta(days=1)
    assert date.fromisoformat(plan["week_start"]).weekday() == 0
    assert plan["items"][0]["day_of_week"] == tomorrow.isoweekday()
    assert plan["items"][0]["start_time"] == "18:00:00"
    assert plan["items"][-1]["topic"] == "Testi tekrar çöz"

    by_day: dict[int, list[str]] = {}
    for item in plan["items"]:
        by_day.setdefault(item["day_of_week"], []).append(item["start_time"])
    for times in by_day.values():
        minutes = [int(value[:2]) * 60 + int(value[3:5]) for value in times]
        assert all(second - first == 35 for first, second in zip(minutes, minutes[1:]))
