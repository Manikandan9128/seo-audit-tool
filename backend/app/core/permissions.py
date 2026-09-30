"""Roles (2026-09-30): super_admin has every authority, admin has everything
except settings/API keys and changing roles, member can only process (upload,
generate, preview, download) and delete their own upload until a report has
been generated with it."""

from app.models.user import User

SUPER_ADMIN = "super_admin"
ADMIN = "admin"
MEMBER = "member"
ROLES = (SUPER_ADMIN, ADMIN, MEMBER)


def is_admin_or_above(user: User) -> bool:
    return user.role in (SUPER_ADMIN, ADMIN)


def can_delete_upload(user: User, record, db) -> bool:
    """Admin and super admin: always. Team member: only a file they uploaded
    themselves, and only until a report has been generated for that client
    after the upload (once a report used it, an admin must remove it)."""
    if is_admin_or_above(user):
        return True
    if record.uploaded_by_user_id != user.id:
        return False
    from app.models.report_generation_job import ReportGenerationJob

    used = (
        db.query(ReportGenerationJob.id)
        .filter(ReportGenerationJob.client_id == record.client_id, ReportGenerationJob.created_at >= record.created_at)
        .first()
    )
    return used is None
