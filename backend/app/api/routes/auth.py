from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core import permissions
from app.core.security import MIN_PASSWORD_LENGTH, create_access_token, hash_password, verify_password
from app.models.user import User
from app.services.activity_service import log_activity
from app.schemas.auth import PasswordChange, TokenOut, UserLogin, UserOut, UserRegister

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, db: Session = Depends(get_db)):
    """First-run setup only: while no account exists the first sign-up becomes
    the super admin. After that accounts are created by the super admin on the
    Team page (POST /users) - this is an internal tool, nobody signs
    themselves up."""
    if db.query(User).count() > 0:
        raise HTTPException(status_code=403, detail="Accounts are created by a super admin. Ask them for a login.")
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=permissions.SUPER_ADMIN,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_activity(user, "registered")
    return user


@router.post("/login", response_model=TokenOut)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been deactivated. Contact a super admin.")
    log_activity(user, "login")
    token = create_access_token(subject=str(user.id))
    return TokenOut(access_token=token)


@router.post("/change-password")
def change_password(payload: PasswordChange, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Your current password is not correct.")
    if len(payload.new_password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"The new password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=400, detail="Choose a password different from the current one.")
    current_user.hashed_password = hash_password(payload.new_password)
    db.commit()
    log_activity(current_user, "password_changed")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user
