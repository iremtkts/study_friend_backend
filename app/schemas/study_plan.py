from datetime import date, datetime, time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _trim_topic(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Topic must be text.")
    topic = value.strip()
    if not 1 <= len(topic) <= 300:
        raise ValueError("Topic must contain 1 to 300 characters.")
    return topic


def _validate_week_start(value: date) -> date:
    if value.weekday() != 0:
        raise ValueError("week_start must be a Monday.")
    return value


class StudyPlanItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_of_week: int = Field(ge=1, le=7)
    start_time: time | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    topic: str
    category_id: UUID | None = None
    note_id: UUID | None = None

    @field_validator("topic", mode="before")
    @classmethod
    def validate_topic(cls, value: object) -> str:
        return _trim_topic(value)


class StudyPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: date
    title: str | None = Field(default=None, max_length=200)
    items: list[StudyPlanItemCreate] = Field(default_factory=list, max_length=100)

    @field_validator("week_start")
    @classmethod
    def validate_weekday(cls, value: date) -> date:
        return _validate_week_start(value)


class StudyPlanItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_of_week: int | None = Field(default=None, ge=1, le=7)
    start_time: time | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    topic: str | None = None
    category_id: UUID | None = None
    note_id: UUID | None = None
    is_completed: bool | None = None

    @field_validator("is_completed", mode="before")
    @classmethod
    def completion_cannot_be_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("is_completed cannot be null.")
        return value

    @field_validator("day_of_week", mode="before")
    @classmethod
    def day_cannot_be_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("day_of_week cannot be null.")
        return value

    @field_validator("topic", mode="before")
    @classmethod
    def validate_topic(cls, value: object) -> str | None:
        return _trim_topic(value)


class StudyPlanItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    day_of_week: int
    start_time: time | None
    duration_minutes: int | None
    topic: str
    category_id: UUID | None
    note_id: UUID | None
    is_completed: bool
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StudyPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    week_start: date
    title: str | None
    source: str
    created_at: datetime
    updated_at: datetime


class StudyPlanProgressOut(BaseModel):
    total_items: int
    completed_items: int
    progress_percent: float


class StudyPlanDetailOut(StudyPlanOut):
    items: list[StudyPlanItemOut]
    progress: StudyPlanProgressOut
