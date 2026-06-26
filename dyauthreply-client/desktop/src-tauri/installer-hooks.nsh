; Tauri NSIS 自定义安装钩子
; 目的：覆盖安装前结束正在运行的旧进程（主程序 + 后端 sidecar），
;       避免文件被占用 / 8765 端口被旧实例占用，导致装完新版后端无法启动。
; 字段对接：tauri.conf.json > bundle.windows.nsis.installerHooks 指向本文件。

!macro NSIS_HOOK_PREINSTALL
  ; 结束主程序（${MAINBINARYNAME} 由 Tauri 模板根据 productName 注入，避免硬编码中文名/编码问题）
  nsExec::Exec 'taskkill /F /T /IM "${MAINBINARYNAME}.exe"'
  Pop $0

  ; 结束 PyInstaller 后端 sidecar（externalBin 打包，可能保留 target triple 名或被去除）
  nsExec::Exec 'taskkill /F /T /IM "launcher-x86_64-pc-windows-msvc.exe"'
  Pop $0
  nsExec::Exec 'taskkill /F /T /IM "launcher.exe"'
  Pop $0

  ; 给系统一点时间释放文件锁与端口
  Sleep 1500
!macroend
