import uuid
from collections.abc import Sequence

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Note
from app.models.summary import Summary
from app.schemas.summary import normalize_summary_content


def create_summaries_bulk(
    db: Session,
    user_id: uuid.UUID,
    note_id: uuid.UUID,
    contents: Sequence[str],
    source: str,
    *,
    commit: bool = True,
) -> list[Summary]:
    if len(contents) > 50:
        raise ValueError("A maximum of 50 summaries can be created at once.")
    if source not in {"ai", "manual"}:
        raise ValueError("Source must be 'ai' or 'manual'.")
    normalized_contents = [normalize_summary_content(content) for content in contents]

    note = db.scalar(select(Note).where(Note.id == note_id, Note.user_id == user_id))
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found.")

    summaries = [
        Summary(user_id=user_id, note_id=note_id, content=content, position=position, source=source)
        for position, content in enumerate(normalized_contents)
    ]
    try:
        db.add_all(summaries)
        if commit:
            db.commit()
            for summary in summaries:
                db.refresh(summary)
        else:
            db.flush()
    except Exception:
        if commit:
            db.rollback()
        raise
    return summaries
