[CmdletBinding()]
param([switch]$OpenBrowser)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$serverPath = Join-Path $PSScriptRoot 'server.py'
$runtimePath = Join-Path $PSScriptRoot 'logs\service'
$pidPath = Join-Path $runtimePath 'avc-server.pid'
$stdoutPath = Join-Path $runtimePath 'avc-server.stdout.log'
$stderrPath = Join-Path $runtimePath 'avc-server.stderr.log'
$url = 'http://127.0.0.1:8765/'
$healthUrl = $url + 'api/version'

function Test-AvcHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-AvcIdentity {
    try {
        return Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    } catch {
        return $null
    }
}

function Get-AttributedServerPid {
    param([object]$Identity)
    if ($null -eq $Identity -or $null -eq $Identity.running) {
        throw 'AVC responde, pero no informó su identidad de ejecución.'
    }
    $reportedRoot = [IO.Path]::GetFullPath([string]$Identity.running.installation).TrimEnd('\')
    $expectedRoot = [IO.Path]::GetFullPath($projectRoot).TrimEnd('\')
    if (-not [string]::Equals($reportedRoot, $expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "El puerto 8765 pertenece a otra instalación de AVC: $reportedRoot"
    }
    $reportedPid = 0
    if (-not [int]::TryParse([string]$Identity.running.pid, [ref]$reportedPid) -or $reportedPid -le 0) {
        throw 'AVC responde, pero no informó un PID válido.'
    }
    return $reportedPid
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "No existe el Python de AVC: $pythonPath"
}
if (-not (Test-Path -LiteralPath $serverPath -PathType Leaf)) {
    throw "No existe el servidor de AVC: $serverPath"
}

New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null

if (Test-AvcHealth) {
    $runningPid = Get-AttributedServerPid (Get-AvcIdentity)
    Set-Content -LiteralPath $pidPath -Value $runningPid -Encoding ascii
    if ($OpenBrowser) { Start-Process $url }
    Write-Output "AVC ya está ejecutándose (PID $runningPid); se actualizó su registro local."
    exit 0
}

if (Test-Path -LiteralPath $pidPath -PathType Leaf) {
    $registeredPid = 0
    [void][int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$registeredPid)
    if ($registeredPid -gt 0 -and (Get-Process -Id $registeredPid -ErrorAction SilentlyContinue)) {
        throw "Existe un proceso AVC registrado (PID $registeredPid), pero no responde. Revisá $stderrPath antes de detenerlo."
    }
    Remove-Item -LiteralPath $pidPath -Force
}

$serverProcess = Start-Process -FilePath $pythonPath `
    -ArgumentList @('-u', $serverPath) `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if ($serverProcess.HasExited) { break }
    if (Test-AvcHealth) { $ready = $true; break }
    Start-Sleep -Milliseconds 500
}

if (-not $ready) {
    if (-not $serverProcess.HasExited) { Stop-Process -Id $serverProcess.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    $detail = if (Test-Path -LiteralPath $stderrPath) {
        (Get-Content -LiteralPath $stderrPath -Tail 12 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
    } else { 'No se generó un registro de error.' }
    throw "AVC no respondió después de iniciarse. $detail"
}

$runningPid = Get-AttributedServerPid (Get-AvcIdentity)
Set-Content -LiteralPath $pidPath -Value $runningPid -Encoding ascii

if ($OpenBrowser) { Start-Process $url }
Write-Output "AVC iniciado en segundo plano (PID $runningPid)."
Write-Output "Registros: $runtimePath"
