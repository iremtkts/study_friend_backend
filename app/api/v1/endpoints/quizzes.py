import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models import Category, Note, User
from app.models.quiz import Quiz, QuizQuestion
from app.schemas.quiz import QuizCreate, QuizDetailOut, QuizListOut, QuizOut, QuizQuestionPublicOut
from app.services.quizzes import create_quiz_with_questions

router = APIRouter(prefix="/api/v1/quizzes", tags=["quizzes"])


@router.post("", response_model=QuizOut, status_code=status.HTTP_201_CREATED)
def create_quiz(
    payload: QuizCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> QuizOut:
    quiz = create_quiz_with_questions(
        db=db,
        user_id=current_user.id,
        category_id=payload.category_id,
        note_id=payload.note_id,
        title=payload.title,
        questions=payload.questions,
        source="manual",
    )
    return QuizOut.model_validate(quiz)


@router.get("", response_model=list[QuizListOut])
def list_quizzes(
    category_id: uuid.UUID = Query(...),
    note_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[QuizListOut]:
    category = db.scalar(
        select(Category.id).where(Category.id == category_id, Category.user_id == current_user.id)
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")
    if note_id is not None:
        note = db.scalar(
            select(Note.id).where(
                Note.id == note_id,
                Note.user_id == current_user.id,
                Note.category_id == category_id,
            )
        )
        if note is None:
            raise HTTPException(status_code=404, detail="Note not found.")

    rows = db.execute(
        select(Quiz, func.count(QuizQuestion.id).label("question_count"))
        .outerjoin(QuizQuestion, QuizQuestion.quiz_id == Quiz.id)
        .where(
            Quiz.user_id == current_user.id,
            Quiz.category_id == category_id,
            *([Quiz.note_id == note_id] if note_id is not None else []),
        )
        .group_by(Quiz.id)
        .order_by(Quiz.created_at.desc(), Quiz.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        QuizListOut(**QuizOut.model_validate(quiz).model_dump(), question_count=question_count)
        for quiz, question_count in rows
    ]


@router.get("/{quiz_id}", response_model=QuizDetailOut)
def get_quiz(
    quiz_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> QuizDetailOut:
    quiz = db.scalar(select(Quiz).where(Quiz.id == quiz_id, Quiz.user_id == current_user.id))
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found.")
    questions = db.scalars(
        select(QuizQuestion)
        .where(QuizQuestion.quiz_id == quiz.id)
        .order_by(QuizQuestion.position)
    ).all()
    return QuizDetailOut(
        **QuizOut.model_validate(quiz).model_dump(),
        questions=[QuizQuestionPublicOut.model_validate(question) for question in questions],
    )


@router.delete("/{quiz_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_quiz(
    quiz_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    quiz = db.scalar(select(Quiz).where(Quiz.id == quiz_id, Quiz.user_id == current_user.id))
    if quiz is None:
        raise HTTPException(status_code=404, detail="Quiz not found.")
    db.delete(quiz)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
