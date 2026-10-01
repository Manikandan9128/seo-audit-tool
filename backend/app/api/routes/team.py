import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, require_admin, require_super_admin
from app.core import permissions
from app.core.security import generate_password, hash_password
from app.models.activity_log import ActivityLog
from app.models.user import User
from app.services.activity_service import log_activity

router = APIRouter(tags=["team"])


class RoleUpdate(BaseModel):
    role: str


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str
    role: str = permissions.MEMBER


class ActiveUpdate(BaseModel):
    active: bool


@router.get("/users")
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    users = db.query(User).order_by(User.created_at.asc()).all()
    return [
        {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role, "is_active": u.is_active,
         "created_at": u.created_at}
        for u in users
    ]


@router.post("/users", status_code=201)
def create_user(payload: UserCreate, db: Session = Depends(get_db), current_user: User = Depends(require_super_admin)):
    """The only way to get an account (internal tool). The tool generates the
    starting password and returns it once, in this response - it is stored
    only as a hash, so the super admin must pass it on now."""
    if payload.role == permissions.SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="There can only be one super admin.")
    if payload.role not in (permissions.ADMIN, permissions.MEMBER):
        raise HTTPException(status_code=400, detail="role must be admin or member")
    email = payload.email.strip().lower()
    if db.query(User).filter(func.lower(User.email) == email).first():
        raise HTTPException(status_code=400, detail="An account with this email already exists.")
    if not payload.full_name.strip():
        raise HTTPException(status_code=400, detail="Name is required.")
    password = generate_password()
    user = User(email=email, full_name=payload.full_name.strip(), role=payload.role, hashed_password=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    log_activity(current_user, "user_created", target_user=user.email, target_role=user.role)
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role,
            "is_active": user.is_active, "temporary_password": password}


@router.post("/users/{user_id}/reset-password")
def reset_user_password(user_id: uuid.UUID, db: Session = Depends(get_db), current_user: User = Depends(require_super_admin)):
    """New generated password for someone who is locked out - returned once."""
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    password = generate_password()
    target.hashed_password = hash_password(password)
    db.commit()
    log_activity(current_user, "password_reset", target_user=target.email, target_role=target.role)
    return {"id": target.id, "temporary_password": password}


@router.patch("/users/{user_id}/role")
def set_user_role(
    user_id: uuid.UUID, payload: RoleUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_super_admin),
):
    if payload.role not in permissions.ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {list(permissions.ROLES)}")
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.role == permissions.SUPER_ADMIN and target.role != permissions.SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="There can only be one super admin.")
    if target.role == permissions.SUPER_ADMIN and payload.role != permissions.SUPER_ADMIN:
        remaining = db.query(User).filter(User.role == permissions.SUPER_ADMIN, User.id != target.id).count()
        if remaining == 0:
            raise HTTPException(status_code=400, detail="There must always be at least one super admin.")
    previous = target.role
    target.role = payload.role
    db.commit()
    log_activity(current_user, "role_changed", target_user=target.email, from_role=previous, to_role=payload.role)
    return {"id": target.id, "role": target.role}


@router.patch("/users/{user_id}/active")
def set_user_active(
    user_id: uuid.UUID, payload: ActiveUpdate, db: Session = Depends(get_db), current_user: User = Depends(require_super_admin),
):
    """Deactivate (or reactivate) a team member or admin. Super admin only;
    a super admin account itself can't be deactivated, so the team can never
    lock itself out."""
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="You can't deactivate your own account.")
    if target.role == permissions.SUPER_ADMIN:
        raise HTTPException(status_code=400, detail="A super admin account can't be deactivated. Change the role first.")
    if target.is_active != payload.active:
        target.is_active = payload.active
        db.commit()
        log_activity(
            current_user, "user_reactivated" if payload.active else "user_deactivated",
            target_user=target.email, target_role=target.role,
        )
    return {"id": target.id, "is_active": target.is_active}


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
