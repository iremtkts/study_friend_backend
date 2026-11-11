import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models.ai_job import AIJob
from app.models import Category, Note, User
from app.models.conversation import Conversation, ConversationMessage
from app.schemas.conversation import (
    ChatJobOut,
    ChatMessageCreate,
    ConversationCreate,
    ConversationDetailOut,
    ConversationOut,
    ConversationUpdate,
    MessageCreate,
    MessageOut,
)
from app.services import n8n_client
from app.services.ai_jobs import run_ai_chat_job
from app.services.membership import is_plus

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


def _get_conversation(db: Session, conversation_id: uuid.UUID, user_id: uuid.UUID) -> Conversation:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationOut:
    category = db.scalar(
        select(Category).where(Category.id == payload.category_id, Category.user_id == current_user.id)
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")
    if payload.note_id is not None:
        note = db.scalar(
            select(Note).where(
                Note.id == payload.note_id,
                Note.user_id == current_user.id,
                Note.category_id == category.id,
            )
        )
        if note is None:
            raise HTTPException(status_code=404, detail="Note not found.")

    conversation = Conversation(
        user_id=current_user.id,
        category_id=category.id,
        note_id=payload.note_id,
        title=payload.title,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.get("", response_model=list[ConversationOut])
def list_conversations(
    category_id: uuid.UUID = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ConversationOut]:
    category = db.scalar(
        select(Category.id).where(Category.id == category_id, Category.user_id == current_user.id)
    )
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")
    conversations = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == current_user.id, Conversation.category_id == category_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [ConversationOut.model_validate(item) for item in conversations]


@router.get("/{conversation_id}", response_model=ConversationDetailOut)
def get_conversation(
    conversation_id: uuid.UUID,
    messages_limit: int = Query(default=50, ge=1, le=100),
    messages_offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationDetailOut:
    conversation = _get_conversation(db, conversation_id, current_user.id)
    messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation.id)
        .order_by(ConversationMessage.created_at, ConversationMessage.id)
        .limit(messages_limit)
        .offset(messages_offset)
    ).all()
    return ConversationDetailOut(
        **ConversationOut.model_validate(conversation).model_dump(),
        messages=[MessageOut.model_validate(message) for message in messages],
        messages_limit=messages_limit,
        messages_offset=messages_offset,
    )


@router.patch("/{conversation_id}", response_model=ConversationOut)
def update_conversation(
    conversation_id: uuid.UUID,
    payload: ConversationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationOut:
    conversation = _get_conversation(db, conversation_id, current_user.id)
    conversation.title = payload.title
    db.commit()
    db.refresh(conversation)
    return ConversationOut.model_validate(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    conversation = _get_conversation(db, conversation_id, current_user.id)
    db.delete(conversation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def create_message(
    conversation_id: uuid.UUID,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MessageOut:
    conversation = _get_conversation(db, conversation_id, current_user.id)
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=payload.content,
    )
    conversation.updated_at = func.now()
    db.add(message)
    db.commit()
    db.refresh(message)
    return MessageOut.model_validate(message)


@router.post(
    "/{conversation_id}/chat",
    response_model=ChatJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_chat_message(
    conversation_id: uuid.UUID,
    payload: ChatMessageCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ChatJobOut:
    conversation = _get_conversation(db, conversation_id, current_user.id)
    if not n8n_client.is_configured():
        raise HTTPException(status_code=503, detail="Chat service is not configured.")

    active = db.scalar(
        select(AIJob).where(
            AIJob.user_id == current_user.id,
            AIJob.conversation_id == conversation.id,
            AIJob.job_type == "chat_message",
            AIJob.status.in_(("pending", "running")),
        )
    )
    if active is not None:
        raise HTTPException(status_code=409, detail="A chat response is already in progress.")

    settings = get_settings()
    plus_user = is_plus(db, current_user.id)
    daily_limit = (
        settings.ai_chat_daily_limit_plus if plus_user else settings.ai_chat_daily_limit_free
    )
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_count = db.scalar(
        select(func.count(AIJob.id)).where(
            AIJob.user_id == current_user.id,
            AIJob.job_type == "chat_message",
            AIJob.created_at >= since,
            AIJob.status != "failed",
        )
    ) or 0
    if recent_count >= daily_limit:
        raise HTTPException(status_code=429, detail="Daily chat limit reached.")

    message = ConversationMessage(
        conversation_id=conversation.id,
        role="user",
        content=payload.content,
    )
    if not conversation.title or not conversation.title.strip():
        conversation.title = payload.content[:60]
    job = AIJob(
        user_id=current_user.id,
        job_type="chat_message",
        conversation_id=conversation.id,
        status="pending",
    )
    conversation.updated_at = func.now()
    db.add_all([message, job])
    try:
        db.commit()
        db.refresh(message)
        db.refresh(job)
    except IntegrityError:
        db.rollback()
        active = db.scalar(
            select(AIJob).where(
                AIJob.user_id == current_user.id,
                AIJob.conversation_id == conversation.id,
                AIJob.job_type == "chat_message",
                AIJob.status.in_(("pending", "running")),
            )
        )
        if active is None:
            raise
        raise HTTPException(status_code=409, detail="A chat response is already in progress.")

    background_tasks.add_task(run_ai_chat_job, job.id, current_user.id, message.id)
    return ChatJobOut(job_id=job.id, status="pending", message_id=message.id)
