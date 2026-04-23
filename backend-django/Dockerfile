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
    TZ=Asia/Shanghai \
    LANG=C.UTF-8

WORKDIR /app

# 系统依赖（psycopg2-binary 已含 C 库，无需 libpq-dev；保留 tzdata 用于正确时区）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tzdata \
        netcat-openbsd \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# ---- Python 依赖（利用 buildx 缓存） ----
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple -r requirements.txt \
    || pip install -r requirements.txt

# ---- 项目源码 ----
COPY . /app

# 预创建运行期目录
RUN mkdir -p /app/logs /var/lib/zq-platform/douyin

# 入口脚本：根据 ROLE 环境变量决定启动 web / scheduler / douyin-worker
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]
