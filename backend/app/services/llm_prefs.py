"""
用户选中的默认模型，存在 data/preferred_model.txt。

【在 Agent 里的位置】
    右上角切换后写入；之后新建会话、resolve_model 都优先读这个。
    .env 的 FORGE_MODEL 只是还没切过时的兜底。
"""

from __future__ import annotations

from app.core.config import REPO_ROOT

PREF_PATH = REPO_ROOT / "data" / "preferred_model.txt"


def preferred_model() -> str:
    """【功能】读默认模型 id。文件不存在返回空串。"""
    try:
        return PREF_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def save_preferred_model(model_id: str) -> str:
    """
    【功能】写入默认模型。改完立刻对后续新会话生效，不用重启 API（Key 仍要重启）。
    【输入】model_id。输出：去掉空白后的 id。
    """
    mid = (model_id or "").strip()
    PREF_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREF_PATH.write_text(mid + "\n", encoding="utf-8")
    return mid
