from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import get_settings


def normalize_quiz_title(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Quiz title must be text.")
    title = value.strip()
    if not 1 <= len(title) <= 200:
        raise ValueError("Quiz title must contain 1 to 200 characters.")
    return title


def validate_quiz_questions(questions: list[object]) -> list["QuizQuestionInput"]:
    settings = get_settings()
    if not settings.quiz_questions_min_count <= len(questions) <= settings.quiz_questions_max_count:
        raise ValueError(
            f"A quiz must contain {settings.quiz_questions_min_count} to "
            f"{settings.quiz_questions_max_count} questions."
        )
    return [QuizQuestionInput.model_validate(question) for question in questions]


class QuizQuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_text: str
    options: list[str]
    correct_index: int = Field(ge=0)
    explanation: str | None = None

    @field_validator("question_text", mode="before")
    @classmethod
    def validate_question_text(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Question text must be text.")
        text = value.strip()
        if not text:
            raise ValueError("Question text cannot be blank.")
        if len(text) > get_settings().quiz_question_text_max_chars:
            raise ValueError(
                f"Question text cannot exceed {get_settings().quiz_question_text_max_chars} characters."
            )
        return text

    @field_validator("options", mode="before")
    @classmethod
    def validate_options(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            raise ValueError("Options must be a list of text choices.")
        settings = get_settings()
        if not settings.quiz_options_min_count <= len(value) <= settings.quiz_options_max_count:
            raise ValueError(
                f"Provide {settings.quiz_options_min_count} to {settings.quiz_options_max_count} options."
            )
        options: list[str] = []
        for option in value:
            if not isinstance(option, str):
                raise ValueError("Each option must be text.")
            trimmed = option.strip()
            if not trimmed:
                raise ValueError("Options cannot be blank.")
            if len(trimmed) > settings.quiz_option_max_chars:
                raise ValueError(f"An option cannot exceed {settings.quiz_option_max_chars} characters.")
            options.append(trimmed)
        return options

    @field_validator("explanation")
    @classmethod
    def validate_explanation(cls, value: str | None) -> str | None:
        if value is not None and len(value) > get_settings().quiz_explanation_max_chars:
            raise ValueError(
                f"Explanation cannot exceed {get_settings().quiz_explanation_max_chars} characters."
            )
        return value

    @model_validator(mode="after")
    def correct_index_must_fit_options(self) -> "QuizQuestionInput":
        if self.correct_index >= len(self.options):
            raise ValueError("correct_index must refer to one of the provided options.")
        return self


class QuizCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: UUID
    note_id: UUID | None = None
    title: str
    questions: list[QuizQuestionInput]

    @field_validator("title", mode="before")
    @classmethod
    def validate_title(cls, value: object) -> str:
        return normalize_quiz_title(value)

    @field_validator("questions")
    @classmethod
    def validate_question_count(cls, value: list[QuizQuestionInput]) -> list[QuizQuestionInput]:
        settings = get_settings()
        if not settings.quiz_questions_min_count <= len(value) <= settings.quiz_questions_max_count:
            raise ValueError(
                f"A quiz must contain {settings.quiz_questions_min_count} to "
                f"{settings.quiz_questions_max_count} questions."
            )
        return value


class QuizGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_count: int = Field(default=5, strict=True, ge=3, le=10)


class QuizOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    category_id: UUID
    note_id: UUID | None
    title: str
    source: str
    created_at: datetime


class QuizListOut(QuizOut):
    question_count: int


class QuizQuestionPublicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    position: int
    question_text: str
    options: list[str]
    created_at: datetime


class QuizDetailOut(QuizOut):
    questions: list[QuizQuestionPublicOut]
