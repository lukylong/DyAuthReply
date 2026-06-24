#!/bin/bash
# 客户端公告 API 测试脚本

BASE_URL="${1:-http://localhost:8000}"

echo "======================================"
echo "客户端公告 API 测试"
echo "Base URL: $BASE_URL"
echo "======================================"

echo -e "\n1. 测试客户端获取公告 API (无需认证)"
echo "GET $BASE_URL/api/client/v1/announcements"
curl -s "$BASE_URL/api/client/v1/announcements?limit=10" | python3 -m json.tool

echo -e "\n\n2. 测试管理后台公告列表 API (需要认证)"
echo "GET $BASE_URL/api/core/client-announcement"
echo "注意：此 API 需要 JWT Token，请在管理后台登录后测试"

echo -e "\n\n======================================"
echo "测试完成"
echo "======================================"
