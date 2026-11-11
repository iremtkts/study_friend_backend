from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class CategoryUpdate(CategoryCreate):
    pass


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime


class NoteListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category_id: UUID
    title: str | None
    original_filename: str
    file_type: str
    file_size_bytes: int
    index_status: str
    created_at: datetime


class NoteDetailOut(NoteListOut):
    extracted_text: str


class MessageOut(BaseModel):
    message: str
