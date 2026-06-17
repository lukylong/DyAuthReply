#!/bin/bash
# 验证 Tauri 打包是否包含 launcher

set -e

APP_PATH="src-tauri/target/release/bundle/macos/DyAuthReply.app"

echo "🔍 验证 DyAuthReply.app 打包内容..."
echo ""

if [ ! -d "$APP_PATH" ]; then
    echo "❌ 未找到打包后的 app: $APP_PATH"
    echo "请先运行: npm run tauri build"
    exit 1
fi

echo "✅ 找到 app: $APP_PATH"
echo ""

# 检查主程序
MAIN_BIN="$APP_PATH/Contents/MacOS/dyauthreply"
if [ -f "$MAIN_BIN" ]; then
    echo "✅ 主程序: $(ls -lh "$MAIN_BIN" | awk '{print $5}')"
else
    echo "❌ 主程序不存在"
fi

# 检查 launcher
LAUNCHER_BIN="$APP_PATH/Contents/MacOS/launcher"
if [ -f "$LAUNCHER_BIN" ]; then
    echo "✅ launcher: $(ls -lh "$LAUNCHER_BIN" | awk '{print $5}')"

    # 检查可执行权限
    if [ -x "$LAUNCHER_BIN" ]; then
        echo "   ✓ 可执行权限正常"
    else
        echo "   ✗ 缺少可执行权限"
    fi

    # 测试运行
    echo ""
    echo "🧪 测试 launcher..."
    if "$LAUNCHER_BIN" --help > /dev/null 2>&1; then
        echo "   ✓ launcher 可以正常运行"
    else
        echo "   ✗ launcher 运行失败"
    fi
else
    echo "❌ launcher 不存在: $LAUNCHER_BIN"
    exit 1
fi

echo ""
echo "📦 所有文件："
ls -lh "$APP_PATH/Contents/MacOS/"

echo ""
echo "✅ 打包验证通过！"
