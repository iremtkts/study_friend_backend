import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversation import Conversation, ConversationMessage


def get_recent_messages(
    db: Session,
    conversation_id: uuid.UUID,
    limit: int = 20,
    *,
    user_id: uuid.UUID,
    exclude_message_id: uuid.UUID | None = None,
) -> list[ConversationMessage]:
    """Return the most recent messages in chronological order."""
    statement = (
        select(ConversationMessage)
        .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
        .where(
            ConversationMessage.conversation_id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if exclude_message_id is not None:
        statement = statement.where(ConversationMessage.id != exclude_message_id)
    recent = db.scalars(
        statement
        .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
        .limit(limit)
    ).all()
    return list(reversed(recent))
