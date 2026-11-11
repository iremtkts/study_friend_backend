import re
import uuid
from datetime import datetime, timezone

import httpx
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.ai_job import AIJob
from app.models.category import Category
from app.models.conversation import Conversation, ConversationMessage
from app.models.note import Note
from app.models.summary import Summary
from app.schemas.quiz import QuizQuestionInput
from app.schemas.summary import normalize_summary_content
from app.services import n8n_client
from app.services.conversations import get_recent_messages
from app.services.summaries import create_summaries_bulk
from app.services.quizzes import create_quiz_with_questions

ERROR_CODES = {"n8n_unreachable", "n8n_timeout", "n8n_bad_response", "n8n_error", "timeout", "too_large"}


def sample_note_text(text: str, max_chars: int) -> str:
    if not text.strip():
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 0:
        return ""

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text.strip()) if part.strip()]
    if not paragraphs:
        return ""

    separator = "\n\n" if max_chars >= 4 else "\n" if max_chars >= 3 else ""
    if len(paragraphs) > 1:
        source_length = sum(map(len, paragraphs)) + len(separator) * (len(paragraphs) - 1)
        sample_count = min(
            len(paragraphs),
            max(2, max_chars * len(paragraphs) // source_length),
        )
        while sample_count >= 2:
            indices = [
                round(index * (len(paragraphs) - 1) / (sample_count - 1))
                for index in range(sample_count)
            ]
            chunks = [paragraphs[index] for index in indices]
            sampled = separator.join(chunks)
            if len(sampled) <= max_chars:
                return sampled
            if sample_count == 2:
                break
            sample_count = max(2, sample_count // 2)

    available = max_chars - len(separator)
    if len(paragraphs) == 1:
        first_paragraph = last_paragraph = paragraphs[0]
    else:
        first_paragraph, last_paragraph = paragraphs[0], paragraphs[-1]
    first_size = available // 2
    last_size = available - first_size
    sampled = first_paragraph[:first_size]
    if last_size:
        sampled += separator + last_paragraph[-last_size:]
    return sampled[:max_chars]


def _fail_job(db: Session, job_id: uuid.UUID, user_id: uuid.UUID, error_code: str) -> None:
    if error_code not in ERROR_CODES:
        error_code = "n8n_error"
    job = db.scalar(select(AIJob).where(AIJob.id == job_id, AIJob.user_id == user_id))
    if job is None or job.status not in {"pending", "running"}:
        return
    job.status = "failed"
    job.error_code = error_code
    job.finished_at = datetime.now(timezone.utc)
    db.commit()


def _validate_n8n_result(result: dict, job_id: uuid.UUID) -> tuple[list[str], str | None]:
    settings = get_settings()
    if result.get("job_id") != str(job_id):
        raise n8n_client.N8NBadResponseError
    cards = result.get("cards")
    if not isinstance(cards, list) or not 1 <= len(cards) <= min(settings.ai_summary_max_cards, 50):
        raise n8n_client.N8NBadResponseError
    normalized: list[str] = []
    try:
        for card in cards:
            normalized.append(normalize_summary_content(card))
    except (ValueError, TypeError) as exc:
        raise n8n_client.N8NBadResponseError from exc
    model_name = result.get("model_name")
    if model_name is not None and not isinstance(model_name, str):
        raise n8n_client.N8NBadResponseError
    return normalized, model_name


def run_ai_summary_job(job_id: uuid.UUID, user_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        job = db.scalar(
            select(AIJob).where(
                AIJob.id == job_id,
                AIJob.user_id == user_id,
                AIJob.status == "pending",
            )
        )
        if job is None:
            return
        note_id = job.note_id
        job.status = "running"
        job.started_at = func.now()
        db.commit()

        note = db.scalar(select(Note).where(Note.id == note_id, Note.user_id == user_id))
        if note is None:
            db.commit()
            _fail_job(db, job_id, user_id, "n8n_error")
            return
        text = note.extracted_text
        db.commit()

        settings = get_settings()
        sampled_text = sample_note_text(text, settings.ai_max_input_chars)
        if not sampled_text:
            _fail_job(db, job_id, user_id, "n8n_bad_response")
            return
        payload = {
            "job_id": str(job_id),
            "text": sampled_text,
            "language": settings.ai_output_language,
            "max_cards": settings.ai_summary_max_cards,
            "target_chars_per_card": settings.ai_card_target_chars,
        }
        try:
            result = n8n_client.post_webhook("summarize-note", payload)
            cards, model_name = _validate_n8n_result(result, job_id)
        except n8n_client.N8NBadResponseError:
            _fail_job(db, job_id, user_id, "n8n_bad_response")
            return
        except httpx.TimeoutException:
            _fail_job(db, job_id, user_id, "n8n_timeout")
            return
        except httpx.ConnectError:
            _fail_job(db, job_id, user_id, "n8n_unreachable")
            return
        except httpx.HTTPStatusError:
            _fail_job(db, job_id, user_id, "n8n_error")
            return
        except httpx.RequestError:
            _fail_job(db, job_id, user_id, "n8n_unreachable")
            return
        except Exception:
            _fail_job(db, job_id, user_id, "n8n_error")
            return

        try:
            with db.begin():
                current_job = db.scalar(
                    select(AIJob).where(AIJob.id == job_id, AIJob.user_id == user_id)
                )
                if current_job is None or current_job.status != "running":
                    return
                db.execute(
                    delete(Summary).where(
                        Summary.user_id == user_id,
                        Summary.note_id == note_id,
                        Summary.source == "ai",
                    )
                )
                create_summaries_bulk(
                    db,
                    user_id,
                    note_id,
                    cards,
                    "ai",
                    commit=False,
                )
                current_job.status = "done"
                current_job.error_code = None
                current_job.model_name = model_name
                current_job.result_count = len(cards)
                current_job.finished_at = datetime.now(timezone.utc)
        except Exception:
            db.rollback()
            _fail_job(db, job_id, user_id, "n8n_error")
    finally:
        db.close()


def _validate_quiz_result(
    result: dict, job_id: uuid.UUID, question_count: int
) -> tuple[list[QuizQuestionInput], str | None]:
    if result.get("job_id") != str(job_id):
        raise n8n_client.N8NBadResponseError
    questions = result.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= question_count:
        raise n8n_client.N8NBadResponseError
    model_name = result.get("model_name")
    if model_name is not None and not isinstance(model_name, str):
        raise n8n_client.N8NBadResponseError
    valid_questions: list[QuizQuestionInput] = []
    for question in questions:
        try:
            valid_questions.append(QuizQuestionInput.model_validate(question))
        except (ValidationError, TypeError, ValueError):
            continue
    if not valid_questions:
        raise n8n_client.N8NBadResponseError
    return valid_questions, model_name


def run_ai_quiz_job(job_id: uuid.UUID, user_id: uuid.UUID, question_count: int) -> None:
    db = SessionLocal()
    try:
        job = db.scalar(
            select(AIJob).where(
                AIJob.id == job_id,
                AIJob.user_id == user_id,
                AIJob.job_type == "generate_quiz",
                AIJob.status == "pending",
            )
        )
        if job is None:
            return
        note_id = job.note_id
        job.status = "running"
        job.started_at = func.now()
        db.commit()

        note = db.scalar(select(Note).where(Note.id == note_id, Note.user_id == user_id))
        if note is None:
            _fail_job(db, job_id, user_id, "n8n_error")
            return
        category_id = note.category_id
        text = note.extracted_text

        settings = get_settings()
        sampled_text = sample_note_text(text, settings.ai_max_input_chars)
        if not sampled_text:
            _fail_job(db, job_id, user_id, "n8n_bad_response")
            return
        payload = {
            "job_id": str(job_id),
            "text": sampled_text,
            "question_count": question_count,
            "language": "tr",
        }
        db.commit()
        try:
            result = n8n_client.post_webhook("generate-quiz", payload)
            questions, model_name = _validate_quiz_result(result, job_id, question_count)
        except n8n_client.N8NBadResponseError:
            _fail_job(db, job_id, user_id, "n8n_bad_response")
            return
        except httpx.TimeoutException:
            _fail_job(db, job_id, user_id, "n8n_timeout")
            return
        except httpx.ConnectError:
            _fail_job(db, job_id, user_id, "n8n_unreachable")
            return
        except httpx.HTTPStatusError:
            _fail_job(db, job_id, user_id, "n8n_error")
            return
        except httpx.RequestError:
            _fail_job(db, job_id, user_id, "n8n_unreachable")
            return
        except Exception:
            _fail_job(db, job_id, user_id, "n8n_error")
            return

        try:
            with db.begin():
                current_job = db.scalar(
                    select(AIJob).where(
                        AIJob.id == job_id,
                        AIJob.user_id == user_id,
                        AIJob.job_type == "generate_quiz",
                    )
                )
                current_note = db.scalar(
                    select(Note).where(Note.id == note_id, Note.user_id == user_id)
                )
                if current_job is None or current_job.status != "running" or current_note is None:
                    return
                title = f"{(current_note.title or current_note.original_filename)[:194]} Testi"
                quiz = create_quiz_with_questions(
                    db=db,
                    user_id=user_id,
                    category_id=current_note.category_id,
                    note_id=current_note.id,
                    title=title,
                    questions=questions,
                    source="ai",
                    commit=False,
                )
                db.flush()
                current_job.status = "done"
                current_job.error_code = None
                current_job.model_name = model_name
                current_job.result_id = quiz.id
                current_job.result_count = len(questions)
                current_job.finished_at = datetime.now(timezone.utc)
        except Exception:
            db.rollback()
            _fail_job(db, job_id, user_id, "n8n_error")
    finally:
        db.close()


def _validate_chat_result(
    result: dict, job_id: uuid.UUID, allowed_note_ids: set[str]
) -> tuple[str, list[str], str | None]:
    if result.get("job_id") != str(job_id):
        raise n8n_client.N8NBadResponseError
    answer = result.get("answer")
    if not isinstance(answer, str):
        raise n8n_client.N8NBadResponseError
    answer = answer.strip()
    if not 1 <= len(answer) <= 4000:
        raise n8n_client.N8NBadResponseError
    sources = result.get("sources", [])
    if not isinstance(sources, list) or not all(isinstance(source, str) for source in sources):
        raise n8n_client.N8NBadResponseError
    model_name = result.get("model")
    if model_name is not None and not isinstance(model_name, str):
        raise n8n_client.N8NBadResponseError
    return answer, [source for source in sources if source in allowed_note_ids], model_name


def run_ai_chat_job(job_id: uuid.UUID, user_id: uuid.UUID, message_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        job = db.scalar(
            select(AIJob).where(
                AIJob.id == job_id,
                AIJob.user_id == user_id,
                AIJob.job_type == "chat_message",
                AIJob.status == "pending",
            )
        )
        if job is None:
            return
        if job.conversation_id is None:
            _fail_job(db, job_id, user_id, "n8n_error")
            return
        conversation_id = job.conversation_id
        job.status = "running"
        job.started_at = func.now()
        db.commit()

        conversation = db.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        message = db.scalar(
            select(ConversationMessage)
            .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
            .where(
                ConversationMessage.id == message_id,
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.role == "user",
                Conversation.user_id == user_id,
            )
        )
        if conversation is None or message is None:
            _fail_job(db, job_id, user_id, "n8n_error")
            return

        settings = get_settings()
        notes_statement = (
            select(Note.id)
            .join(Category, Category.id == Note.category_id)
            .where(
                Note.user_id == user_id,
                Note.category_id == conversation.category_id,
                Note.index_status == "indexed",
                Category.user_id == user_id,
            )
        )
        if conversation.note_id is not None:
            notes_statement = notes_statement.where(Note.id == conversation.note_id)
        allowed_note_ids = {
            str(note_id)
            for note_id in db.scalars(
                notes_statement.order_by(Note.created_at, Note.id).limit(settings.chat_max_note_ids)
            ).all()
        }
        history = get_recent_messages(
            db,
            conversation_id,
            settings.chat_history_limit,
            user_id=user_id,
            exclude_message_id=message_id,
        )
        payload = {
            "job_id": str(job_id),
            "question": message.content,
            "history": [{"role": item.role, "content": item.content} for item in history],
            "user_id": str(user_id),
            "category_id": str(conversation.category_id),
            "note_ids": sorted(allowed_note_ids),
            "language": "tr",
            "top_k": settings.chat_top_k,
        }
        db.commit()
        try:
            result = n8n_client.post_webhook("chat", payload)
            answer, sources, model_name = _validate_chat_result(result, job_id, allowed_note_ids)
        except n8n_client.N8NBadResponseError:
            _fail_job(db, job_id, user_id, "n8n_bad_response")
            return
        except httpx.TimeoutException:
            _fail_job(db, job_id, user_id, "n8n_timeout")
            return
        except httpx.ConnectError:
            _fail_job(db, job_id, user_id, "n8n_unreachable")
            return
        except httpx.HTTPStatusError:
            _fail_job(db, job_id, user_id, "n8n_error")
            return
        except httpx.RequestError:
            _fail_job(db, job_id, user_id, "n8n_unreachable")
            return
        except Exception:
            _fail_job(db, job_id, user_id, "n8n_error")
            return

        try:
            with db.begin():
                current_job = db.scalar(
                    select(AIJob).where(
                        AIJob.id == job_id,
                        AIJob.user_id == user_id,
                        AIJob.job_type == "chat_message",
                    )
                )
                current_conversation = db.scalar(
                    select(Conversation).where(
                        Conversation.id == conversation_id,
                        Conversation.user_id == user_id,
                    )
                )
                if (
                    current_job is None
                    or current_job.status != "running"
                    or current_conversation is None
                ):
                    return
                db.add(
                    ConversationMessage(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=answer,
                        model_name=model_name,
                        sources=sources,
                        created_at=datetime.now(timezone.utc),
                    )
                )
                current_conversation.updated_at = func.now()
                current_job.status = "done"
                current_job.error_code = None
                current_job.model_name = model_name
                current_job.result_count = 1
                current_job.finished_at = datetime.now(timezone.utc)
        except Exception:
            db.rollback()
            _fail_job(db, job_id, user_id, "n8n_error")
    finally:
        db.close()
