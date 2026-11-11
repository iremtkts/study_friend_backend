import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models import User
from app.models.study_plan import StudyPlan, StudyPlanItem
from app.schemas.study_plan import (
    StudyPlanCreate,
    StudyPlanDetailOut,
    StudyPlanItemCreate,
    StudyPlanItemOut,
    StudyPlanItemUpdate,
    StudyPlanOut,
    StudyPlanProgressOut,
)
from app.services.study_plans import (
    _validate_item_references,
    create_study_plan_with_items,
    validate_item_payload,
)

router = APIRouter(prefix="/api/v1", tags=["study plans"])


def _owned_plan(db: Session, plan_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool = False) -> StudyPlan:
    query = select(StudyPlan).where(StudyPlan.id == plan_id, StudyPlan.user_id == user_id)
    if lock:
        query = query.with_for_update()
    plan = db.scalar(query)
    if plan is None:
        raise HTTPException(status_code=404, detail="Study plan not found.")
    return plan


def _plan_detail(db: Session, plan: StudyPlan, user_id: uuid.UUID) -> StudyPlanDetailOut:
    items = db.scalars(
        select(StudyPlanItem)
        .where(StudyPlanItem.plan_id == plan.id, StudyPlan.user_id == user_id)
        .join(StudyPlan, StudyPlan.id == StudyPlanItem.plan_id)
        .order_by(StudyPlanItem.day_of_week, StudyPlanItem.start_time, StudyPlanItem.id)
    ).all()
    total = len(items)
    completed = sum(item.is_completed for item in items)
    progress = round((completed / total * 100) if total else 0.0, 2)
    return StudyPlanDetailOut(
        **StudyPlanOut.model_validate(plan).model_dump(),
        items=[StudyPlanItemOut.model_validate(item) for item in items],
        progress=StudyPlanProgressOut(
            total_items=total,
            completed_items=completed,
            progress_percent=progress,
        ),
    )


@router.post("/study-plans", response_model=StudyPlanDetailOut, status_code=status.HTTP_201_CREATED)
def create_study_plan(
    payload: StudyPlanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StudyPlanDetailOut:
    plan = create_study_plan_with_items(
        db=db,
        user_id=current_user.id,
        week_start=payload.week_start,
        title=payload.title,
        items=payload.items,
        source="manual",
    )
    return _plan_detail(db, plan, current_user.id)


@router.get("/study-plans", response_model=list[StudyPlanOut])
def list_study_plans(
    week_start: date | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[StudyPlanOut]:
    query = select(StudyPlan).where(StudyPlan.user_id == current_user.id)
    if week_start is not None:
        query = query.where(StudyPlan.week_start == week_start)
    plans = db.scalars(
        query.order_by(StudyPlan.week_start.desc(), StudyPlan.id).limit(limit).offset(offset)
    ).all()
    return [StudyPlanOut.model_validate(plan) for plan in plans]


@router.get("/study-plans/{plan_id}", response_model=StudyPlanDetailOut)
def get_study_plan(
    plan_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StudyPlanDetailOut:
    plan = _owned_plan(db, plan_id, current_user.id)
    return _plan_detail(db, plan, current_user.id)


@router.delete("/study-plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_study_plan(
    plan_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    plan = _owned_plan(db, plan_id, current_user.id)
    db.delete(plan)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/study-plans/{plan_id}/items",
    response_model=StudyPlanItemOut,
    status_code=status.HTTP_201_CREATED,
)
def add_study_plan_item(
    plan_id: uuid.UUID,
    payload: StudyPlanItemCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StudyPlanItemOut:
    plan = _owned_plan(db, plan_id, current_user.id, lock=True)
    item_count = db.scalar(
        select(func.count(StudyPlanItem.id))
        .join(StudyPlan, StudyPlan.id == StudyPlanItem.plan_id)
        .where(StudyPlanItem.plan_id == plan.id, StudyPlan.user_id == current_user.id)
    )
    if item_count >= 100:
        raise HTTPException(status_code=409, detail="A study plan can contain at most 100 items.")
    item_data = validate_item_payload(db, current_user.id, payload)
    item = StudyPlanItem(plan_id=plan.id, **item_data.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return StudyPlanItemOut.model_validate(item)


@router.patch("/study-plan-items/{item_id}", response_model=StudyPlanItemOut)
def update_study_plan_item(
    item_id: uuid.UUID,
    payload: StudyPlanItemUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StudyPlanItemOut:
    item = db.scalar(
        select(StudyPlanItem)
        .join(StudyPlan, StudyPlan.id == StudyPlanItem.plan_id)
        .where(StudyPlanItem.id == item_id, StudyPlan.user_id == current_user.id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Study plan item not found.")

    updates = payload.model_dump(exclude_unset=True)
    category_id = updates.get("category_id", item.category_id)
    note_id = updates.get("note_id", item.note_id)
    _validate_item_references(db, current_user.id, category_id, note_id)
    for field, value in updates.items():
        if field not in {"category_id", "note_id", "is_completed"}:
            setattr(item, field, value)
    if "category_id" in updates:
        item.category_id = updates["category_id"]
    if "note_id" in updates:
        item.note_id = updates["note_id"]
    if "is_completed" in updates:
        item.is_completed = updates["is_completed"]
        item.completed_at = datetime.now(timezone.utc) if updates["is_completed"] else None
    db.commit()
    db.refresh(item)
    return StudyPlanItemOut.model_validate(item)


@router.delete("/study-plan-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_study_plan_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    item = db.scalar(
        select(StudyPlanItem)
        .join(StudyPlan, StudyPlan.id == StudyPlanItem.plan_id)
        .where(StudyPlanItem.id == item_id, StudyPlan.user_id == current_user.id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Study plan item not found.")
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
