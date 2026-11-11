from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class GenerateSummaryJobOut(BaseModel):
    job_id: UUID
    status: Literal["pending", "running"]


class IndexNoteJobOut(BaseModel):
    job_id: UUID
    status: Literal["pending", "running"]


class AIJobOut(BaseModel):
    job_id: UUID
    conversation_id: UUID | None
    result_id: UUID | None
    status: Literal["pending", "running", "done", "failed"]
    error_code: str | None
    result_count: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
