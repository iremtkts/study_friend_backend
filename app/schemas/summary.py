from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.config import get_settings


def normalize_summary_content(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Summary content must be text.")
    content = value.strip()
    if not content:
        raise ValueError("Summary content cannot be blank.")
    if len(content) > get_settings().summary_max_chars:
        raise ValueError(f"Summary content cannot exceed {get_settings().summary_max_chars} characters.")
    return content


class SummaryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str

    @field_validator("content", mode="before")
    @classmethod
    def validate_content(cls, value: object) -> str:
        return normalize_summary_content(value)


class SummaryUpdate(SummaryCreate):
    pass


class SummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    note_id: UUID
    content: str
    position: int
    source: str
    created_at: datetime
    updated_at: datetime
