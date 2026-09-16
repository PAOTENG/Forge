"""
Forge 后端 Python 包根。

【在 Agent 里的位置】
    FastAPI 进程启动后，所有路由、工具、会话 Runtime 都从本包加载。
    本文件本身不实现业务，只标记这是可 import 的 `app` 包。

【调用关系】
    uvicorn 入口：`app.main:app`
    内部模块互相 import：`from app.services.xxx import ...`
"""
