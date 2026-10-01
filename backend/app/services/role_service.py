import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.core import permissions
from app.models.user import User

logger = logging.getLogger(__name__)


def _emails(raw: str) -> set[str]:
    return {e.strip().lower() for e in (raw or "").split(",") if e.strip()}


def bootstrap_role_for(email: str) -> str:
    """Role a login email gets from configuration (SUPER_ADMIN_EMAILS /
    ADMIN_EMAILS), else a plain team member."""
    email = (email or "").strip().lower()
    if email in _emails(settings.super_admin_emails):
        return permissions.SUPER_ADMIN
    if email in _emails(settings.admin_emails):
        return permissions.ADMIN
    return permissions.MEMBER


def sync_configured_roles(db: Session) -> None:
    """Promotes (never demotes) accounts whose email is listed in the config."""
    rank = {permissions.MEMBER: 0, permissions.ADMIN: 1, permissions.SUPER_ADMIN: 2}
    # Only one super admin may exist: a configured super-admin email is promoted
    # only while nobody holds the role yet.
    super_exists = db.query(User).filter(User.role == permissions.SUPER_ADMIN).first() is not None
    for user in db.query(User).all():
        wanted = bootstrap_role_for(user.email)
        if wanted == permissions.SUPER_ADMIN and super_exists and user.role != permissions.SUPER_ADMIN:
            wanted = permissions.ADMIN
        if rank.get(wanted, 0) > rank.get(user.role, 0):
            user.role = wanted
            if wanted == permissions.SUPER_ADMIN:
                super_exists = True
    db.commit()

    # Lockout guard: with no super admin at all nobody could change settings
    # or roles, so the oldest account (the original login) becomes one. Change
    # roles on the Team page afterwards.
    if not db.query(User).filter(User.role == permissions.SUPER_ADMIN).first():
        oldest = db.query(User).order_by(User.created_at.asc()).first()
        if oldest:
            oldest.role = permissions.SUPER_ADMIN
            db.commit()
            logger.warning("No super admin existed; promoted the oldest account %s", oldest.email)
