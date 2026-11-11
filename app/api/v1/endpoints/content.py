import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models import Category, Note, User
from app.schemas.content import CategoryCreate, CategoryOut, CategoryUpdate, MessageOut, NoteDetailOut, NoteListOut
from app.services.note_indexing import queue_index_note_job
from app.services.storage import cleanup_temporary_upload, delete_file, extract_pdf_text, store_upload

router = APIRouter(prefix="/api/v1", tags=["categories", "notes"])


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CategoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CategoryOut:
    category = Category(user_id=current_user.id, name=payload.name)
    db.add(category)
    try:
        db.commit()
        db.refresh(category)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A category with this name already exists.") from exc
    return CategoryOut.model_validate(category)


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CategoryOut]:
    categories = db.scalars(
        select(Category).where(Category.user_id == current_user.id).order_by(Category.created_at, Category.id)
    ).all()
    return [CategoryOut.model_validate(category) for category in categories]


@router.patch("/categories/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: uuid.UUID,
    payload: CategoryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CategoryOut:
    category = db.scalar(
        select(Category).where(Category.id == category_id, Category.user_id == current_user.id)
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")
    category.name = payload.name
    try:
        db.commit()
        db.refresh(category)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A category with this name already exists.") from exc
    return CategoryOut.model_validate(category)


@router.delete("/categories/{category_id}", response_model=MessageOut)
def delete_category(
    category_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MessageOut:
    category = db.scalar(
        select(Category).where(Category.id == category_id, Category.user_id == current_user.id)
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")
    has_notes = db.scalar(
        select(Note.id).where(Note.category_id == category.id, Note.user_id == current_user.id).limit(1)
    )
    if has_notes is not None:
        raise HTTPException(status_code=409, detail="Category contains notes and cannot be deleted.")
    db.delete(category)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Category contains notes and cannot be deleted.") from exc
    return MessageOut(message="Category deleted.")


@router.post("/notes", response_model=NoteListOut, status_code=status.HTTP_201_CREATED)
async def create_note(
    background_tasks: BackgroundTasks,
    category_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NoteListOut:
    category = db.scalar(
        select(Category).where(Category.id == category_id, Category.user_id == current_user.id)
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")

    stored = await store_upload(file)
    try:
        extracted_text = stored.extracted_text
        if stored.file_type == "pdf":
            extracted_text = extract_pdf_text(stored.file_path)
        if extracted_text is None:
            raise HTTPException(status_code=422, detail="Could not extract text from the uploaded file.")

        note = Note(
            user_id=current_user.id,
            category_id=category.id,
            title=title.strip() if title and title.strip() else None,
            original_filename=file.filename or "",
            file_type=stored.file_type,
            stored_filename=stored.stored_filename,
            file_size_bytes=stored.file_size_bytes,
            extracted_text=extracted_text,
        )
        db.add(note)
        db.flush()
        db.refresh(note)
        response = NoteListOut.model_validate(note)
        db.commit()
        cleanup_temporary_upload(stored.file_path)
        try:
            queue_index_note_job(db, current_user.id, note.id, background_tasks)
        except Exception:
            db.rollback()
        return response
    except Exception:
        db.rollback()
        try:
            delete_file(stored.stored_filename)
        except Exception:
            pass
        try:
            cleanup_temporary_upload(stored.file_path)
        except OSError:
            pass
        raise


@router.get("/notes", response_model=list[NoteListOut])
def list_notes(
    category_id: uuid.UUID = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[NoteListOut]:
    category = db.scalar(
        select(Category.id).where(Category.id == category_id, Category.user_id == current_user.id)
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")
    notes = db.scalars(
        select(Note)
        .where(Note.user_id == current_user.id, Note.category_id == category_id)
        .order_by(Note.created_at.desc(), Note.id)
    ).all()
    return [NoteListOut.model_validate(note) for note in notes]


@router.get("/notes/{note_id}", response_model=NoteDetailOut)
def get_note(
    note_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NoteDetailOut:
    note = db.scalar(select(Note).where(Note.id == note_id, Note.user_id == current_user.id))
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found.")
    return NoteDetailOut.model_validate(note)


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    note = db.scalar(select(Note).where(Note.id == note_id, Note.user_id == current_user.id))
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found.")
    stored_filename = note.stored_filename
    db.delete(note)
    db.commit()
    delete_file(stored_filename)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
