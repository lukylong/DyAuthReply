# syntax=docker/dockerfile:1.7
#
# zq-platform backend-django image
# 基础镜像选择 Playwright 官方镜像 —— 已预装 Chromium / 字体 / libnss3 等依赖，
# 无需再手动 apt-get，后续 `core.douyin` worker 可直接跑浏览器自动化。
# 若不需要抖音模块，可把 FROM 换成 python:3.11-slim 以获得更小体积。

FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8

WORKDIR /app

# 系统依赖（psycopg2-binary 已含 C 库，无需 libpq-dev；保留 tzdata 用于正确时区）
# 换清华源加速 apt；tzdata 预置 debconf 完全无人值守
RUN sed -i 's|http://archive.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g; s|http://security.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && echo "tzdata tzdata/Areas select Asia" | debconf-set-selections \
    && echo "tzdata tzdata/Zones/Asia select Shanghai" | debconf-set-selections \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        tzdata \
        netcat-openbsd \
        build-essential \
        pkg-config \
        unixodbc-dev \
        xvfb \
        x11vnc \
        fluxbox \
        novnc \
        websockify \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# ---- Python 依赖（利用 buildx 缓存） ----
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -U "pip<25" "setuptools<81" "packaging>=24.1" wheel \
    && \
    pip install -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple -r requirements.txt \
    || pip install -r requirements.txt

# ---- 项目源码 ----
COPY . /app

# 预创建运行期目录
RUN mkdir -p /app/logs /var/lib/zq-platform/douyin

# 入口脚本：根据 ROLE 环境变量决定启动 web / scheduler / douyin-worker
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000 5900 6080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]
