from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models import RefreshToken, User
from app.schemas.auth import MessageResponse

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.delete("/me", response_model=MessageResponse)
def deactivate_current_user(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    now = datetime.now(timezone.utc)
    user.is_active = False
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.commit()
    return MessageResponse(message="Account deactivated")
