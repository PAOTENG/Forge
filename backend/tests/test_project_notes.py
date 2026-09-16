from pathlib import Path

from app.services.project_notes import (
    is_placeholder_title,
    load_project_notes,
    title_from_prompt,
    with_open_file,
)


def test_load_agents_md(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("# notes\nfix one bug", encoding="utf-8")
    assert "fix one bug" in load_project_notes(tmp_path)


def test_load_skips_missing(tmp_path: Path):
    assert load_project_notes(tmp_path) == ""


def test_open_file_prefix():
    out = with_open_file("看看这个函数", "app/main.py")
    assert out.startswith("[当前编辑器打开：app/main.py]")
    assert "看看这个函数" in out


def test_open_file_rejects_parent():
    assert with_open_file("x", "../secret") == "x"
    assert with_open_file("x", "") == "x"


def test_title_from_prompt():
    assert title_from_prompt("  修一下 canary  ") == "修一下 canary"
    long = "a" * 80
    assert title_from_prompt(long).endswith("…")
    assert is_placeholder_title("New session", "order-api")
    assert is_placeholder_title("order-api", "order-api")
    assert is_placeholder_title("值班 · 下单500")
    assert not is_placeholder_title("修一下 canary")
