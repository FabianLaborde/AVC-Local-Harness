[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = (Resolve-Path -LiteralPath (Join-Path $projectRoot '.venv\Scripts\python.exe')).Path
$pidPath = Join-Path $PSScriptRoot 'logs\service\avc-server.pid'
$healthUrl = 'http://127.0.0.1:8765/api/version'

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
        throw 'AVC responde, pero no informó su identidad de ejecución; no se detendrá.'
    }
    $reportedRoot = [IO.Path]::GetFullPath([string]$Identity.running.installation).TrimEnd('\')
    $expectedRoot = [IO.Path]::GetFullPath($projectRoot).TrimEnd('\')
    if (-not [string]::Equals($reportedRoot, $expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "El puerto 8765 pertenece a otra instalación de AVC ($reportedRoot); no se detendrá."
    }
    $reportedPid = 0
    if (-not [int]::TryParse([string]$Identity.running.pid, [ref]$reportedPid) -or $reportedPid -le 0) {
        throw 'AVC responde, pero no informó un PID válido; no se detendrá.'
    }
    return $reportedPid
}

if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
    Write-Output 'AVC no tiene un proceso oculto registrado.'
    exit 0
}

$identity = Get-AvcIdentity
if ($null -ne $identity) {
    $serverPid = Get-AttributedServerPid $identity
} else {
    $serverPid = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$serverPid) -or $serverPid -le 0) {
        throw 'El archivo PID de AVC no es válido; no se detendrá ningún proceso.'
    }
}

$serverProcess = Get-Process -Id $serverPid -ErrorAction SilentlyContinue
if (-not $serverProcess) {
    Remove-Item -LiteralPath $pidPath -Force
    Write-Output 'El proceso registrado ya no existe; se limpió el PID obsoleto.'
    exit 0
}

if ($null -eq $identity -and (-not $serverProcess.Path -or
        [IO.Path]::GetFullPath($serverProcess.Path) -ne [IO.Path]::GetFullPath($pythonPath))) {
    throw "AVC no responde y el PID $serverPid no corresponde al Python de esta instalación; no se detendrá."
}

& taskkill.exe /PID $serverPid /T /F | Out-Null
if ($LASTEXITCODE -ne 0) { throw "No se pudo detener el árbol de AVC (PID $serverPid)." }
Remove-Item -LiteralPath $pidPath -Force
Write-Output 'AVC detenido correctamente.'
