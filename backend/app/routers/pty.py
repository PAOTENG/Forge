from __future__ import annotations

import asyncio

"""
用户终端：WebSocket → docker exec TTY 进当前会话沙箱。标题栏「终端」用。

业务：你自己敲命令，和模型 bash 共用同一只笼子（ensure 后再 exec /bin/bash）。
若还没点过排查/新建会话，这里会第一次把容器拉起来。
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db.session import get_session_factory
from app.models.session import SessionRow
from app.services.sandbox import sandbox_manager
from app.services.workspace import resolve_workspace

router = APIRouter()


def _raw_socket(sock):
    return getattr(sock, "_sock", None) or sock


@router.websocket("/sessions/{session_id}/pty")
async def session_pty(ws: WebSocket, session_id: str):
    """
    【业务】工作台点「终端」：键进去的是沙箱里的 bash，工作目录 /workspace（挂着的工作区）。
    【什么时候用】会话已经存在，你打开终端面板。
    【谁调用】前端 WebSocket。
    【里面调用】sandbox_manager.ensure，再 docker exec_create /bin/bash。
    【输入】路径里的 session_id。无返回值（长连接）。
    """
    await ws.accept()
    factory = get_session_factory()
    async with factory() as db:
        row = await db.get(SessionRow, session_id)
        if not row:
            await ws.send_text("session not found\r\n")
            await ws.close()
            return
        workspace = resolve_workspace(row.workspace_path)
    container_id = sandbox_manager.ensure(session_id, workspace)
    client = sandbox_manager._client
    exec_id = client.api.exec_create(
        container_id,
        "/bin/bash",
        stdin=True,
        tty=True,
        workdir="/workspace",
    )["Id"]
    sock = client.api.exec_start(exec_id, tty=True, stream=True, socket=True)
    raw = _raw_socket(sock)
    await ws.send_text("\r\n# interactive sandbox · /workspace\r\n")

    loop = asyncio.get_running_loop()
    closed = asyncio.Event()

    def read_docker():
        try:
            while not closed.is_set():
                chunk = raw.recv(4096)
                if not chunk:
                    break
                asyncio.run_coroutine_threadsafe(ws.send_bytes(chunk), loop).result()
        except Exception:
            pass
        finally:
            closed.set()

    reader = asyncio.create_task(asyncio.to_thread(read_docker))
    try:
        while not closed.is_set():
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            data = msg.get("bytes") or (msg.get("text") or "").encode("utf-8")
            if data:
                await asyncio.to_thread(raw.sendall, data)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        closed.set()
        reader.cancel()
        try:
            raw.close()
        except Exception:
            pass
