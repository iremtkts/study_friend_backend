import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models import Category, Note, User
from app.models.summary import Summary
from app.schemas.summary import SummaryCreate, SummaryOut, SummaryUpdate

router = APIRouter(prefix="/api/v1", tags=["summaries"])


def _get_owned_note(db: Session, note_id: uuid.UUID, user_id: uuid.UUID) -> Note:
    note = db.scalar(select(Note).where(Note.id == note_id, Note.user_id == user_id))
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found.")
    return note


def _get_owned_summary(db: Session, summary_id: uuid.UUID, user_id: uuid.UUID) -> Summary:
    summary = db.scalar(
        select(Summary).where(Summary.id == summary_id, Summary.user_id == user_id)
    )
    if summary is None:
        raise HTTPException(status_code=404, detail="Summary not found.")
    return summary


@router.get("/summaries", response_model=list[SummaryOut])
def list_summaries(
    category_id: uuid.UUID = Query(...),
    note_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SummaryOut]:
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
    query = (
        select(Summary)
        .join(Note, Summary.note_id == Note.id)
        .where(
            Summary.user_id == current_user.id,
            Note.user_id == current_user.id,
            Note.category_id == category_id,
        )
    )
    if note_id is not None:
        query = query.where(Summary.note_id == note_id)
    summaries = db.scalars(
        query.order_by(Note.id, Summary.position, Summary.id).limit(limit).offset(offset)
    ).all()
    return [SummaryOut.model_validate(summary) for summary in summaries]


@router.get("/notes/{note_id}/summaries", response_model=list[SummaryOut])
def list_note_summaries(
    note_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SummaryOut]:
    _get_owned_note(db, note_id, current_user.id)
    summaries = db.scalars(
        select(Summary)
        .where(Summary.user_id == current_user.id, Summary.note_id == note_id)
        .order_by(Summary.position, Summary.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [SummaryOut.model_validate(summary) for summary in summaries]


@router.post(
    "/notes/{note_id}/summaries",
    response_model=SummaryOut,
    status_code=status.HTTP_201_CREATED,
)
def create_summary(
    note_id: uuid.UUID,
    payload: SummaryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SummaryOut:
    _get_owned_note(db, note_id, current_user.id)
    last_position = db.scalar(
        select(func.max(Summary.position)).where(
            Summary.user_id == current_user.id,
            Summary.note_id == note_id,
        )
    )
    summary = Summary(
        user_id=current_user.id,
        note_id=note_id,
        content=payload.content,
        source="manual",
        position=(last_position if last_position is not None else -1) + 1,
    )
    db.add(summary)
    db.commit()
    db.refresh(summary)
    return SummaryOut.model_validate(summary)


@router.patch("/summaries/{summary_id}", response_model=SummaryOut)
def update_summary(
    summary_id: uuid.UUID,
    payload: SummaryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SummaryOut:
    summary = _get_owned_summary(db, summary_id, current_user.id)
    summary.content = payload.content
    db.commit()
    db.refresh(summary)
    return SummaryOut.model_validate(summary)


@router.delete("/summaries/{summary_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_summary(
    summary_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    summary = _get_owned_summary(db, summary_id, current_user.id)
    db.delete(summary)
    db.commit()
