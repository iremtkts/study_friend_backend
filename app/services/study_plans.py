import uuid
from collections.abc import Sequence
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Category, Note
from app.models.study_plan import StudyPlan, StudyPlanItem
from app.schemas.study_plan import StudyPlanItemCreate, _trim_topic, _validate_week_start


def _validate_item_references(
    db: Session,
    user_id: uuid.UUID,
    category_id: uuid.UUID | None,
    note_id: uuid.UUID | None,
) -> None:
    if category_id is not None:
        category = db.scalar(
            select(Category.id).where(Category.id == category_id, Category.user_id == user_id)
        )
        if category is None:
            raise HTTPException(status_code=404, detail="Category not found.")
    if note_id is not None:
        note_query = select(Note.id).where(Note.id == note_id, Note.user_id == user_id)
        if category_id is not None:
            note_query = note_query.where(Note.category_id == category_id)
        if db.scalar(note_query) is None:
            raise HTTPException(status_code=404, detail="Note not found.")


def validate_item_payload(
    db: Session,
    user_id: uuid.UUID,
    item: StudyPlanItemCreate,
) -> StudyPlanItemCreate:
    normalized = StudyPlanItemCreate.model_validate(item)
    _validate_item_references(db, user_id, normalized.category_id, normalized.note_id)
    return normalized


def create_study_plan_with_items(
    db: Session,
    user_id: uuid.UUID,
    week_start: date,
    title: str | None,
    items: Sequence[StudyPlanItemCreate | dict],
    source: str,
) -> StudyPlan:
    _validate_week_start(week_start)
    if title is not None and len(title) > 200:
        raise ValueError("Title cannot exceed 200 characters.")
    if len(items) > 100:
        raise ValueError("A study plan can contain at most 100 items.")
    if source not in {"ai", "manual"}:
        raise ValueError("Source must be 'ai' or 'manual'.")

    normalized_items = [StudyPlanItemCreate.model_validate(item) for item in items]
    plan = StudyPlan(user_id=user_id, week_start=week_start, title=title, source=source)
    try:
        for item in normalized_items:
            _validate_item_references(db, user_id, item.category_id, item.note_id)
        plan.items = [
            StudyPlanItem(
                day_of_week=item.day_of_week,
                start_time=item.start_time,
                duration_minutes=item.duration_minutes,
                topic=_trim_topic(item.topic),
                category_id=item.category_id,
                note_id=item.note_id,
            )
            for item in normalized_items
        ]
        db.add(plan)
        db.commit()
        db.refresh(plan)
        return plan
    except IntegrityError as exc:
        db.rollback()
        constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint_name == "uq_study_plans_user_week_start" or (
            "UNIQUE constraint failed: study_plans.user_id, study_plans.week_start" in str(exc.orig)
        ):
            raise HTTPException(status_code=409, detail="A study plan already exists for this week.") from exc
        raise
    except Exception:
        db.rollback()
        raise
