[CmdletBinding()]
param()

$pidPath = Join-Path $PSScriptRoot 'logs\service\avc-server.pid'
$healthUrl = 'http://127.0.0.1:8765/api/version'
$serverPid = 0
$registered = (Test-Path -LiteralPath $pidPath -PathType Leaf) -and
    [int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$serverPid) -and
    $serverPid -gt 0 -and $null -ne (Get-Process -Id $serverPid -ErrorAction SilentlyContinue)
try {
    $version = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
    $label = if ($version.running.version) { $version.running.version } elseif ($version.version) { $version.version } else { 'versión no informada' }
    Write-Output "AVC disponible: $label - http://127.0.0.1:8765/"
    Write-Output ($(if ($registered) { "Proceso oculto registrado: PID $serverPid" } else { 'Proceso no registrado por el controlador oculto.' }))
    exit 0
} catch {
    Write-Output 'AVC no responde en http://127.0.0.1:8765/.'
    if ($registered) { Write-Output "Proceso registrado sin respuesta: PID $serverPid" }
    exit 1
}
