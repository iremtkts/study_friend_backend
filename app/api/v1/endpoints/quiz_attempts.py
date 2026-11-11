import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models import Category, User
from app.models.quiz import Quiz, QuizAttempt
from app.schemas.quiz_attempt import (
    AttemptCompletedOut,
    AttemptInProgressOut,
    AttemptListItemOut,
    AttemptRecommendationOut,
    AttemptSubmit,
    CategoryStatsOut,
    QuizStatsOut,
    RecentAttemptOut,
)
from app.services.quiz_attempts import (
    completed_attempt_response,
    get_attempt_recommendation,
    get_owned_attempt,
    start_attempt,
    submit_attempt,
)

router = APIRouter(prefix="/api/v1", tags=["quiz attempts"])


@router.get(
    "/attempts/{attempt_id}/recommendation",
    response_model=AttemptRecommendationOut,
)
def recommend_after_attempt(
    attempt_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AttemptRecommendationOut:
    return get_attempt_recommendation(db, attempt_id, current_user.id)


@router.post(
    "/quizzes/{quiz_id}/attempts",
    response_model=AttemptInProgressOut,
    status_code=status.HTTP_201_CREATED,
)
def create_attempt(
    quiz_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AttemptInProgressOut:
    attempt, created = start_attempt(db, quiz_id, current_user.id)
    if not created:
        response.status_code = status.HTTP_200_OK
    return AttemptInProgressOut(
        id=attempt.id,
        quiz_id=attempt.quiz_id,
        status="in_progress",
        started_at=attempt.started_at,
    )


@router.post("/attempts/{attempt_id}/submit", response_model=AttemptCompletedOut)
def submit_quiz_attempt(
    attempt_id: uuid.UUID,
    payload: AttemptSubmit,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AttemptCompletedOut:
    return submit_attempt(db, attempt_id, current_user.id, payload)


@router.get("/attempts/{attempt_id}", response_model=AttemptInProgressOut | AttemptCompletedOut)
def get_quiz_attempt(
    attempt_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AttemptInProgressOut | AttemptCompletedOut:
    attempt = get_owned_attempt(db, attempt_id, current_user.id)
    if attempt.status == "in_progress":
        return AttemptInProgressOut(
            id=attempt.id,
            quiz_id=attempt.quiz_id,
            status="in_progress",
            started_at=attempt.started_at,
        )
    return completed_attempt_response(db, attempt, current_user.id)


@router.get("/attempts", response_model=list[AttemptListItemOut])
def list_quiz_attempts(
    quiz_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AttemptListItemOut]:
    if quiz_id is not None:
        quiz = db.scalar(select(Quiz.id).where(Quiz.id == quiz_id, Quiz.user_id == current_user.id))
        if quiz is None:
            raise HTTPException(status_code=404, detail="Quiz not found.")
    rows = db.execute(
        select(QuizAttempt, Quiz.title)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .where(
            QuizAttempt.user_id == current_user.id,
            Quiz.user_id == current_user.id,
            QuizAttempt.status == "completed",
            *([QuizAttempt.quiz_id == quiz_id] if quiz_id is not None else []),
        )
        .order_by(QuizAttempt.completed_at.desc(), QuizAttempt.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        AttemptListItemOut(
            id=attempt.id,
            quiz_id=attempt.quiz_id,
            quiz_title=title,
            score_percent=attempt.score_percent,
            completed_at=attempt.completed_at,
        )
        for attempt, title in rows
    ]


@router.get("/stats", response_model=QuizStatsOut)
def quiz_stats(
    category_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> QuizStatsOut:
    if category_id is not None:
        category = db.scalar(
            select(Category.id).where(Category.id == category_id, Category.user_id == current_user.id)
        )
        if category is None:
            raise HTTPException(status_code=404, detail="Category not found.")

    common_filters = [
        QuizAttempt.user_id == current_user.id,
        Quiz.user_id == current_user.id,
        QuizAttempt.status == "completed",
    ]
    if category_id is not None:
        common_filters.append(Quiz.category_id == category_id)
    total, average, highest = db.execute(
        select(
            func.count(QuizAttempt.id),
            func.avg(QuizAttempt.score_percent),
            func.max(QuizAttempt.score_percent),
        )
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .where(*common_filters)
    ).one()
    recent_rows = db.execute(
        select(QuizAttempt.completed_at, Quiz.title, QuizAttempt.score_percent)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .where(*common_filters)
        .order_by(QuizAttempt.completed_at.desc(), QuizAttempt.id)
        .limit(10)
    ).all()

    categories = None
    if category_id is None:
        category_rows = db.execute(
            select(
                Category.id,
                Category.name,
                func.count(QuizAttempt.id),
                func.avg(QuizAttempt.score_percent),
            )
            .join(Quiz, Quiz.category_id == Category.id)
            .join(QuizAttempt, QuizAttempt.quiz_id == Quiz.id)
            .where(
                Category.user_id == current_user.id,
                Quiz.user_id == current_user.id,
                QuizAttempt.user_id == current_user.id,
                QuizAttempt.status == "completed",
            )
            .group_by(Category.id)
            .order_by(Category.name, Category.id)
        ).all()
        categories = [
            CategoryStatsOut(
                category_id=cat_id,
                category_name=name,
                attempt_count=count,
                average_score=cat_average,
            )
            for cat_id, name, count, cat_average in category_rows
        ]

    return QuizStatsOut(
        total_attempts=total,
        average_score=average,
        highest_score=highest,
        recent_attempts=[
            RecentAttemptOut(attempted_at=completed_at, quiz_title=title, score_percent=score)
            for completed_at, title, score in recent_rows
        ],
        categories=categories,
    )
