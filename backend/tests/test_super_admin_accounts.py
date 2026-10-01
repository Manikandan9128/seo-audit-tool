import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import auth, team
from app.core import permissions
from app.core.security import MIN_PASSWORD_LENGTH, generate_password, hash_password, verify_password
from app.schemas.auth import PasswordChange, UserRegister


class _Q:
    def __init__(self, first=None, count=0):
        self._first, self._count = first, count

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._first

    def count(self):
        return self._count


class _Db:
    def __init__(self, existing_users=0, duplicate=None, target=None, other_super_admins=0):
        self.existing_users, self.duplicate, self.target = existing_users, duplicate, target
        self.other_super_admins = other_super_admins
        self.added = []
        self.committed = False

    def query(self, *a):
        return _Q(first=self.duplicate, count=self.existing_users or self.other_super_admins)

    def get(self, model, pk):
        return self.target

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "is_active", None) is None:
            obj.is_active = True


def _super():
    return SimpleNamespace(id=uuid.uuid4(), role=permissions.SUPER_ADMIN, email="boss@x.com", full_name="Boss")


@pytest.fixture(autouse=True)
def _no_activity_log(monkeypatch):
    monkeypatch.setattr(team, "log_activity", lambda *a, **k: None)
    monkeypatch.setattr(auth, "log_activity", lambda *a, **k: None)


def test_generated_password_is_random_and_unambiguous():
    first, second = generate_password(), generate_password()
    assert first != second and len(first) == 12
    assert not set(first) & set("0O1lI")


def test_public_registration_is_closed_once_any_account_exists():
    with pytest.raises(HTTPException) as e:
        auth.register(UserRegister(email="a@b.com", password="pw", full_name="A"), _Db(existing_users=1))
    assert e.value.status_code == 403 and "created by a super admin" in e.value.detail


def test_first_run_registration_creates_the_super_admin():
    db = _Db(existing_users=0)
    auth.register(UserRegister(email="first@b.com", password="pw", full_name="First"), db)
    assert db.added[0].role == permissions.SUPER_ADMIN


def test_super_admin_creates_user_with_a_generated_password_shown_once():
    db = _Db()
    result = team.create_user(team.UserCreate(email="New@Corp.com", full_name="New Person", role="member"), db, _super())
    assert result["email"] == "new@corp.com" and result["role"] == "member"
    stored = db.added[0]
    assert verify_password(result["temporary_password"], stored.hashed_password)
    assert "hashed_password" not in result


def test_cannot_create_a_second_super_admin_or_a_duplicate_email():
    with pytest.raises(HTTPException) as e:
        team.create_user(team.UserCreate(email="x@y.com", full_name="X", role="super_admin"), _Db(), _super())
    assert "only be one super admin" in e.value.detail
    with pytest.raises(HTTPException) as e:
        team.create_user(team.UserCreate(email="x@y.com", full_name="X", role="admin"), _Db(duplicate=object()), _super())
    assert "already exists" in e.value.detail


def test_promoting_anyone_to_super_admin_is_refused():
    target = SimpleNamespace(id=uuid.uuid4(), role="admin", email="a@x.com")
    with pytest.raises(HTTPException) as e:
        team.set_user_role(target.id, team.RoleUpdate(role="super_admin"), _Db(target=target), _super())
    assert "only be one super admin" in e.value.detail and target.role == "admin"


def test_reset_password_gives_a_new_working_password():
    target = SimpleNamespace(id=uuid.uuid4(), role="member", email="m@x.com", hashed_password=hash_password("old-pass-1"))
    result = team.reset_user_password(target.id, _Db(target=target), _super())
    assert verify_password(result["temporary_password"], target.hashed_password)
    assert not verify_password("old-pass-1", target.hashed_password)


def _me(password="current-pw-1"):
    return SimpleNamespace(id=uuid.uuid4(), role="member", email="me@x.com", hashed_password=hash_password(password))


def test_user_can_change_own_password():
    me, db = _me(), _Db()
    auth.change_password(PasswordChange(current_password="current-pw-1", new_password="a-brand-new-pw"), db, me)
    assert verify_password("a-brand-new-pw", me.hashed_password) and db.committed


@pytest.mark.parametrize("current,new,fragment", [
    ("wrong", "a-brand-new-pw", "not correct"),
    ("current-pw-1", "short", f"at least {MIN_PASSWORD_LENGTH}"),
    ("current-pw-1", "current-pw-1", "different"),
])
def test_change_password_rules(current, new, fragment):
    me = _me()
    with pytest.raises(HTTPException) as e:
        auth.change_password(PasswordChange(current_password=current, new_password=new), _Db(), me)
    assert e.value.status_code == 400 and fragment in e.value.detail
    assert verify_password("current-pw-1", me.hashed_password)


def test_config_never_promotes_a_second_super_admin(monkeypatch):
    from app.services import role_service

    monkeypatch.setattr(role_service.settings, "super_admin_emails", "second@x.com", raising=False)
    monkeypatch.setattr(role_service.settings, "admin_emails", "", raising=False)
    owner = SimpleNamespace(email="owner@x.com", role=permissions.SUPER_ADMIN)
    second = SimpleNamespace(email="second@x.com", role="member")

    class _RoleDb:
        def query(self, model):
            return SimpleNamespace(
                all=lambda: [owner, second],
                filter=lambda *a: SimpleNamespace(first=lambda: owner),
                order_by=lambda *a: SimpleNamespace(first=lambda: owner),
            )

        def commit(self):
            pass

    role_service.sync_configured_roles(_RoleDb())
    assert second.role != permissions.SUPER_ADMIN
