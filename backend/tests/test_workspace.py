from pathlib import Path

import pytest

from app.services.workspace import (
    WorkspaceError,
    assert_workspace_allowed,
    is_c_drive,
    resolve_under_workspace,
    resolve_workspace,
)


def test_workspace_exists():
    root = resolve_workspace(str(Path(__file__).resolve().parents[2]))
    assert root.is_dir()


def test_jail_blocks_parent(tmp_path: Path):
    nested = tmp_path / "ws"
    nested.mkdir()
    with pytest.raises(WorkspaceError):
        resolve_under_workspace(nested, "../secret.txt")


def test_c_drive_is_blocked():
    assert is_c_drive(Path("C:/Windows"))
    with pytest.raises(WorkspaceError, match="C 盘"):
        assert_workspace_allowed(Path("C:/Windows"))


def test_jail_blocks_absolute_c_drive_file(tmp_path: Path):
    nested = tmp_path / "ws"
    nested.mkdir()
    with pytest.raises(WorkspaceError, match="escapes"):
        resolve_under_workspace(nested, r"C:\Windows\System32\drivers\etc\hosts")


def test_jail_blocks_absolute_other_repo(tmp_path: Path):
    nested = tmp_path / "ws"
    nested.mkdir()
    outsider = tmp_path / "other" / "secret.txt"
    outsider.parent.mkdir()
    outsider.write_text("keep", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="escapes"):
        resolve_under_workspace(nested, str(outsider))
