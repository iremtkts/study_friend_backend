from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.study_plan import StudyPlanItemCreate


class AnswerSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: UUID
    selected_index: int | None = Field(default=None, ge=0)


class AttemptSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[AnswerSubmission]


class AttemptInProgressOut(BaseModel):
    id: UUID
    quiz_id: UUID
    status: Literal["in_progress"]
    started_at: datetime


class AttemptQuestionResultOut(BaseModel):
    question_id: UUID
    question_text: str
    selected_index: int | None
    correct_index: int
    is_correct: bool
    explanation: str | None


class AttemptCompletedOut(BaseModel):
    id: UUID
    quiz_id: UUID
    status: Literal["completed"]
    started_at: datetime
    completed_at: datetime
    correct_count: int
    total_questions: int
    score_percent: Decimal
    answers: list[AttemptQuestionResultOut]


class AttemptListItemOut(BaseModel):
    id: UUID
    quiz_id: UUID
    quiz_title: str
    score_percent: Decimal
    completed_at: datetime


class RecentAttemptOut(BaseModel):
    attempted_at: datetime
    quiz_title: str
    score_percent: Decimal


class CategoryStatsOut(BaseModel):
    category_id: UUID
    category_name: str
    attempt_count: int
    average_score: Decimal | None


class QuizStatsOut(BaseModel):
    total_attempts: int
    average_score: Decimal | None
    highest_score: Decimal | None
    recent_attempts: list[RecentAttemptOut]
    categories: list[CategoryStatsOut] | None = None


class AttemptRecommendationSummary(BaseModel):
    score_percent: Decimal
    correct_count: int
    total_questions: int
    wrong_count: int


class RelatedSummaryOut(BaseModel):
    id: UUID
    content: str


class WeakTopicOut(BaseModel):
    question_id: UUID
    question_text: str
    selected_option_text: str | None
    correct_option_text: str
    explanation: str | None
    related_summaries: list[RelatedSummaryOut]


class SuggestedPlanOut(BaseModel):
    week_start: date
    items: list[StudyPlanItemCreate]


class AttemptRecommendationOut(BaseModel):
    summary: AttemptRecommendationSummary
    weak_topics: list[WeakTopicOut]
    suggested_plan: SuggestedPlanOut
    message: str
