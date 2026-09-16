"""
HTTP 请求/响应的 Pydantic 形状。

【在 Agent 里的位置】
    前端开会话、发消息、点批准、存文件，body 都先过这些模型再进 router。
    这里不跑 Agent，只约定字段，避免脏数据进 Runtime。

【调用关系】
    routers/sessions.py、chat.py、models.py 引用。
    和 ORM（app.models）分开：ORM 是库表，这里是 API 契约。
"""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    """
    【功能】POST /api/sessions 开会话的入参。
    【字段】
        workspace_path：值班机上的工作区绝对路径（热修默认 order-api）。
        title：会话标题，空则用仓库名，首条消息后会被改成摘要。
        mode：plan 只读规划 / build 才能写文件跑测试。
        model：空则用 preferred_model 或 .env 兜底。
    """

    workspace_path: str
    title: str = "New session"
    mode: Literal["plan", "build"] = "build"
    model: str = ""


class SessionOut(BaseModel):
    """
    【功能】会话列表/详情返回给前端，用来画侧栏和批准条。
    【字段】
        pending_tool / pending_arguments：库里还记着上次没批完的工具，刷新后要重新画批准条。
        live_run / run_status：进程内存里 Agent 是否还在跑（running / approval / idle）。
    """

    id: str
    title: str
    workspace_path: str
    model: str
    mode: str
    pending_tool: str = ""
    pending_arguments: str = ""
    live_run: bool = False
    run_status: str = "idle"


class MessageIn(BaseModel):
    """
    【功能】用户发一条值班消息。
    【字段】
        content：用户原话，至少 1 字符。
        open_path：当前编辑器打开的相对路径，chat 会拼进 prompt 让模型先读它。
    """

    content: str = Field(min_length=1)
    open_path: str = ""


class MessageOut(BaseModel):
    """
    【功能】历史气泡。kind=tool 表示这是工具调用日志，不是模型最终回答。
    """

    id: str
    role: str
    kind: str = ""
    content: str


class ApprovalIn(BaseModel):
    """
    【功能】HITL：允许或拒绝当前变更工具。
    【字段】
        approved：True 允许执行，False 拒绝。
        scope：once 只批这一次；session 本会话后续同类工具不再打断（SDK always_approve）。
    """

    approved: bool
    scope: Literal["once", "session"] = "once"


class WritePreviewIn(BaseModel):
    """【功能】工作台预览 unified diff：path + 即将写入的 content。"""

    path: str
    content: str


class FileSaveIn(BaseModel):
    """【功能】工作台人手改文件后保存：相对路径 + 全文。"""

    path: str
    content: str


class FsEntryIn(BaseModel):
    """【功能】新建文件或目录。path 相对工作区；建文件时可带初始 content。"""

    path: str = Field(min_length=1, max_length=400)
    content: str = ""


class FsRenameIn(BaseModel):
    """【功能】重命名：原相对路径 + 新文件名（不含目录）。"""

    path: str = Field(min_length=1, max_length=400)
    name: str = Field(min_length=1, max_length=200)


class CustomModelIn(BaseModel):
    """【功能】管理员往目录里加一条自定义模型 id。"""

    provider: str = "custom"
    label: str
    model_id: str


class DefaultModelIn(BaseModel):
    """【功能】右上角切默认模型，写入 data/preferred_model.txt。"""

    model: str
