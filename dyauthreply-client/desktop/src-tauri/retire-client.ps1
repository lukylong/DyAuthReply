param(
  [Parameter(Mandatory=$true)][string]$InstallDir,
  [Parameter(Mandatory=$true)][string]$MainBinary,
  # Report only: exit 10 when this installation still has client processes, 0 otherwise.
  [switch]$Probe
)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$mainPath = Join-Path $root ($MainBinary + '.exe')
$legacyPaths = @((Join-Path $root 'launcher.exe'),(Join-Path $root 'launcher-x86_64-pc-windows-msvc.exe'))
$nativePath = Join-Path $root 'dy-agent.exe'
function Get-ClientProcesses {
  @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $nativePath -or $_.ExecutablePath -eq $mainPath -or $_.ExecutablePath -in $legacyPaths })
}
function Wait-Drained([int]$Seconds) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  do {
    if ((Get-ClientProcesses).Count -eq 0) { return $true }
    Start-Sleep -Milliseconds 250
  } while ((Get-Date) -lt $deadline)
  return $false
}
function Stop-Exact($p) {
  # Re-check identity so a recycled PID is never terminated.
  $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ProcessId)"
  if ($current -and $current.CreationDate -eq $p.CreationDate -and $current.ExecutablePath -eq $p.ExecutablePath) {
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
}
if ($Probe) {
  if ((Get-ClientProcesses).Count -gt 0) { exit 10 }
  exit 0
}
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
# Closing the GUI closes dy-agent's stdin, which starts its bounded graceful drain.
foreach ($p in $selected.Values) {
  if ($p.ExecutablePath -ne $nativePath) { Stop-Exact $p }
}
if (Wait-Drained 20) { exit 0 }
# Still running after the graceful window: dy-agent storage is crash-safe (WAL, durable
# receipts, fenced and reconciled sends), so terminating it cannot corrupt state, while
# leaving it running would lock its executable and make the install fail half-way.
foreach ($p in @(Get-ClientProcesses)) { Stop-Exact $p }
if (Wait-Drained 15) { exit 0 }
Write-Error 'Client processes could not be stopped. Installation was not started.'
exit 1
