from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Membership


def is_plus(db: Session, user_id: UUID) -> bool:
    active_plus_membership = db.scalar(
        select(Membership.id)
        .where(
            Membership.user_id == user_id,
            Membership.plan == "plus",
            Membership.status == "active",
            or_(Membership.expires_at.is_(None), Membership.expires_at > func.now()),
        )
        .limit(1)
    )
    return active_plus_membership is not None
