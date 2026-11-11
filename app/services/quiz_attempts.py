import uuid
import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.note import Note
from app.models.quiz import Quiz, QuizAnswer, QuizAttempt, QuizQuestion
from app.models.summary import Summary
from app.schemas.quiz_attempt import (
    AttemptCompletedOut,
    AttemptInProgressOut,
    AttemptQuestionResultOut,
    AttemptRecommendationOut,
    AttemptRecommendationSummary,
    AttemptSubmit,
    RelatedSummaryOut,
    SuggestedPlanOut,
    WeakTopicOut,
)
from app.schemas.study_plan import StudyPlanItemCreate


def get_owned_attempt(db: Session, attempt_id: uuid.UUID, user_id: uuid.UUID) -> QuizAttempt:
    attempt = db.scalar(
        select(QuizAttempt).where(QuizAttempt.id == attempt_id, QuizAttempt.user_id == user_id)
    )
    if attempt is None:
        raise HTTPException(status_code=404, detail="Attempt not found.")
    return attempt


def start_attempt(db: Session, quiz_id: uuid.UUID, user_id: uuid.UUID) -> tuple[QuizAttempt, bool]:
    quiz = db.scalar(
        select(Quiz).where(Quiz.id == quiz_id, Quiz.user_id == user_id).with_for_update()
    )
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found.")
    existing = db.scalar(
        select(QuizAttempt).where(
            QuizAttempt.quiz_id == quiz.id,
            QuizAttempt.user_id == user_id,
            QuizAttempt.status == "in_progress",
        )
    )
    if existing is not None:
        return existing, False
    attempt = QuizAttempt(quiz_id=quiz.id, user_id=user_id, status="in_progress")
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt, True


def completed_attempt_response(
    db: Session, attempt: QuizAttempt, user_id: uuid.UUID
) -> AttemptCompletedOut:
    question_rows = db.execute(
        select(QuizQuestion, QuizAnswer)
        .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
        .join(QuizAttempt, QuizAttempt.id == attempt.id)
        .outerjoin(
            QuizAnswer,
            (QuizAnswer.question_id == QuizQuestion.id) & (QuizAnswer.attempt_id == attempt.id),
        )
        .where(
            QuizQuestion.quiz_id == attempt.quiz_id,
            Quiz.user_id == user_id,
            QuizAttempt.user_id == user_id,
        )
        .order_by(QuizQuestion.position)
    ).all()
    return AttemptCompletedOut(
        id=attempt.id,
        quiz_id=attempt.quiz_id,
        status="completed",
        started_at=attempt.started_at,
        completed_at=attempt.completed_at,
        correct_count=attempt.correct_count,
        total_questions=attempt.total_questions,
        score_percent=attempt.score_percent,
        answers=[
            AttemptQuestionResultOut(
                question_id=question.id,
                question_text=question.question_text,
                selected_index=answer.selected_index if answer is not None else None,
                correct_index=question.correct_index,
                is_correct=answer.is_correct if answer is not None else False,
                explanation=question.explanation,
            )
            for question, answer in question_rows
        ],
    )


def submit_attempt(
    db: Session, attempt_id: uuid.UUID, user_id: uuid.UUID, payload: AttemptSubmit
) -> AttemptCompletedOut:
    attempt = get_owned_attempt(db, attempt_id, user_id)
    if attempt.status != "in_progress":
        raise HTTPException(status_code=409, detail="Attempt has already been completed.")

    questions = db.scalars(
        select(QuizQuestion)
        .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
        .join(QuizAttempt, QuizAttempt.id == attempt.id)
        .where(
            QuizQuestion.quiz_id == attempt.quiz_id,
            Quiz.user_id == user_id,
            QuizAttempt.user_id == user_id,
        )
        .order_by(QuizQuestion.position)
    ).all()
    by_id = {question.id: question for question in questions}
    submitted: dict[uuid.UUID, int | None] = {}
    for answer in payload.answers:
        if answer.question_id in submitted:
            raise HTTPException(status_code=422, detail="A question may only be answered once.")
        question = by_id.get(answer.question_id)
        if question is None:
            raise HTTPException(status_code=422, detail="Question does not belong to this quiz.")
        if answer.selected_index is not None and answer.selected_index >= len(question.options):
            raise HTTPException(status_code=422, detail="Selected option is outside the question's options.")
        submitted[answer.question_id] = answer.selected_index

    answer_rows: list[QuizAnswer] = []
    correct_count = 0
    for question in questions:
        selected_index = submitted.get(question.id)
        is_correct = selected_index is not None and selected_index == question.correct_index
        correct_count += int(is_correct)
        answer_rows.append(
            QuizAnswer(
                attempt_id=attempt.id,
                question_id=question.id,
                selected_index=selected_index,
                is_correct=is_correct,
            )
        )
    total = len(questions)
    score = (Decimal(correct_count * 100) / Decimal(total)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    try:
        result = db.execute(
            update(QuizAttempt)
            .where(
                QuizAttempt.id == attempt.id,
                QuizAttempt.user_id == user_id,
                QuizAttempt.status == "in_progress",
            )
            .values(
                status="completed",
                completed_at=func.now(),
                correct_count=correct_count,
                total_questions=total,
                score_percent=score,
            )
        )
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="Attempt has already been completed.")
        db.add_all(answer_rows)
        db.commit()
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise

    attempt = get_owned_attempt(db, attempt_id, user_id)
    return completed_attempt_response(db, attempt, user_id)


