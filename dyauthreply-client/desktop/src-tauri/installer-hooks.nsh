;; Client retirement for installs and upgrades. Scoped to this installation's exact
;; executable paths; never another application's launcher.exe or dy-agent.exe.
;;
;; This file is !included before installer.nsi defines PRODUCTNAME/MAINBINARYNAME and its
;; variables, so code here uses runtime state ($INSTDIR is already the previous install
;; location after .onInit) and the identity pinned by check_release_contract.mjs.
;; Saved as UTF-8 with BOM: makensis is run without -INPUTCHARSET.
!include LogicLib.nsh
!include FileFunc.nsh

!define DY_MAIN_BINARY "dyauthreply"
!define DY_PRODUCT "D助手"
!if /FileExists "${__FILEDIR__}\retire-client.ps1"
  !define DY_RETIRE_SOURCE "${__FILEDIR__}\retire-client.ps1"
!else
  !error "retire-client.ps1 must sit next to installer-hooks.nsh"
!endif

; Runs retire-client.ps1 for $INSTDIR. ARGS: extra script flags. Exit code -> $0.
!macro DyRunRetire ARGS
  InitPluginsDir
  File /oname=$PLUGINSDIR\retire-client.ps1 "${DY_RETIRE_SOURCE}"
  nsExec::ExecToStack 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\retire-client.ps1" -InstallDir "$INSTDIR" -MainBinary "${DY_MAIN_BINARY}" ${ARGS}'
  Pop $0
  Pop $1
!macroend

; Stops this installation's client, offering a retry instead of failing half-way.
; ID: unique label suffix. ONCANCEL: Abort (in a section) or Quit (in a function).
!macro DyRetireWithRetry ID ONCANCEL
  dy_retire_retry_${ID}:
  !insertmacro DyRunRetire ""
  ${If} $0 != 0
    IfSilent dy_retire_cancel_${ID}
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "${DY_PRODUCT} 仍有后台进程未能退出，安装暂未开始。$\n$\n请稍候几秒后点击「重试」。如仍失败，可在任务管理器中结束 dy-agent.exe 后再重试。" IDRETRY dy_retire_retry_${ID}
    dy_retire_cancel_${ID}:
    ${ONCANCEL}
  ${EndIf}
!macroend

; Before any page: the reinstall page's default choice runs the PREVIOUS version's
; uninstaller, which only kills the GUI and then fails on the still-draining dy-agent.exe,
; leaving a half-removed install. Stop everything first, with the user's consent.
!define MUI_CUSTOMFUNCTION_GUIINIT DyRetireBeforePages
Function DyRetireBeforePages
  Push $0
  Push $1
  ; In-app updates (/UPDATE) arrive after the client stopped itself; PREINSTALL verifies.
  ClearErrors
  ${GetOptions} $CMDLINE "/UPDATE" $1
  ${If} ${Errors}
    !insertmacro DyRunRetire "-Probe"
    ${If} $0 == 10
      ClearErrors
      ${GetOptions} $CMDLINE "/P" $1
      ${If} ${Errors}
      ${AndIfNot} ${Silent}
        MessageBox MB_OKCANCEL|MB_ICONINFORMATION "检测到 ${DY_PRODUCT} 正在运行。$\n$\n继续安装需要先关闭它，未完成的任务会被安全保存，安装完成后即可重新打开。$\n$\n点击「确定」自动关闭并继续安装，点击「取消」退出安装。" IDOK dy_close_confirmed
        Quit
      ${EndIf}
      dy_close_confirmed:
      !insertmacro DyRetireWithRetry gui Quit
    ${EndIf}
  ${EndIf}
  Pop $1
  Pop $0
FunctionEnd

; Last guard right before files are written (covers silent installs and in-app updates).
!macro NSIS_HOOK_PREINSTALL
  !insertmacro DyRetireWithRetry preinstall Abort
!macroend
