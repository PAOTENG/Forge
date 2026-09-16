from app.core.security import hash_password, new_session_token, verify_password
from app.tools import assert_safe_git, default_test_command
from pathlib import Path


def test_password_roundtrip():
    hashed = hash_password("correct-horse")
    assert verify_password("correct-horse", hashed)
    assert not verify_password("wrong", hashed)


def test_session_token_three_days():
    _token, exp = new_session_token(days=3)
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    assert exp - now > timedelta(days=2, hours=20)
    assert exp - now < timedelta(days=3, hours=2)


def test_git_blocks_force():
    try:
        assert_safe_git(["push", "--force", "origin"])
        raise AssertionError("should block")
    except ValueError:
        pass


def test_git_blocks_hard_reset():
    try:
        assert_safe_git(["reset", "--hard", "HEAD"])
        raise AssertionError("should block")
    except ValueError:
        pass


def test_git_blocks_force_with_lease():
    try:
        assert_safe_git(["push", "--force-with-lease", "origin"])
        raise AssertionError("should block")
    except ValueError:
        pass


def test_git_blocks_rebase():
    try:
        assert_safe_git(["rebase", "origin/master"])
        raise AssertionError("should block")
    except ValueError:
        pass


def test_default_test_command_prefers_backend(tmp_path: Path):
    (tmp_path / "backend" / "tests").mkdir(parents=True)
    assert "backend/tests" in default_test_command(tmp_path)
