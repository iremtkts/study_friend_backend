import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.ai_job import AIJob
from app.models.note import Note
from app.models.user import User
from app.schemas.ai_job import AIJobOut, GenerateSummaryJobOut, IndexNoteJobOut
from app.schemas.quiz import QuizGenerationRequest
from app.services.membership import is_plus
from app.services import n8n_client
from app.services.ai_jobs import run_ai_quiz_job, run_ai_summary_job, sample_note_text
from app.services.note_indexing import queue_index_note_job, run_index_note_job

router = APIRouter(prefix="/api/v1", tags=["ai jobs"])


@router.post(
    "/notes/{note_id}/index",
    response_model=IndexNoteJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def index_note(
    note_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IndexNoteJobOut:
    note = db.scalar(select(Note).where(Note.id == note_id, Note.user_id == current_user.id))
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found.")

    active = db.scalar(
        select(AIJob).where(
            AIJob.user_id == current_user.id,
            AIJob.job_type == "index_note",
            AIJob.note_id == note.id,
            AIJob.status.in_(("pending", "running")),
        )
    )
    if active is not None:
        return IndexNoteJobOut(job_id=active.id, status=active.status)
    if note.index_status == "indexed":
        raise HTTPException(status_code=409, detail="Note is already indexed.")
    if not n8n_client.is_configured():
        raise HTTPException(status_code=503, detail="Note indexing service is not configured.")

    job = queue_index_note_job(db, current_user.id, note.id, background_tasks)
    if job is None:
        raise HTTPException(status_code=503, detail="Note indexing job could not be created.")
    return IndexNoteJobOut(job_id=job.id, status=job.status)


@router.post(
    "/notes/{note_id}/summaries/generate",
    response_model=GenerateSummaryJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_note_summaries(
    note_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GenerateSummaryJobOut:
    note = db.scalar(
        select(Note).where(Note.id == note_id, Note.user_id == current_user.id)
    )
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found.")

    if not n8n_client.is_configured():
        raise HTTPException(status_code=503, detail="AI summary service is not configured.")

    settings = get_settings()
    if not sample_note_text(note.extracted_text, settings.ai_max_input_chars):
        raise HTTPException(status_code=422, detail="Note text exceeds the AI input limit.")

    active = db.scalar(
        select(AIJob).where(
            AIJob.user_id == current_user.id,
            AIJob.job_type == "summarize_note",
            AIJob.note_id == note.id,
            AIJob.status.in_(("pending", "running")),
        )
    )
    if active is not None:
        return GenerateSummaryJobOut(job_id=active.id, status=active.status)

    plus_user = is_plus(db, current_user.id)
    daily_limit = settings.ai_daily_limit_plus if plus_user else settings.ai_daily_limit_free
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_count = db.scalar(
        select(func.count(AIJob.id)).where(
            AIJob.user_id == current_user.id,
            AIJob.job_type.in_(("summarize_note", "generate_quiz")),
            AIJob.created_at >= since,
            AIJob.status != "failed",
        )
    )
    if recent_count >= daily_limit:
        raise HTTPException(status_code=429, detail="Daily AI summary limit reached.")

    job = AIJob(
        user_id=current_user.id,
        job_type="summarize_note",
        note_id=note.id,
        status="pending",
    )
    db.add(job)
    try:
        db.commit()
        db.refresh(job)
    except IntegrityError:
        db.rollback()
        active = db.scalar(
            select(AIJob).where(
                AIJob.user_id == current_user.id,
                AIJob.job_type == "summarize_note",
                AIJob.note_id == note.id,
                AIJob.status.in_(("pending", "running")),
            )
        )
        if active is None:
            raise
        return GenerateSummaryJobOut(job_id=active.id, status=active.status)

    background_tasks.add_task(run_ai_summary_job, job.id, current_user.id)
    return GenerateSummaryJobOut(job_id=job.id, status="pending")


@router.post(
    "/notes/{note_id}/quizzes/generate",
    response_model=GenerateSummaryJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_note_quiz(
    note_id: uuid.UUID,
    payload: QuizGenerationRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> GenerateSummaryJobOut:
    note = db.scalar(select(Note).where(Note.id == note_id, Note.user_id == current_user.id))
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found.")

    if not n8n_client.is_configured():
        raise HTTPException(status_code=503, detail="AI quiz service is not configured.")

    settings = get_settings()
    if not sample_note_text(note.extracted_text, settings.ai_max_input_chars):
        raise HTTPException(status_code=422, detail="Note text exceeds the AI input limit.")

    active = db.scalar(
        select(AIJob).where(
            AIJob.user_id == current_user.id,
            AIJob.job_type == "generate_quiz",
            AIJob.note_id == note.id,
            AIJob.status.in_(("pending", "running")),
        )
    )
    if active is not None:
        return GenerateSummaryJobOut(job_id=active.id, status=active.status)

    plus_user = is_plus(db, current_user.id)
    daily_limit = settings.ai_daily_limit_plus if plus_user else settings.ai_daily_limit_free
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_count = db.scalar(
        select(func.count(AIJob.id)).where(
            AIJob.user_id == current_user.id,
            AIJob.job_type.in_(("summarize_note", "generate_quiz")),
            AIJob.created_at >= since,
            AIJob.status != "failed",
        )
    ) or 0
    if recent_count >= daily_limit:
        raise HTTPException(status_code=429, detail="Daily AI generation limit reached.")

    job = AIJob(
        user_id=current_user.id,
        job_type="generate_quiz",
        note_id=note.id,
        status="pending",
    )
    db.add(job)
    try:
        db.commit()
        db.refresh(job)
    except IntegrityError:
        db.rollback()
        active = db.scalar(
            select(AIJob).where(
                AIJob.user_id == current_user.id,
                AIJob.job_type == "generate_quiz",
                AIJob.note_id == note.id,
                AIJob.status.in_(("pending", "running")),
            )
        )
        if active is None:
            raise
        return GenerateSummaryJobOut(job_id=active.id, status=active.status)

    background_tasks.add_task(run_ai_quiz_job, job.id, current_user.id, payload.question_count)
    return GenerateSummaryJobOut(job_id=job.id, status="pending")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@router.get("/ai-jobs/{job_id}", response_model=AIJobOut)
def get_ai_job(
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AIJobOut:
    job = db.scalar(
        select(AIJob).where(AIJob.id == job_id, AIJob.user_id == current_user.id)
    )
    if job is None:
        raise HTTPException(status_code=404, detail="AI job not found.")

    now = datetime.now(timezone.utc)
    settings = get_settings()
    is_stale_running = (
        job.status == "running"
        and job.started_at is not None
        and _utc(job.started_at) + timedelta(seconds=settings.n8n_timeout_seconds + 30) < now
    )
    is_stale_pending = (
        job.status == "pending"
        and _utc(job.created_at) + timedelta(seconds=60) < now
    )
    if is_stale_running or is_stale_pending:
        job.status = "failed"
        job.error_code = "timeout"
        job.finished_at = now
        if job.job_type == "index_note" and job.note_id is not None:
            note = db.scalar(
                select(Note).where(Note.id == job.note_id, Note.user_id == current_user.id)
            )
            if note is not None:
                note.index_status = "failed"
        db.commit()
        db.refresh(job)
    return AIJobOut(
        job_id=job.id,
        conversation_id=job.conversation_id,
        result_id=job.result_id,
        status=job.status,
        error_code=job.error_code,
        result_count=job.result_count,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
