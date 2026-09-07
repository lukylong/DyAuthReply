; Scope retirement to this installation, never another application's launcher.exe.
!macro NSIS_HOOK_PREINSTALL
  InitPluginsDir
  File /oname=$PLUGINSDIR\retire-client.ps1 "${__FILEDIR__}\retire-client.ps1"
  nsExec::ExecToStack 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\retire-client.ps1" -InstallDir "$INSTDIR" -MainBinary "${MAINBINARYNAME}"'
  Pop $0
  Pop $1
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONEXCLAMATION "Client processes are still running. Please exit the previous client and retry installation."
    Abort
  ${EndIf}
!macroend
