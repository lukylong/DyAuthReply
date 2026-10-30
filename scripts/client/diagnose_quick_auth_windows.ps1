param(
    [string]$BrowserPath = '',
    [string]$OutputDir = ''
)
$ErrorActionPreference = 'Stop'
if (-not $OutputDir) {
    $OutputDir = Join-Path ([Environment]::GetFolderPath('Desktop')) ('DAssistant-browser-diagnostic-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$report = [ordered]@{ login_attempted = $false; account_data_changed = $false; sandbox_disabled = $false; browser_ready = $false }
$profile = Join-Path $env:TEMP ('DAssistant-browser-probe-' + [guid]::NewGuid().ToString())
$process = $null
$stdoutTask = $null
$stderrTask = $null

function Redact-LocalText([string]$Text) {
    foreach ($path in @($profile, $env:USERPROFILE, $env:APPDATA, $env:TEMP)) {
        if ($path) { $Text = $Text.Replace($path, '<LOCAL_PATH>') }
    }
    return [regex]::Replace($Text, '(?im)^(Cookie|Authorization|X-CSRF-Token):.*$', '$1: <REDACTED>')
}

try {
    $os = Get-CimInstance Win32_OperatingSystem
    $report.os = [ordered]@{ caption = $os.Caption; version = $os.Version; build = $os.BuildNumber; architecture = $os.OSArchitecture }
    $app = Get-Process -Name dyauthreply -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($app -and $app.Path) { $report.client_version = (Get-Item -LiteralPath $app.Path).VersionInfo.ProductVersion }
    if (-not $BrowserPath) {
        $componentRoot = Join-Path $env:APPDATA 'DyAuthReply\agent-v2\components\chromium'
        $manifest = Get-Content -LiteralPath (Join-Path $componentRoot 'current.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $BrowserPath = [IO.Path]::GetFullPath((Join-Path $componentRoot $manifest.executable))
        $allowed = [IO.Path]::GetFullPath($componentRoot).TrimEnd('\') + '\'
        if (-not $BrowserPath.StartsWith($allowed, [StringComparison]::OrdinalIgnoreCase)) { throw 'Component path is outside the managed directory' }
        $report.component_version = $manifest.version
        $report.executable_hash_matches = (Get-FileHash -LiteralPath $BrowserPath -Algorithm SHA256).Hash -eq $manifest.sha256
        if (-not $report.executable_hash_matches) { throw 'Browser executable hash does not match its manifest' }
    }
    $report.browser_version = (Get-Item -LiteralPath $BrowserPath).VersionInfo.ProductVersion
    $report.executable_path_chars = $BrowserPath.Length
    $parent = Split-Path -Parent $BrowserPath
    $inventory = @()
    foreach ($name in @('chrome.exe', 'chrome.dll', 'chrome_elf.dll', 'icudtl.dat', 'resources.pak', 'chrome_100_percent.pak', 'chrome_200_percent.pak', 'v8_context_snapshot.bin', 'locales\en-US.pak')) {
        $file = Join-Path $parent $name
        $exists = Test-Path -LiteralPath $file -PathType Leaf
        $length = 0
        if ($exists) { $length = (Get-Item -LiteralPath $file).Length }
        $inventory += [ordered]@{ name = $name; exists = $exists; bytes = $length }
    }
    $report.resources = $inventory
    if (@($inventory | Where-Object { -not $_.exists -or $_.bytes -eq 0 }).Count -gt 0) { throw 'One or more browser resource files are missing or empty' }
    New-Item -ItemType Directory -Path $profile | Out-Null
    $arguments = @(
        ('--user-data-dir="' + $profile + '"'),
        '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
        '--no-first-run', '--no-default-browser-check', '--disable-sync',
        '--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding',
        '--disable-background-timer-throttling', '--disable-features=CalculateNativeWinOcclusion',
        '--enable-logging=stderr', '--log-level=0', '--new-window', 'about:blank'
    )
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $BrowserPath
    $start.Arguments = $arguments -join ' '
    $start.UseShellExecute = $false
    $start.RedirectStandardError = $true
    $start.RedirectStandardOutput = $true
    $start.CreateNoWindow = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    $null = $process.Start()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $deadline = (Get-Date).AddSeconds(20)
    $activePort = Join-Path $profile 'DevToolsActivePort'
    while ((Get-Date) -lt $deadline) {
        if ($process.HasExited) {
            $process.WaitForExit()
            $report.exit_code = $process.ExitCode
            break
        }
        if (Test-Path -LiteralPath $activePort) {
            $lines = @(Get-Content -LiteralPath $activePort -Encoding UTF8)
            if ($lines.Count -ge 2 -and $lines[0] -match '^\d+$' -and $lines[1].StartsWith('/devtools/browser/')) {
                $report.browser_ready = $true
                break
            }
        }
        Start-Sleep -Milliseconds 100
    }
    if (-not $report.browser_ready -and -not $report.Contains('exit_code')) { $report.startup_timeout = $true }
} catch {
    $report.error = Redact-LocalText $_.Exception.Message
} finally {
    if ($process -and -not $process.HasExited) {
        # Kill only the disposable blank browser created by this script.
        & taskkill.exe /PID $process.Id /T /F 2>&1 | Out-Null
        $null = $process.WaitForExit(10000)
    }
    foreach ($item in @(@('stdout', $stdoutTask), @('stderr', $stderrTask))) {
        if ($item[1] -and $item[1].Wait(3000)) {
            $text = Redact-LocalText $item[1].Result
            if ($text.Length -gt 65536) { $text = $text.Substring($text.Length - 65536) }
            [IO.File]::WriteAllText((Join-Path $OutputDir ($item[0] + '.log')), $text, [Text.Encoding]::UTF8)
        }
    }
    if (Test-Path -LiteralPath $profile) { Remove-Item -LiteralPath $profile -Recurse -Force -ErrorAction SilentlyContinue }
    $report.profile_removed = -not (Test-Path -LiteralPath $profile)
    [IO.File]::WriteAllText((Join-Path $OutputDir 'report.json'), ($report | ConvertTo-Json -Depth 8), [Text.Encoding]::UTF8)
    Compress-Archive -Path (Join-Path $OutputDir '*') -DestinationPath ($OutputDir + '.zip') -Force
    Write-Output ('DIAGNOSTIC_BROWSER_READY=' + $report.browser_ready)
    Write-Output ('REPORT_ZIP=' + $OutputDir + '.zip')
}
