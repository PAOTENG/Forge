# 会话沙箱镜像：模型 bash / 普通仓 pytest 跑在这里，不是 order-api 自己的镜像。
# 谁编：本机没有 FORGE_SANDBOX_IMAGE 时，sandbox._ensure_image() 用本文件 build。
# 谁跑：ensure() 里 command=sleep infinity，再 exec 进 /workspace（挂载的值班工作区）。
# 注意：默认容器没网（network=none），这里装了 git/npm 也不等于能拉外网或用宿主凭据。
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        git \
        nodejs \
        npm \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
CMD ["sleep", "infinity"]
