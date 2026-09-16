"""
会话级 Docker 沙箱：模型的 bash / 普通仓测试跑在这里，不在你的登录 shell 里。

业务故事：
    你点排查或新建会话时，ensure 起一个长挂着的容器（sleep infinity）。
    工作区挂到 /workspace。默认没网、内存 2g、最多 256 个进程。
    之后模型批准通过的 bash，或工作台「终端」，都是 docker exec 进这个容器。
    Git 凭据、重启 order-api、热修 pytest（.forge-host-tests）不走这里。

设置从哪来：
    镜像/内存/网络 → get_settings()（.env 的 FORGE_SANDBOX_*）
    进程数 256 → 本文件 pids_limit 写死
    镜像配方 → infra/sandbox/Dockerfile（没有镜像才现编）

谁在何时 ensure（容器这时才真正启动，不是 uvicorn 一启动就起）：
    duty.investigate、sessions.create_session / attach_session、
    tools.bash / run_tests（exec 里会再 ensure）、pty.session_pty、评测脚本。
删会话 sessions.delete_session → stop。
"""

from __future__ import annotations

from pathlib import Path

import docker
from docker.errors import DockerException, ImageNotFound, NotFound
from docker.models.containers import Container

from app.core.config import REPO_ROOT, get_settings
from app.services.workspace import docker_bind

_DOCKERFILE_DIR = REPO_ROOT / "infra" / "sandbox"


class SandboxError(RuntimeError):
    """容器起不来时抛给路由，变成 HTTP 500。"""

    pass


class SandboxManager:
    """
    【业务】每个工作台会话对应一个笼子。同一会话反复 ensure 不会起第二个。
            uvicorn 重启后内存缓存空了，按容器名 forge-{id前12位} 还能找回来。
    【谁调用】模块底部的全局 sandbox_manager，路由和工具都用这一份。
    """

    def __init__(self) -> None:
        self._client = docker.from_env()
        self._containers: dict[str, str] = {}

    def _ensure_image(self) -> str:
        """
        【业务】本机还没有沙箱镜像时，用 infra/sandbox 现编一份，以免第一次点排查卡在 pull 外网。
        【什么时候用】ensure 决定要新建容器、取 image 名之前。
        【谁调用】ensure。
        【参数】无。镜像名来自 settings.forge_sandbox_image。
        【里面调用】docker images.get；没有则 images.build(Dockerfile)。
        """
        settings = get_settings()
        image = settings.forge_sandbox_image
        try:
            self._client.images.get(image)
        except ImageNotFound:
            self._client.images.build(
                path=str(_DOCKERFILE_DIR),
                dockerfile="Dockerfile",
                tag=image,
                rm=True,
            )
        return image

    def ensure(self, session_id: str, workspace: Path) -> str:
        """
        【业务】保证「这次值班对话」有一个正在跑的笼子。有就复用，停了就 start，没有才新建。
                新建时才第一次 docker run：挂上工作区、套上没网/2g/256pid。
        【什么时候用】点排查、新建会话、刷新后 attach、终端打开、bash/测试 exec 之前。
                不是 Forge API 启动时批量起沙箱。
        【谁调用】duty.investigate、sessions.create_session/attach、pty、exec 自己、评测。
        【参数】
            session_id：用来拼容器名、做缓存键。
            workspace：宿主上要挂进 /workspace 的目录（热修一般是 order-api）。
        【里面调用】_ensure_image、docker_bind、containers.run / get / start。
        【是否主线】是。没有笼子，后面 bash 没地方跑。
        """
        name = f"forge-{session_id[:12]}"
        existing = self._containers.get(session_id)
        if existing:
            try:
                container = self._client.containers.get(existing)
                if container.status != "running":
                    container.start()
                return existing
            except NotFound:
                self._containers.pop(session_id, None)

        try:
            found = self._client.containers.get(name)
            if found.status != "running":
                found.start()
            self._containers[session_id] = found.id
            return found.id
        except NotFound:
            pass

        image = self._ensure_image()
        settings = get_settings()
        try:
            container = self._client.containers.run(
                image,
                command=["sleep", "infinity"],
                detach=True,
                name=name,
                working_dir="/workspace",
                volumes={docker_bind(workspace): {"bind": "/workspace", "mode": "rw"}},
                network_mode=settings.forge_sandbox_network,
                mem_limit=settings.forge_sandbox_memory,
                pids_limit=256,
                stdin_open=True,
                tty=False,
            )
        except DockerException as exc:
            raise SandboxError(f"Failed to start sandbox: {exc}") from exc
        self._containers[session_id] = container.id
        return container.id

    def exec(self, session_id: str, workspace: Path, command: str, timeout_s: int = 120) -> str:
        """
        【业务】在这个会话的笼子里执行一句命令，把打印还给模型或终端。
                先进 ensure（刷新后容器可能被你手动停过）。
        【什么时候用】人已批准（或评测自动批）之后，bash / 普通仓 run_tests 真正执行时。
        【谁调用】tools.bash、tools.run_tests（无 .forge-host-tests 时）。
        【参数】
            session_id / workspace：找到或拉起哪只笼子、挂哪份代码。
            command：已过安全检查的整句 shell。
            timeout_s：默认 120，包一层 timeout 防止测到死。
        【里面调用】ensure、container.exec_run。
        【是否主线】是。热修 order-api 的 pytest 通常不走这里，走宿主 run_host_tests。
        """
        container_id = self.ensure(session_id, workspace)
        container: Container = self._client.containers.get(container_id)
        wrapped = f"timeout {int(timeout_s)}s bash -lc {command!r}"
        result = container.exec_run(
            ["bash", "-lc", wrapped],
            workdir="/workspace",
            demux=True,
        )
        stdout, stderr = result.output if result.output else (b"", b"")
        out = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        text = out
        if err:
            text = f"{out}\n{err}".strip()
        if result.exit_code not in (0, None):
            return f"[exit {result.exit_code}]\n{text}".strip()
        return text.strip() or "(no output)"

    def stop(self, session_id: str) -> None:
        """
        【业务】这次对话结束了，把笼子拆掉，避免磁盘上堆满 forge-xxxx 容器。
        【什么时候用】工作台删除会话。
        【谁调用】sessions.delete_session；评测收尾。
        【参数】session_id。找不到容器就当已经没了。
        【里面调用】containers.get + remove(force=True)。
        """
        cid = self._containers.pop(session_id, None)
        name = f"forge-{session_id[:12]}"
        for key in (cid, name):
            if not key:
                continue
            try:
                container = self._client.containers.get(key)
                container.remove(force=True)
            except (NotFound, DockerException):
                pass


# 进程里只这一份。路由/工具 import 它，不要各自 new，否则缓存对不上。
sandbox_manager = SandboxManager()
