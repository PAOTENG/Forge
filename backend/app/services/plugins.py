"""
工作台插件库：内置查看器元数据 + 把 plugin_store 拷到 data/plugins/。

【在 Agent 里的位置】
    不是 Agent 工具，也不是 VS Code 市场。进入工作台按扩展名打开 md/docx/xlsx。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.core.config import BACKEND_ROOT, REPO_ROOT

PLUGIN_ROOT = REPO_ROOT / "data" / "plugins"
STORE_ROOT = BACKEND_ROOT / "plugin_store"

BUILTINS: list[dict] = [
    {
        "id": "markdown",
        "name": "Markdown",
        "version": "1.0.0",
        "publisher": "Forge",
        "summary": "内置。Preview 渲染与源码编辑。",
        "exts": [".md", ".markdown"],
        "kind": "markdown",
        "builtin": True,
        "recommended": True,
    },
    {
        "id": "latex",
        "name": "LaTeX",
        "version": "1.0.0",
        "publisher": "Forge",
        "summary": "内置。打开 .tex 源码。",
        "exts": [".tex", ".ltx", ".latex"],
        "kind": "editor",
        "builtin": True,
        "recommended": True,
    },
    {
        "id": "code",
        "name": "代码高亮",
        "version": "1.0.2",
        "publisher": "Forge",
        "summary": "内置。Python / TS / JS / Vue 等源码。",
        "exts": [
            ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".go", ".rs", ".java",
            ".c", ".cpp", ".h", ".hpp", ".cs", ".kt", ".swift", ".rb", ".php",
            ".css", ".html", ".sql", ".toml", ".xml", ".ini", ".env", ".txt",
            ".log", ".conf", ".sh", ".ps1", ".bat",
        ],
        "kind": "editor",
        "builtin": True,
        "recommended": True,
    },
    {
        "id": "json",
        "name": "JSON",
        "version": "1.0.0",
        "publisher": "Forge",
        "summary": "内置。高亮 JSON。",
        "exts": [".json", ".jsonc"],
        "kind": "editor",
        "builtin": True,
        "recommended": True,
    },
    {
        "id": "yaml",
        "name": "YAML",
        "version": "1.0.0",
        "publisher": "Forge",
        "summary": "内置。高亮 YAML。",
        "exts": [".yml", ".yaml"],
        "kind": "editor",
        "builtin": True,
        "recommended": True,
    },
    {
        "id": "image",
        "name": "图片预览",
        "version": "1.0.0",
        "publisher": "Forge",
        "summary": "内置。png / jpg / gif / webp / svg。",
        "exts": [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"],
        "kind": "image",
        "builtin": True,
        "recommended": True,
    },
    {
        "id": "csv",
        "name": "CSV",
        "version": "1.0.0",
        "publisher": "Forge",
        "summary": "内置。表格预览 CSV / TSV。",
        "exts": [".csv", ".tsv"],
        "kind": "csv",
        "builtin": True,
        "recommended": True,
    },
    {
        "id": "excel",
        "name": "Excel 预览",
        "version": "1.0.0",
        "publisher": "Forge",
        "summary": "内置。xlsx / xls 表格编辑。",
        "exts": [".xlsx", ".xlsm", ".xls", ".ods"],
        "kind": "spreadsheet",
        "builtin": True,
        "recommended": True,
    },
    {
        "id": "pdf",
        "name": "PDF 预览",
        "version": "1.0.0",
        "publisher": "Forge",
        "summary": "内置。浏览器内预览 PDF，不能当 Word 改。",
        "exts": [".pdf"],
        "kind": "pdf",
        "builtin": True,
        "recommended": True,
    },
    {
        "id": "word",
        "name": "Word",
        "version": "1.0.0",
        "publisher": "Forge",
        "summary": "内置。打开并编辑 .docx（富文本，不是完整 Word）。",
        "exts": [".docx"],
        "kind": "document",
        "builtin": True,
        "recommended": True,
    },
    {
        "id": "media",
        "name": "音视频",
        "version": "1.0.0",
        "publisher": "Forge",
        "summary": "内置。浏览器内播放 mp3 / mp4 等，不能改编码。",
        "exts": [".mp3", ".mp4", ".wav", ".mov", ".avi", ".webm", ".ogg", ".m4a"],
        "kind": "media",
        "builtin": True,
        "recommended": True,
    },
]


def _read_json(path: Path) -> dict:
    """【功能】读插件 manifest.json。"""
    return json.loads(path.read_text(encoding="utf-8"))


def load_store() -> list[dict]:
    """【功能】扫描 backend/plugin_store 各子目录的 manifest。"""
    items: list[dict] = []
    if not STORE_ROOT.is_dir():
        return items
    for folder in sorted(STORE_ROOT.iterdir()):
        manifest = folder / "manifest.json"
        if not folder.is_dir() or not manifest.is_file():
            continue
        data = _read_json(manifest)
        data["builtin"] = False
        data["package_dir"] = str(folder)
        items.append(data)
    return items


def catalog() -> list[dict]:
    """【功能】内置 + 商店，id 去重。给插件面板列表。"""
    seen = {p["id"] for p in BUILTINS}
    extras = [p for p in load_store() if p["id"] not in seen]
    return [*BUILTINS, *extras]


def catalog_by_id() -> dict[str, dict]:
    """【功能】id → 元数据，详情页用。"""
    return {p["id"]: p for p in catalog()}


def plugin_for_ext(ext: str) -> dict | None:
    """【功能】按扩展名找第一个能打开的查看器。"""
    ext = ext.lower()
    if not ext.startswith("."):
        ext = f".{ext}"
    for item in catalog():
        if ext in item.get("exts", []):
            return item
    return None


def install_dir(plugin_id: str) -> Path:
    """【功能】data/plugins/{id} 路径。"""
    return PLUGIN_ROOT / plugin_id


def is_installed_on_disk(plugin_id: str) -> bool:
    """【功能】磁盘上是否已有拷出来的包。"""
    return (install_dir(plugin_id) / "manifest.json").is_file()


def package_readme(plugin_id: str) -> str:
    """【功能】详情页 README，找不到就用 summary。"""
    meta = catalog_by_id().get(plugin_id)
    store = STORE_ROOT / plugin_id / "README.md"
    if store.is_file():
        return store.read_text(encoding="utf-8")
    installed = install_dir(plugin_id) / "README.md"
    if installed.is_file():
        return installed.read_text(encoding="utf-8")
    return (meta or {}).get("summary") or ""


def install_from_store(plugin_id: str) -> Path:
    """【功能】copytree 商店包到 data/plugins。已存在则先删。输出目标目录。"""
    src = STORE_ROOT / plugin_id
    if not (src / "manifest.json").is_file():
        raise FileNotFoundError(plugin_id)
    dest = install_dir(plugin_id)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest
