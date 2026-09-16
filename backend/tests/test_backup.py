from datetime import datetime, timezone
from pathlib import Path

from app.models.backup import FileBackupRow
from app.services.backup import _apply_row, write_note


def test_write_note_records_actor_model_and_diff_summary(tmp_path: Path):
    folder = tmp_path / "snap"
    write_note(
        folder,
        {
            "created_at": "2026-09-03T07:00:00+00:00",
            "actor": "oops",
            "session_id": "sess-1",
            "model": "deepseek/deepseek-chat",
            "action": "write",
            "rel_path": "app/orders.py",
            "existed": True,
            "summary": "@@ -1 +1 @@\n-old\n+new\n",
        },
    )
    text = (folder / "NOTES.txt").read_text(encoding="utf-8")
    assert "时间：2026-09-03T07:00:00+00:00" in text
    assert "修改人：oops" in text
    assert "模型：deepseek/deepseek-chat" in text
    assert "app/orders.py" in text
    assert "old" in text


def _row(workspace: Path, rel: str, existed: bool, original: str) -> FileBackupRow:
    folder = workspace / ".blob"
    folder.mkdir(exist_ok=True)
    (folder / "original").write_text(original, encoding="utf-8")
    return FileBackupRow(
        id="b1",
        session_id="s1",
        rel_path=rel,
        action="write" if existed else "write",
        existed=existed,
        actor="oops",
        model="deepseek/deepseek-chat",
        summary="test",
        blob_dir=str(folder),
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
    )


def test_restore_overwrites_with_original(tmp_path: Path):
    dest = tmp_path / "bugs.py"
    dest.write_text("broken", encoding="utf-8")
    row = _row(tmp_path, "bugs.py", True, "healthy")
    assert _apply_row(tmp_path, row) == "restored bugs.py"
    assert dest.read_text(encoding="utf-8") == "healthy"


def test_restore_new_file_deletes_it(tmp_path: Path):
    dest = tmp_path / "created.py"
    dest.write_text("agent added", encoding="utf-8")
    row = _row(tmp_path, "created.py", False, "")
    assert _apply_row(tmp_path, row) == "removed created.py"
    assert not dest.exists()
