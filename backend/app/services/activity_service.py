"""Activity log writer. Uses its own DB session so a log write can never
break, roll back or be rolled back by the request it describes, and works
from background job threads."""

import logging
import uuid

from app.db.session import SessionLocal
from app.models.activity_log import ActivityLog
from app.models.user import User

logger = logging.getLogger(__name__)


def log_activity(
    user: User | None, action: str, client=None, *, client_id: uuid.UUID | None = None,
    client_name: str | None = None, **detail,
) -> None:
    try:
        db = SessionLocal()
        try:
            db.add(ActivityLog(
                user_id=user.id if user else None,
                user_name=(user.full_name if user else "") or "",
                user_email=(user.email if user else "") or "",
                user_role=(user.role if user else "") or "",
                action=action,
                client_id=client.id if client is not None else client_id,
                client_name=client.name if client is not None else client_name,
                detail={k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in detail.items()} or None,
            ))
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.warning("Could not write activity log entry %r", action, exc_info=True)
