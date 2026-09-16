from app.tools import ForgeContext, _needs_hitl
from agents import RunContextWrapper


def _ctx(**kwargs) -> RunContextWrapper[ForgeContext]:
    base = dict(
        session_id="s",
        workspace=".",
        mode="build",
        actor="eval",
        model="",
        role="",
        auto_approve=False,
        allow_git_push=True,
    )
    base.update(kwargs)
    return RunContextWrapper(context=ForgeContext(**base))


def test_eval_auto_approve_skips_hitl():
    assert _needs_hitl(_ctx(auto_approve=True), {}, "c1") is False


def test_normal_engineer_still_needs_hitl():
    assert _needs_hitl(_ctx(role="engineer"), {}, "c1") is True


def test_admin_without_setting_still_needs_hitl(monkeypatch):
    class Settings:
        forge_admin_auto_approve = False

    monkeypatch.setattr("app.core.config.get_settings", lambda: Settings())
    assert _needs_hitl(_ctx(role="admin"), {}, "c1") is True


def test_admin_with_setting_skips_hitl(monkeypatch):
    class Settings:
        forge_admin_auto_approve = True

    monkeypatch.setattr("app.core.config.get_settings", lambda: Settings())
    assert _needs_hitl(_ctx(role="admin", auto_approve=False), {}, "c1") is False
