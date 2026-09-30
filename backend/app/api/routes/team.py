import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_admin, require_super_admin
from app.core import permissions
from app.models.activity_log import ActivityLog
from app.models.user import User
from app.services.activity_service import log_activity

router = APIRouter(tags=["team"])


class RoleUpdate(BaseModel):
    role: str


@router.get("/users")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    users = db.query(User).order_by(User.created_at.asc()).all()
    return [
        {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role, "created_at": u.created_at}
        for u in users
    ]


@router.patch("/users/{user_id}/role")
def set_user_role(
    user_id: uuid.UUID, payload: RoleUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_super_admin),
):
    if payload.role not in permissions.ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {list(permissions.ROLES)}")
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role == permissions.SUPER_ADMIN and payload.role != permissions.SUPER_ADMIN:
        remaining = db.query(User).filter(User.role == permissions.SUPER_ADMIN, User.id != target.id).count()
        if remaining == 0:
            raise HTTPException(status_code=400, detail="There must always be at least one super admin.")
    previous = target.role
    target.role = payload.role
    db.commit()
    log_activity(current_user, "role_changed", target_user=target.email, from_role=previous, to_role=payload.role)
    return {"id": target.id, "role": target.role}


@router.get("/activity")
def list_activity(
    client_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    action: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    query = db.query(ActivityLog)
    if client_id:
        query = query.filter(ActivityLog.client_id == client_id)
    if user_id:
        query = query.filter(ActivityLog.user_id == user_id)
    if action:
        query = query.filter(ActivityLog.action == action)
    rows = query.order_by(ActivityLog.created_at.desc()).offset(max(offset, 0)).limit(min(max(limit, 1), 500)).all()
    return [
        {
            "id": r.id, "created_at": r.created_at, "user_id": r.user_id, "user_name": r.user_name,
            "user_email": r.user_email, "user_role": r.user_role, "action": r.action,
            "client_id": r.client_id, "client_name": r.client_name, "detail": r.detail,
        }
        for r in rows
    ]
