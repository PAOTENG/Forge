from pathlib import Path

import pytest

from app.services.safety import (
    SafetyError,
    assert_safe_bash,
    assert_safe_git,
    assert_write_path,
    resolve_tool_path,
)


def test_write_refuses_c_drive_absolute(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(SafetyError, match="C 盘"):
        assert_write_path(ws, r"C:\Users\oops\forge-eval-should-not-exist.txt")


def test_write_refuses_parent_escape(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "secret.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(SafetyError, match="超出当前工作区"):
        assert_write_path(ws, "../secret.txt")


def test_hotfix_refuses_readme_and_tests(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "bugs.py").write_text("ok", encoding="utf-8")
    (ws / "README.md").write_text("doc", encoding="utf-8")
    (ws / "tests").mkdir()
    (ws / "tests" / "test_orders.py").write_text("assert True", encoding="utf-8")
    with pytest.raises(SafetyError, match="热修模式只能改"):
        assert_write_path(ws, "README.md", write_scope="hotfix")
    with pytest.raises(SafetyError, match="不能改测试"):
        assert_write_path(ws, "tests/test_orders.py", write_scope="hotfix")
    with pytest.raises(SafetyError, match="active_bug"):
        assert_write_path(ws, "active_bug.txt", write_scope="hotfix")
    allowed = assert_write_path(ws, "bugs.py", write_scope="hotfix")
    assert allowed == (ws / "bugs.py").resolve()


def test_workspace_scope_allows_readme_but_not_env(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "README.md").write_text("doc", encoding="utf-8")
    assert_write_path(ws, "README.md", write_scope="workspace")
    with pytest.raises(SafetyError, match=".env"):
        assert_write_path(ws, ".env", write_scope="workspace")


def test_bash_refuses_dangerous_git():
    with pytest.raises(SafetyError, match="reset --hard"):
        assert_safe_bash("git reset --hard HEAD")
    with pytest.raises(SafetyError, match="force"):
        assert_safe_bash("git push --force origin master")
    with pytest.raises(SafetyError, match="C 盘"):
        assert_safe_bash("echo pwned > C:/Windows/Temp/x.txt")
    assert_safe_bash("git show HEAD --stat")


def test_git_tool_refuses_force():
    with pytest.raises(SafetyError, match="force"):
        assert_safe_git(["push", "--force", "origin"])


def test_read_refuses_env(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".env").write_text("SECRET=1", encoding="utf-8")
    with pytest.raises(SafetyError, match=".env"):
        resolve_tool_path(ws, ".env")


def test_read_also_refuses_absolute(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(SafetyError, match="绝对路径|C 盘"):
        resolve_tool_path(ws, r"C:\Windows\win.ini")
