import uuid
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.ai_job import AIJob
from app.models.note import Note
from app.services import n8n_client

INDEX_ERROR_CODES = {
    "n8n_unreachable",
    "n8n_timeout",
    "n8n_bad_response",
    "n8n_error",
    "timeout",
    "too_large",
}


def queue_index_note_job(
    db: Session,
    user_id: uuid.UUID,
    note_id: uuid.UUID,
    background_tasks: BackgroundTasks,
) -> AIJob | None:
    """Create and schedule an index job when n8n is configured; best effort for uploads."""
    if not n8n_client.is_configured():
        return None

    active = db.scalar(
        select(AIJob).where(
            AIJob.user_id == user_id,
            AIJob.job_type == "index_note",
            AIJob.note_id == note_id,
            AIJob.status.in_(("pending", "running")),
        )
    )
    if active is not None:
        return active

    note = db.scalar(select(Note).where(Note.id == note_id, Note.user_id == user_id))
    if note is None:
        return None
    note.index_status = "pending"
    job = AIJob(user_id=user_id, job_type="index_note", note_id=note_id, status="pending")
    db.add(job)
    try:
        db.commit()
        db.refresh(job)
    except IntegrityError:
        db.rollback()
        active = db.scalar(
            select(AIJob).where(
                AIJob.user_id == user_id,
                AIJob.job_type == "index_note",
                AIJob.note_id == note_id,
                AIJob.status.in_(("pending", "running")),
            )
        )
        return active
    except Exception:
        db.rollback()
        return None

    background_tasks.add_task(run_index_note_job, job.id, user_id)
    return job


def _fail_index_job(db: Session, job_id: uuid.UUID, user_id: uuid.UUID, error_code: str) -> None:
    if error_code not in INDEX_ERROR_CODES:
        error_code = "n8n_error"
    job = db.scalar(
        select(AIJob).where(
            AIJob.id == job_id,
            AIJob.user_id == user_id,
            AIJob.job_type == "index_note",
        )
    )
    if job is None or job.status not in {"pending", "running"}:
        return
    if job.note_id is not None:
        note = db.scalar(select(Note).where(Note.id == job.note_id, Note.user_id == user_id))
        if note is not None:
            note.index_status = "failed"
    job.status = "failed"
    job.error_code = error_code
    job.finished_at = datetime.now(timezone.utc)
    db.commit()


def _validate_index_result(result: dict, job_id: uuid.UUID) -> int:
    chunk_count = result.get("chunk_count")
    if (
        result.get("job_id") != str(job_id)
        or isinstance(chunk_count, bool)
        or not isinstance(chunk_count, int)
        or not 1 <= chunk_count <= 2000
    ):
        raise n8n_client.N8NBadResponseError
    return chunk_count


def run_index_note_job(job_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Run indexing with a worker-owned database session."""
    db = SessionLocal()
    try:
        job = db.scalar(
            select(AIJob).where(
                AIJob.id == job_id,
                AIJob.user_id == user_id,
                AIJob.job_type == "index_note",
                AIJob.status == "pending",
            )
        )
        if job is None:
            return

        note_id = job.note_id
        note = None
        if note_id is not None:
            note = db.scalar(select(Note).where(Note.id == note_id, Note.user_id == user_id))
        text = note.extracted_text if note is not None else None
        category_id = note.category_id if note is not None else None
        job.status = "running"
        job.started_at = func.now()
        db.commit()

        if text is None or note_id is None or category_id is None:
            _fail_index_job(db, job_id, user_id, "n8n_error")
            return

        settings = get_settings()
        if len(text) > settings.index_max_input_chars:
            _fail_index_job(db, job_id, user_id, "too_large")
            return

        payload = {
            "job_id": str(job_id),
            "text": text,
            "user_id": str(user_id),
            "note_id": str(note_id),
            "category_id": str(category_id),
        }
        try:
            result = n8n_client.post_webhook("index-note", payload)
            chunk_count = _validate_index_result(result, job_id)
        except n8n_client.N8NBadResponseError:
            _fail_index_job(db, job_id, user_id, "n8n_bad_response")
            return
        except httpx.TimeoutException:
            _fail_index_job(db, job_id, user_id, "n8n_timeout")
            return
        except httpx.ConnectError:
            _fail_index_job(db, job_id, user_id, "n8n_unreachable")
            return
        except httpx.HTTPStatusError:
            _fail_index_job(db, job_id, user_id, "n8n_error")
            return
        except httpx.RequestError:
            _fail_index_job(db, job_id, user_id, "n8n_unreachable")
            return
        except Exception:
            _fail_index_job(db, job_id, user_id, "n8n_error")
            return

        try:
            current_job = db.scalar(
                select(AIJob).where(
                    AIJob.id == job_id,
                    AIJob.user_id == user_id,
                    AIJob.job_type == "index_note",
                )
            )
            current_note = db.scalar(
                select(Note).where(Note.id == note_id, Note.user_id == user_id)
            )
            if current_job is None or current_job.status != "running" or current_note is None:
                db.rollback()
                return
            now = datetime.now(timezone.utc)
            current_note.index_status = "indexed"
            current_note.indexed_at = now
            current_note.chunk_count = chunk_count
            current_job.status = "done"
            current_job.error_code = None
            current_job.finished_at = now
            db.commit()
        except Exception:
            db.rollback()
            _fail_index_job(db, job_id, user_id, "n8n_error")
    finally:
        db.close()
