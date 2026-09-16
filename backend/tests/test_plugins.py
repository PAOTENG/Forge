from pathlib import Path

from app.services.plugins import catalog_by_id, plugin_for_ext
from app.services.viewers import classify


def test_store_has_code_and_json():
    catalog = catalog_by_id()
    assert catalog["markdown"]["builtin"] is True
    assert catalog["code"]["builtin"] is True
    assert catalog["excel"]["builtin"] is True
    assert catalog["word"]["builtin"] is True


def test_plugin_for_ext():
    assert plugin_for_ext(".md")["id"] == "markdown"
    assert plugin_for_ext(".py")["id"] == "code"
    assert plugin_for_ext(".xlsx")["id"] == "excel"
    assert plugin_for_ext(".docx")["id"] == "word"


def test_classify_xlsx_is_spreadsheet(tmp_path: Path):
    f = tmp_path / "a.xlsx"
    f.write_bytes(b"")
    assert classify(f)["kind"] == "spreadsheet"
    assert classify(f)["binary"] is True


def test_classify_docx_is_document(tmp_path: Path):
    f = tmp_path / "a.docx"
    f.write_bytes(b"")
    assert classify(f)["kind"] == "document"
    assert classify(f)["binary"] is True


def test_install_copies_package(tmp_path, monkeypatch):
    import app.services.plugins as plugins

    dest_root = tmp_path / "installed"
    monkeypatch.setattr(plugins, "PLUGIN_ROOT", dest_root)
    target = plugins.install_from_store("json")
    assert (target / "manifest.json").is_file()
    assert (target / "README.md").is_file()
    assert target.parent == dest_root