def _normalized_keywords(value: str) -> set[str]:
    value = value.translate(str.maketrans({"İ": "i", "I": "ı"})).lower()
    words = re.findall(r"[^\W_]+", value, flags=re.UNICODE)
    return {word[:5] for word in words if len(word) >= 3}


def _related_summaries(
    db: Session,
    user_id: uuid.UUID,
    quiz: Quiz,
    search_text: str,
) -> list[RelatedSummaryOut]:
    if quiz.note_id is not None:
        statement = (
            select(Summary)
            .join(Note, Note.id == Summary.note_id)
            .where(
                Summary.user_id == user_id,
                Summary.note_id == quiz.note_id,
                Note.user_id == user_id,
            )
        )
    else:
        statement = (
            select(Summary)
            .join(Note, Note.id == Summary.note_id)
            .where(
                Summary.user_id == user_id,
                Note.user_id == user_id,
                Note.category_id == quiz.category_id,
            )
        )
    cards = db.scalars(statement.order_by(Summary.position, Summary.id)).all()
    question_keywords = _normalized_keywords(search_text)
    scored = [
        (len(question_keywords & _normalized_keywords(card.content)), card)
        for card in cards
    ]
    return [
        RelatedSummaryOut(id=card.id, content=card.content)
        for score, card in sorted(scored, key=lambda item: (-item[0], item[1].position, item[1].id))
        if score > 0
    ][:2]


def _suggested_plan(
    quiz: Quiz, weak_topics: list[WeakTopicOut], *, today: date | None = None
) -> SuggestedPlanOut:
    tomorrow = (today or date.today()) + timedelta(days=1)
    week_start = tomorrow - timedelta(days=tomorrow.weekday())
    if not weak_topics:
        return SuggestedPlanOut(week_start=week_start, items=[])

    days = list(range(tomorrow.isoweekday(), 8))
    day_counts: dict[int, int] = {}
    items: list[StudyPlanItemCreate] = []
    for topic_index, weak_topic in enumerate(weak_topics[:5]):
        day = days[min(topic_index, len(days) - 1)]
        block_index = day_counts.get(day, 0)
        day_counts[day] = block_index + 1
        items.append(
            StudyPlanItemCreate(
                day_of_week=day,
                start_time=(datetime.combine(date.min, time(18, 0)) + timedelta(minutes=35 * block_index)).time(),
                duration_minutes=25,
                topic=f"Tekrar: {weak_topic.question_text[:60]}",
                category_id=quiz.category_id,
                note_id=quiz.note_id,
            )
        )

    review_day = 7
    review_block = day_counts.get(review_day, 0)
    items.append(
        StudyPlanItemCreate(
            day_of_week=review_day,
            start_time=(datetime.combine(date.min, time(18, 0)) + timedelta(minutes=35 * review_block)).time(),
            duration_minutes=30,
            topic="Testi tekrar çöz",
            category_id=quiz.category_id,
            note_id=quiz.note_id,
        )
    )
    return SuggestedPlanOut(week_start=week_start, items=items)


def get_attempt_recommendation(
    db: Session, attempt_id: uuid.UUID, user_id: uuid.UUID
) -> AttemptRecommendationOut:
    attempt = get_owned_attempt(db, attempt_id, user_id)
    if attempt.status != "completed":
        raise HTTPException(status_code=409, detail="Attempt is not completed.")

    quiz = db.scalar(
        select(Quiz).where(Quiz.id == attempt.quiz_id, Quiz.user_id == user_id)
    )
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found.")

    rows = db.execute(
        select(QuizQuestion, QuizAnswer)
        .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
        .join(QuizAttempt, QuizAttempt.id == attempt.id)
        .outerjoin(
            QuizAnswer,
            (QuizAnswer.question_id == QuizQuestion.id)
            & (QuizAnswer.attempt_id == attempt.id),
        )
        .where(
            QuizQuestion.quiz_id == quiz.id,
            Quiz.user_id == user_id,
            QuizAttempt.user_id == user_id,
        )
        .order_by(QuizQuestion.position)
    ).all()
    weak_topics: list[WeakTopicOut] = []
    for question, answer in rows:
        if answer is not None and answer.is_correct:
            continue
        selected_index = answer.selected_index if answer is not None else None
        selected_option = (
            question.options[selected_index]
            if selected_index is not None and selected_index < len(question.options)
            else None
        )
        weak_topics.append(
            WeakTopicOut(
                question_id=question.id,
                question_text=question.question_text,
                selected_option_text=selected_option,
                correct_option_text=question.options[question.correct_index],
                explanation=question.explanation,
                related_summaries=_related_summaries(
                    db,
                    user_id,
                    quiz,
                    f"{question.question_text} {question.explanation or ''}",
                ),
            )
        )

    correct_count = attempt.correct_count or 0
    total_questions = attempt.total_questions or len(rows)
    wrong_count = total_questions - correct_count
    message = (
        "Tebrikler, tüm soruları doğru yanıtladın!"
        if wrong_count == 0
        else f"{total_questions} sorudan {wrong_count}'ini yanlış ya da boş bıraktın; aşağıdaki konuları tekrar etmeni öneririm."
    )
    return AttemptRecommendationOut(
        summary=AttemptRecommendationSummary(
            score_percent=attempt.score_percent or Decimal("0"),
            correct_count=correct_count,
            total_questions=total_questions,
            wrong_count=wrong_count,
        ),
        weak_topics=weak_topics,
        suggested_plan=_suggested_plan(quiz, weak_topics),
        message=message,
    )
