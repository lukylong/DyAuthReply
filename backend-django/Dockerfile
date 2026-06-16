# zq-platform backend-django image
# 抖音模块已全面转为纯 HTTP 协议（JS 签名 dy_ab.js，无 Chromium/Playwright），
# 因此基础镜像用 python:3.11-slim：体积小，仅需 Node（跑 dy_ab.js）+ 少量系统库。

FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8

# 服务器本地 docker build 时若某镜像站慢，可 --build-arg 覆盖，例如清华源：
#   --build-arg PIP_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple
#   --build-arg PIP_TRUSTED_HOST=mirrors.tuna.tsinghua.edu.cn
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

WORKDIR /app

# 系统依赖（psycopg2-binary 已含 C 库，无需 libpq-dev；保留 tzdata 用于正确时区）
# 换清华源加速 apt（Debian bookworm，deb822 格式）；tzdata 预置 debconf 无人值守
RUN ( sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g; s|http://security.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true ) \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && echo "tzdata tzdata/Areas select Asia" | debconf-set-selections \
    && echo "tzdata tzdata/Zones/Asia select Shanghai" | debconf-set-selections \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        tzdata \
        netcat-openbsd \
        curl \
        ca-certificates \
        xz-utils \
        build-essential \
        pkg-config \
        unixodbc-dev \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# ---- Node.js 20（JS 签名引擎：execjs 需要 node 运行 dy_ab.js）----
# 基础镜像未在 PATH 暴露 node/npm，显式安装；从 npmmirror 取二进制（国内快，自动适配 amd64/arm64）
RUN ARCH=$(dpkg --print-architecture) \
    && case "$ARCH" in \
        amd64) NODEARCH=x64 ;; \
        arm64) NODEARCH=arm64 ;; \
        *) echo "unsupported arch: $ARCH" && exit 1 ;; \
    esac \
    && curl -fsSL "https://cdn.npmmirror.com/binaries/node/v20.18.0/node-v20.18.0-linux-${NODEARCH}.tar.xz" -o /tmp/node.tar.xz \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.xz \
    && node --version && npm --version

# ---- Python 依赖（全程国内 PyPI 镜像；不升级 pip，避免访问 files.pythonhosted.org） ----
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    printf '[global]\nindex-url = %s\ntrusted-host = %s\ntimeout = 300\nretries = 10\n' \
      "${PIP_INDEX_URL}" "${PIP_TRUSTED_HOST}" > /etc/pip.conf \
    && pip install -r requirements.txt

# ---- 项目源码 ----
COPY . /app

# ---- 抖音 JS 签名引擎依赖 ----
# dy_ab.js 需要 jsrsasign；node_modules 不入仓。bind mount 场景由 entrypoint 兜底安装。
RUN cd /app/core/douyin/runtime/transport/sign/js \
    && npm ci --no-audit --no-fund --registry=https://registry.npmmirror.com

# 预创建运行期目录
RUN mkdir -p /app/logs /var/lib/zq-platform/douyin

# 入口脚本：根据 ROLE 环境变量决定启动 web / scheduler / douyin-worker
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]
