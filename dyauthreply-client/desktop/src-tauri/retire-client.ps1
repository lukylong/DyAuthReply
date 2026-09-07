param([Parameter(Mandatory=$true)][string]$InstallDir,[Parameter(Mandatory=$true)][string]$MainBinary)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$mainPath = Join-Path $root ($MainBinary + '.exe')
$legacyPaths = @((Join-Path $root 'launcher.exe'),(Join-Path $root 'launcher-x86_64-pc-windows-msvc.exe'))
$nativePath = Join-Path $root 'dy-agent.exe'
# Exact executable paths, not global image-name taskkill. Preserve process creation identity.
$all = @(Get-CimInstance Win32_Process)
$roots = @($all | Where-Object { $_.ExecutablePath -in $legacyPaths })
$selected = @{}
foreach ($p in $roots) { $selected[[int]$p.ProcessId] = $p }
$changed = $true
while ($changed) {
  $changed = $false
  foreach ($p in $all) {
    if (!$selected.ContainsKey([int]$p.ProcessId) -and $selected.ContainsKey([int]$p.ParentProcessId)) {
      $parent = $selected[[int]$p.ParentProcessId]
      if ($p.CreationDate -ge $parent.CreationDate) { $selected[[int]$p.ProcessId] = $p; $changed = $true }
    }
  }
}
# Add only the GUI itself AFTER legacy descendant discovery. Otherwise an in-app installer
# descended from the GUI could accidentally select and terminate itself.
foreach ($p in @($all | Where-Object { $_.ExecutablePath -eq $mainPath })) { $selected[[int]$p.ProcessId] = $p }
# Native children drain when their owner's stdin closes. Never force-kill them while writing.
foreach ($p in $selected.Values) {
  if ($p.ExecutablePath -eq $nativePath) { continue }
  $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ProcessId)"
  if ($current -and $current.CreationDate -eq $p.CreationDate -and $current.ExecutablePath -eq $p.ExecutablePath) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
}
$deadline = (Get-Date).AddSeconds(75)
do {
  $remaining = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $nativePath -or $_.ExecutablePath -eq $mainPath -or $_.ExecutablePath -in $legacyPaths })
  if ($remaining.Count -eq 0) { exit 0 }
  Start-Sleep -Milliseconds 250
} while ((Get-Date) -lt $deadline)
Write-Error 'Client processes have not finished draining. Installation was stopped.'
exit 1
