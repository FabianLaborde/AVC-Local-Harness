[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Start', 'Stop')]
    [string]$Action
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$healthUrl = 'http://127.0.0.1:8765/api/version'
$workerPath = Join-Path $PSScriptRoot ($(if ($Action -eq 'Start') { 'Start-AVC.ps1' } else { 'Stop-AVC.ps1' }))
$runId = [Guid]::NewGuid().ToString('N')
$outputPath = Join-Path ([IO.Path]::GetTempPath()) "avc-$($Action.ToLowerInvariant())-$runId.out.log"
$errorPath = Join-Path ([IO.Path]::GetTempPath()) "avc-$($Action.ToLowerInvariant())-$runId.err.log"
$labels = if ($Action -eq 'Start') {
    @('Verificando instalacion', 'Iniciando servicio', 'Confirmando respuesta', 'Abriendo AVC')
} else {
    @('Verificando servicio', 'Deteniendo procesos', 'Confirmando cierre', 'AVC detenido')
}
$themeColor = $(if ($Action -eq 'Start') {
    [Drawing.Color]::FromArgb(65, 210, 125)
} else {
    [Drawing.Color]::FromArgb(255, 105, 105)
})

function Test-AvcHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 1
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Convert-ToAsciiText {
    param([string]$Text)
    if (-not $Text) { return '' }
    $normalized = $Text.Normalize([Text.NormalizationForm]::FormD)
    $builder = New-Object Text.StringBuilder
    foreach ($character in $normalized.ToCharArray()) {
        if ([Globalization.CharUnicodeInfo]::GetUnicodeCategory($character) -ne
                [Globalization.UnicodeCategory]::NonSpacingMark -and [int]$character -le 127) {
            [void]$builder.Append($character)
        }
    }
    return $builder.ToString()
}

function Read-ResultText {
    $parts = @()
    if (Test-Path -LiteralPath $outputPath) {
        $parts += (Get-Content -LiteralPath $outputPath -Tail 8 -ErrorAction SilentlyContinue)
    }
    if (Test-Path -LiteralPath $errorPath) {
        $parts += (Get-Content -LiteralPath $errorPath -Tail 8 -ErrorAction SilentlyContinue)
    }
    return Convert-ToAsciiText ((($parts | Where-Object { $_ -and $_.Trim() }) -join [Environment]::NewLine).Trim())
}

$form = New-Object Windows.Forms.Form
$form.Text = $(if ($Action -eq 'Start') { 'Iniciando AVC' } else { 'Deteniendo AVC' })
$form.AutoScaleMode = [Windows.Forms.AutoScaleMode]::Dpi
$form.ClientSize = New-Object Drawing.Size(500, 330)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.ControlBox = $false
$form.TopMost = $true
$form.BackColor = [Drawing.Color]::FromArgb(24, 27, 34)
$form.ForeColor = $themeColor

$title = New-Object Windows.Forms.Label
$title.Text = $(if ($Action -eq 'Start') { 'Preparando Aider Vision and Code' } else { 'Cerrando Aider Vision and Code' })
$title.Font = New-Object Drawing.Font('Segoe UI', 13, [Drawing.FontStyle]::Bold)
$title.Location = New-Object Drawing.Point(18, 15)
$title.Size = New-Object Drawing.Size(464, 28)
$title.ForeColor = $themeColor
$form.Controls.Add($title)

$stageLabels = @()
for ($index = 0; $index -lt $labels.Count; $index++) {
    $stage = New-Object Windows.Forms.Label
    $stage.Text = '[ ]  ' + $labels[$index]
    $stage.Font = New-Object Drawing.Font('Segoe UI', 10)
    $stage.Location = New-Object Drawing.Point(24, (55 + 34 * $index))
    $stage.Size = New-Object Drawing.Size(450, 26)
    $stage.ForeColor = $themeColor
    $form.Controls.Add($stage)
    $stageLabels += $stage
}

$detail = New-Object Windows.Forms.Label
$detail.Text = 'Esperando inicio...'
$detail.Location = New-Object Drawing.Point(20, 198)
$detail.Size = New-Object Drawing.Size(460, 72)
$detail.Font = New-Object Drawing.Font('Segoe UI', 9)
$detail.ForeColor = $themeColor
$form.Controls.Add($detail)

$closeButton = New-Object Windows.Forms.Button
$closeButton.Text = 'Cerrar'
$closeButton.Location = New-Object Drawing.Point(392, 286)
$closeButton.Size = New-Object Drawing.Size(88, 28)
$closeButton.ForeColor = $themeColor
$closeButton.BackColor = [Drawing.Color]::FromArgb(38, 42, 52)
$closeButton.Visible = $false
$closeButton.Add_Click({ $form.Close() })
$form.Controls.Add($closeButton)

function Set-Stage {
    param([int]$Index, [ValidateSet('pending','active','done','error')][string]$State)
    $prefix = switch ($State) { 'active' { '[...]' }; 'done' { '[OK]' }; 'error' { '[!]' }; default { '[ ]' } }
    $color = $(if ($State -eq 'error') { [Drawing.Color]::FromArgb(255, 100, 100) } else { $themeColor })
    $stageLabels[$Index].Text = "$prefix  $($labels[$Index])"
    $stageLabels[$Index].ForeColor = $color
}

$state = @{
    Worker = $null
    FinishedAt = $null
    Failed = $false
}
$started = [Diagnostics.Stopwatch]::StartNew()
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 250

function Complete-Progress {
    for ($i = 0; $i -lt $stageLabels.Count; $i++) { Set-Stage $i done }
    $detail.Text = $(if ($Action -eq 'Start') { 'AVC esta disponible. Se abrira el navegador.' } else { 'AVC se detuvo correctamente.' })
    $state.FinishedAt = [DateTime]::UtcNow
}

function Fail-Progress {
    param([string]$Message)
    $state.Failed = $true
    $active = 0
    for ($i = 0; $i -lt $stageLabels.Count; $i++) {
        if ($stageLabels[$i].Text.StartsWith('[...]')) { $active = $i; break }
    }
    Set-Stage $active error
    $detail.Text = Convert-ToAsciiText $(if ($Message) { $Message } else { 'La operacion no pudo completarse. Revisa los registros.' })
    $detail.ForeColor = [Drawing.Color]::FromArgb(255, 130, 130)
    $closeButton.Visible = $true
    $form.ControlBox = $true
    $timer.Stop()
}

$timer.Add_Tick({
    try {
        $worker = $state.Worker
        if ($null -eq $worker) { return }
        $healthy = Test-AvcHealth
        if ($Action -eq 'Start') {
            Set-Stage 0 done
            if (-not $worker.HasExited) { Set-Stage 1 active }
            if ($healthy) { Set-Stage 1 done; Set-Stage 2 done; Set-Stage 3 active }
        } else {
            Set-Stage 0 done
            if (-not $worker.HasExited) { Set-Stage 1 active }
            if (-not $healthy) { Set-Stage 1 done; Set-Stage 2 done; Set-Stage 3 active }
        }

        if ($worker.HasExited) {
            $expectedState = $(if ($Action -eq 'Start') { $healthy } else { -not $healthy })
            if ($worker.ExitCode -ne 0) {
                Fail-Progress (Read-ResultText)
            } elseif ($expectedState -and $null -eq $state.FinishedAt) {
                Complete-Progress
            } elseif ($started.Elapsed.TotalSeconds -gt 25) {
                Fail-Progress (Read-ResultText)
            } else {
                Set-Stage 2 active
            }
        } elseif ($started.Elapsed.TotalSeconds -gt 40) {
            Fail-Progress 'La operacion continua sin confirmar el estado final. Revisa los registros del servicio.'
        }

        if ($null -ne $state.FinishedAt -and
                ([DateTime]::UtcNow - $state.FinishedAt).TotalSeconds -ge 3) {
            $timer.Stop()
            $form.Close()
        }
    } catch {
        Fail-Progress $_.Exception.Message
    }
})

$form.Add_Shown({
    try {
        Set-Stage 0 active
        $detail.Text = 'Comprobando el estado real del servicio...'
        if (-not (Test-Path -LiteralPath $workerPath -PathType Leaf)) {
            throw "No existe el controlador: $workerPath"
        }
        $arguments = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                       '-File', ('"' + $workerPath + '"'))
        if ($Action -eq 'Start') { $arguments += '-OpenBrowser' }
        $state.Worker = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments `
            -WindowStyle Hidden -RedirectStandardOutput $outputPath `
            -RedirectStandardError $errorPath -PassThru
        $detail.Text = $(if ($Action -eq 'Start') { 'El servidor se esta iniciando en segundo plano...' } else { 'El servicio se esta cerrando en segundo plano...' })
        $timer.Start()
    } catch {
        Fail-Progress $_.Exception.Message
    }
})

$form.Add_FormClosed({
    $timer.Stop()
    Remove-Item -LiteralPath $outputPath, $errorPath -Force -ErrorAction SilentlyContinue
})

[void][Windows.Forms.Application]::Run($form)
if ($state.Failed) { exit 1 }
