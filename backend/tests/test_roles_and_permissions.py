import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import deps
from app.api.routes import team
from app.core import permissions
from app.services import role_service


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
    def __init__(self, report_after_upload=False, other_super_admins=1, target=None):
        self.report = report_after_upload
        self.others = other_super_admins
        self.target = target
        self.committed = False

    def query(self, model, *a):
        from app.models.report_generation_job import ReportGenerationJob

        if model is ReportGenerationJob.id:
            return _Q(first=(uuid.uuid4(),) if self.report else None)
        return _Q(count=self.others)

    def get(self, model, pk):
        return self.target

    def commit(self):
        self.committed = True


def _user(role, uid=None):
    return SimpleNamespace(id=uid or uuid.uuid4(), role=role, email=f"{role}@x.com", full_name=role)


def _upload(owner_id):
    return SimpleNamespace(uploaded_by_user_id=owner_id, client_id=uuid.uuid4(), created_at=datetime.now(timezone.utc))


def test_admin_and_super_admin_can_always_delete():
    rec = _upload(uuid.uuid4())
    for role in ("admin", "super_admin"):
        assert permissions.can_delete_upload(_user(role), rec, _Db(report_after_upload=True))


def test_member_deletes_own_upload_until_a_report_is_generated():
    me = _user("member")
    rec = _upload(me.id)
    assert permissions.can_delete_upload(me, rec, _Db(report_after_upload=False))
    assert not permissions.can_delete_upload(me, rec, _Db(report_after_upload=True))


def test_member_cannot_delete_someone_elses_upload():
    assert not permissions.can_delete_upload(_user("member"), _upload(uuid.uuid4()), _Db())


def test_role_gates():
    with pytest.raises(HTTPException) as e:
        deps.require_super_admin(_user("admin"))
    assert e.value.status_code == 403
    assert deps.require_super_admin(_user("super_admin"))
    with pytest.raises(HTTPException):
        deps.require_admin(_user("member"))
    assert deps.require_admin(_user("admin"))


def test_bootstrap_roles_from_config(monkeypatch):
    monkeypatch.setattr(role_service.settings, "super_admin_emails", "Boss@x.com")
    monkeypatch.setattr(role_service.settings, "admin_emails", "lead@x.com, other@x.com")
    assert role_service.bootstrap_role_for("boss@x.com") == "super_admin"
    assert role_service.bootstrap_role_for("LEAD@x.com") == "admin"
    assert role_service.bootstrap_role_for("new@x.com") == "member"


def test_only_valid_roles_and_never_remove_last_super_admin(monkeypatch):
    monkeypatch.setattr(team, "log_activity", lambda *a, **k: None)
    target = _user("super_admin")
    with pytest.raises(HTTPException) as e:
        team.set_user_role(target.id, team.RoleUpdate(role="root"), _Db(target=target), _user("super_admin"))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        team.set_user_role(target.id, team.RoleUpdate(role="member"), _Db(other_super_admins=0, target=target), _user("super_admin"))
    assert "at least one super admin" in e.value.detail
    db = _Db(other_super_admins=1, target=target)
    team.set_user_role(target.id, team.RoleUpdate(role="admin"), db, _user("super_admin"))
    assert target.role == "admin" and db.committed


def test_startup_promotes_oldest_account_when_no_super_admin(monkeypatch):
    monkeypatch.setattr(role_service.settings, "super_admin_emails", "")
    monkeypatch.setattr(role_service.settings, "admin_emails", "")
    users = [_user("member"), _user("member")]

    class Db:
        def __init__(self):
            self.super = None

        def query(self, model):
            outer = self

            class Q:
                def all(self):
                    return users

                def filter(self, *a):
                    return self

                def first(self):
                    return next((u for u in users if u.role == "super_admin"), None) if outer.super is None else None

                def order_by(self, *a):
                    class O:
                        def first(_):
                            return users[0]

                    return O()

            return Q()

        def commit(self):
            pass

    role_service.sync_configured_roles(Db())
    assert users[0].role == "super_admin" and users[1].role == "member"
