#!/bin/bash
# -----------------------------------------------------------------------------
# 前端构建并同步至 Django 后端目录的脚本
# -----------------------------------------------------------------------------
set -e

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
PROJECT_ROOT="${SCRIPT_DIR}"

echo "🚀 开始构建前端项目..."
cd "${PROJECT_ROOT}/web"

# 安装依赖
pnpm install --frozen-lockfile

# 运行构建（仅针对 @vben/web-ele 主前端应用）
pnpm run build:ele

echo "📦 同步构建产物至 Django 后端目录..."
rm -rf "${PROJECT_ROOT}/backend-django/dist"
cp -r "${PROJECT_ROOT}/web/apps/web-ele/dist" "${PROJECT_ROOT}/backend-django/dist"

echo "🎉 前端构建与同步完成！现在可以直接通过 Django 服务了。"
