"""
ORM 模型汇总：让 Alembic 和业务代码一次 import 到全部表。

【在 Agent 里的位置】
    Agent 状态、聊天、告警、备份、账号都落在这些表。
    本文件只做再导出，不定义列。

【表与 Agent 功能对照】
    SessionRow          一次值班排查会话
    MessageRow          聊天气泡 + 工具日志
    AgentStateRow       SDK input_items + 待审批工具（断线/重启后续聊）
    AuditRow            人点允许/拒绝的审计
    ToolCallRow         工具调用哈希链审计
    FileBackupRow       写前快照元数据
    FileChunkRow        工作区切块，给 search_code
    DutyAlertRow        金丝雀/webhook 告警收件箱
    DutyPendingRow      企微/钉钉批准卡 token
    CustomModelRow      用户加的模型
    InstalledPluginRow  工作台插件是否启用
    UserRow / AuthSessionRow  登录
"""

from app.db.base import Base
from app.models.agent_state import AgentStateRow
from app.models.audit import AuditRow
from app.models.backup import FileBackupRow
from app.models.chunk import FileChunkRow
from app.models.duty import DutyAlertRow, DutyPendingRow
from app.models.llm import CustomModelRow
from app.models.message import MessageRow
from app.models.plugin import InstalledPluginRow
from app.models.session import SessionRow
from app.models.tool_call import ToolCallRow
from app.models.user import AuthSessionRow, UserRow

__all__ = [
    "Base",
    "AgentStateRow",
    "AuditRow",
    "FileBackupRow",
    "CustomModelRow",
    "DutyAlertRow",
    "DutyPendingRow",
    "FileChunkRow",
    "InstalledPluginRow",
    "MessageRow",
    "SessionRow",
    "ToolCallRow",
    "AuthSessionRow",
    "UserRow",
]
