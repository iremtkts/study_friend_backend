import uuid
from collections.abc import Sequence

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Note
from app.models.quiz import Quiz, QuizQuestion
from app.schemas.quiz import QuizQuestionInput, normalize_quiz_title, validate_quiz_questions


def create_quiz_with_questions(
    db: Session,
    user_id: uuid.UUID,
    category_id: uuid.UUID,
    note_id: uuid.UUID | None,
    title: str,
    questions: Sequence[QuizQuestionInput | dict],
    source: str,
    *,
    commit: bool = True,
) -> Quiz:
    normalized_title = normalize_quiz_title(title)
    validated_questions = validate_quiz_questions(list(questions))
    if source not in {"ai", "manual"}:
        raise ValueError("Source must be 'ai' or 'manual'.")

    category = db.scalar(
        select(Category).where(Category.id == category_id, Category.user_id == user_id)
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")
    if note_id is not None:
        note = db.scalar(
            select(Note).where(
                Note.id == note_id,
                Note.user_id == user_id,
                Note.category_id == category_id,
            )
        )
        if note is None:
            raise HTTPException(status_code=404, detail="Note not found.")

    quiz = Quiz(user_id=user_id, category_id=category_id, note_id=note_id, title=normalized_title, source=source)
    quiz_questions = [
        QuizQuestion(
            quiz=quiz,
            position=position,
            question_text=question.question_text,
            options=question.options,
            correct_index=question.correct_index,
            explanation=question.explanation,
        )
        for position, question in enumerate(validated_questions)
    ]
    try:
        db.add(quiz)
        db.add_all(quiz_questions)
        if commit:
            db.commit()
            db.refresh(quiz)
            for question in quiz_questions:
                db.refresh(question)
    except Exception:
        if commit:
            db.rollback()
        raise
    return quiz
