"""
按扩展名判断工作台用哪种查看器（markdown/editor/image/pdf/…）。

【在 Agent 里的位置】
    sessions 读文件时 classify，前端决定开 Monaco 还是 DocxEditor 还是只下载。
"""

from __future__ import annotations

from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico"}
PDF_EXTS = {".pdf"}
SHEET_EXTS = {".xlsx", ".xlsm", ".xls", ".ods"}
CSV_EXTS = {".csv", ".tsv"}
MARKDOWN_EXTS = {".md", ".markdown"}
LATEX_EXTS = {".tex", ".ltx", ".latex"}
DOCUMENT_EXTS = {".docx"}
MEDIA_EXTS = {".mp3", ".mp4", ".wav", ".mov", ".avi", ".webm", ".ogg", ".m4a"}
BINARY_EXTS = {
    ".doc",
    ".pptx",
    ".ppt",
    ".odt",
    ".odp",
    ".zip",
    ".7z",
    ".rar",
    ".gz",
    ".tar",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".wasm",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".pyc",
    ".class",
    ".o",
    ".a",
    ".bin",
    ".dat",
}


def classify(path: Path) -> dict:
    """
    【功能】返回 {kind, binary}。未知后缀则读前 4KB，有 \\x00 当二进制只能下载。
    【输入】文件 Path。输出：给前端的 kind。
    """
    ext = path.suffix.lower()
    if ext in MARKDOWN_EXTS:
        return {"kind": "markdown", "binary": False}
    if ext in LATEX_EXTS:
        return {"kind": "editor", "binary": False}
    if ext in IMAGE_EXTS:
        return {"kind": "image", "binary": True}
    if ext in PDF_EXTS:
        return {"kind": "pdf", "binary": True}
    if ext in SHEET_EXTS:
        return {"kind": "spreadsheet", "binary": True}
    if ext in CSV_EXTS:
        return {"kind": "csv", "binary": False}
    if ext in DOCUMENT_EXTS:
        return {"kind": "document", "binary": True}
    if ext in MEDIA_EXTS:
        return {"kind": "media", "binary": True}
    if ext in BINARY_EXTS:
        return {"kind": "binary", "binary": True}
    try:
        sample = path.read_bytes()[:4096]
    except OSError:
        return {"kind": "editor", "binary": False}
    if b"\x00" in sample:
        return {"kind": "binary", "binary": True}
    return {"kind": "editor", "binary": False}
