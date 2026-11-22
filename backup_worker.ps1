param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$Remote,
    [Parameter(Mandatory=$true)][string]$Branch,
    [Parameter(Mandatory=$true)][int]$AnkiProcessId,
    [string]$ReopenPath = '',
    [string]$CancelSignal = '',
    [ValidateSet('en','pt-BR')][string]$Language = 'en',
    [switch]$AllowRemoteUpdate,
    [int]$MaxFileMiB = 95,
    [ValidateSet('dark','light')][string]$Theme = 'dark',
    [switch]$ExternalGui
)

$ErrorActionPreference = 'Stop'
$script:Copied = 0
$script:Unchanged = 0
$script:Removed = 0
$script:TotalBytes = [long]0
$script:TranscriptStarted = $false
$script:Succeeded = $false
$script:Finished = $false
$script:CancelRequested = $false
$script:LargeFiles = New-Object System.Collections.Generic.List[string]
$script:OperationLock = $null

function Acquire-OperationLock {
    $folder = Join-Path $env:LOCALAPPDATA 'GitBridge'
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    $lockFile = Join-Path $folder 'operation.lock'
    try {
        $script:OperationLock = [IO.File]::Open($lockFile, 'OpenOrCreate', 'ReadWrite', 'None')
    } catch {
        throw $(if ($Language -eq 'pt-BR') { 'Outra operação do GitBridge já está em andamento.' }
                else { 'Another GitBridge operation is already running.' })
    }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$script:Form = New-Object System.Windows.Forms.Form
$script:Form.Text = 'GitBridge'
$script:Form.Width = 760
$script:Form.Height = 540
$script:Form.StartPosition = 'CenterScreen'
$script:Form.MinimumSize = New-Object System.Drawing.Size(650, 440)
$script:Form.Font = New-Object System.Drawing.Font('Segoe UI', 9)
$script:Form.BackColor = [System.Drawing.Color]::FromArgb(31, 32, 36)
$script:Form.ForeColor = [System.Drawing.Color]::FromArgb(232, 233, 235)

# Windows 10/11: ask DWM for a dark title bar, without requiring that API.
try {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AnkiBackupDwm {
    [DllImport("dwmapi.dll", PreserveSig=true)]
    public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);
}
'@
    $darkMode = 1
    $dwmResult = [AnkiBackupDwm]::DwmSetWindowAttribute($script:Form.Handle, 20, [ref]$darkMode, 4)
    if ($dwmResult -ne 0) {
        $dwmResult = [AnkiBackupDwm]::DwmSetWindowAttribute($script:Form.Handle, 19, [ref]$darkMode, 4)
    }
} catch { }

$script:Header = New-Object System.Windows.Forms.Panel
$script:Header.BackColor = [System.Drawing.Color]::FromArgb(38, 40, 45)
$script:Header.Dock = 'Top'
$script:Header.Height = 68
$script:Form.Controls.Add($script:Header)
$script:TitleLabel = New-Object System.Windows.Forms.Label
$script:TitleLabel.Text = 'GitBridge'
$script:TitleLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 15)
$script:TitleLabel.ForeColor = [System.Drawing.Color]::FromArgb(246, 247, 248)
$script:TitleLabel.SetBounds(22, 10, 570, 31)
$script:Header.Controls.Add($script:TitleLabel)
$script:SubtitleLabel = New-Object System.Windows.Forms.Label
$script:SubtitleLabel.Text = 'Copia segura da pasta Anki2  -  Git'
$script:SubtitleLabel.ForeColor = [System.Drawing.Color]::FromArgb(164, 168, 177)
$script:SubtitleLabel.SetBounds(24, 41, 570, 20)
$script:Header.Controls.Add($script:SubtitleLabel)

$script:StageLabel = New-Object System.Windows.Forms.Label
$script:StageLabel.Text = 'Preparando backup...'
$script:StageLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 11)
$script:StageLabel.ForeColor = [System.Drawing.Color]::FromArgb(239, 240, 242)
$script:StageLabel.SetBounds(24, 87, 680, 28)
$script:StageLabel.Anchor = 'Top,Left,Right'
$script:Form.Controls.Add($script:StageLabel)
$script:ProgressBar = New-Object System.Windows.Forms.ProgressBar
$script:ProgressBar.Style = 'Marquee'
$script:ProgressBar.MarqueeAnimationSpeed = 25
$script:ProgressBar.SetBounds(24, 124, 694, 12)
$script:ProgressBar.Anchor = 'Top,Left,Right'
$script:Form.Controls.Add($script:ProgressBar)
$script:StatsLabel = New-Object System.Windows.Forms.Label
$script:StatsLabel.Text = 'Aguardando...'
$script:StatsLabel.ForeColor = [System.Drawing.Color]::FromArgb(173, 178, 187)
$script:StatsLabel.SetBounds(24, 148, 690, 24)
$script:StatsLabel.Anchor = 'Top,Left,Right'
$script:Form.Controls.Add($script:StatsLabel)
$script:LogsLabel = New-Object System.Windows.Forms.Label
$script:LogsLabel.Text = 'REGISTRO DE ATIVIDADES'
$script:LogsLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold', 8)
$script:LogsLabel.ForeColor = [System.Drawing.Color]::FromArgb(145, 151, 163)
$script:LogsLabel.SetBounds(25, 190, 400, 20)
$script:Form.Controls.Add($script:LogsLabel)
$script:LogBox = New-Object System.Windows.Forms.RichTextBox
$script:LogBox.Multiline = $true
$script:LogBox.ReadOnly = $true
$script:LogBox.ScrollBars = 'ForcedVertical'
$script:LogBox.Font = New-Object System.Drawing.Font('Consolas', 9)
$script:LogBox.BackColor = [System.Drawing.Color]::FromArgb(24, 25, 29)
$script:LogBox.ForeColor = [System.Drawing.Color]::FromArgb(213, 217, 224)
$script:LogBox.BorderStyle = 'None'
$script:LogBox.DetectUrls = $false
$script:LogBox.SetBounds(24, 216, 694, 230)
$script:LogBox.Anchor = 'Top,Bottom,Left,Right'
$script:Form.Controls.Add($script:LogBox)
$script:FooterLabel = New-Object System.Windows.Forms.Label
$script:FooterLabel.Text = 'O Anki original nao sera modificado.'
$script:FooterLabel.ForeColor = [System.Drawing.Color]::FromArgb(140, 148, 160)
$script:FooterLabel.SetBounds(24, 462, 440, 24)
$script:FooterLabel.Anchor = 'Left,Bottom'
$script:Form.Controls.Add($script:FooterLabel)
$script:CloseButton = New-Object System.Windows.Forms.Button
$script:CloseButton.Text = 'Cancelar backup'
$script:CloseButton.FlatStyle = 'Flat'
$script:CloseButton.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(94, 128, 181)
$script:CloseButton.FlatAppearance.BorderSize = 1
$script:CloseButton.BackColor = [System.Drawing.Color]::FromArgb(51, 76, 116)
$script:CloseButton.ForeColor = [System.Drawing.Color]::White
$script:CloseButton.Cursor = [System.Windows.Forms.Cursors]::Hand
$script:CloseButton.SetBounds(557, 455, 161, 34)
$script:CloseButton.Anchor = 'Bottom,Right'
$script:CloseButton.Add_Click({
    if ($script:Finished) { $script:Form.Close() }
    else {
        $script:CancelRequested = $true
        $script:CloseButton.Enabled = $false
        $script:StageLabel.Text = 'Cancelando apos a operacao atual...'
    }
})
$script:Form.Controls.Add($script:CloseButton)
$script:Form.Add_FormClosing({
    param($sender, $eventArgs)
    if (-not $script:Finished) { $eventArgs.Cancel = $true }
})
if ($Theme -eq 'light') {
    $script:Form.BackColor = [System.Drawing.Color]::FromArgb(245, 246, 248)
    $script:Form.ForeColor = [System.Drawing.Color]::FromArgb(33, 35, 40)
    $script:Header.BackColor = [System.Drawing.Color]::FromArgb(231, 234, 238)
    $script:TitleLabel.ForeColor = [System.Drawing.Color]::FromArgb(32, 36, 44)
    $script:SubtitleLabel.ForeColor = [System.Drawing.Color]::FromArgb(92, 98, 108)
    $script:StageLabel.ForeColor = [System.Drawing.Color]::FromArgb(36, 40, 47)
    $script:StatsLabel.ForeColor = [System.Drawing.Color]::FromArgb(87, 94, 105)
    $script:LogsLabel.ForeColor = [System.Drawing.Color]::FromArgb(92, 98, 108)
    $script:LogBox.BackColor = [System.Drawing.Color]::White
    $script:LogBox.ForeColor = [System.Drawing.Color]::FromArgb(40, 44, 52)
    $script:FooterLabel.ForeColor = [System.Drawing.Color]::FromArgb(90, 98, 108)
    $script:CloseButton.BackColor = [System.Drawing.Color]::FromArgb(53, 104, 179)
    $script:CloseButton.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(53, 104, 179)
}
if (-not $ExternalGui) {
    $script:Form.Show()
    [System.Windows.Forms.Application]::DoEvents()
}

function Show-Log([string]$Message, [string]$Stage = '') {
    if ($Stage) { $script:StageLabel.Text = $Stage }
    if ($ExternalGui -and $Stage) { Write-Host "STAGE: $Stage" }
    $lineStart = $script:LogBox.TextLength
    $script:LogBox.AppendText("$(Get-Date -Format 'HH:mm:ss')  $Message`r`n")
    if ($Message.StartsWith('ERRO:') -or $Stage -eq 'Falha no backup') {
        $script:LogBox.Select($lineStart, $script:LogBox.TextLength - $lineStart)
        $script:LogBox.SelectionColor = [System.Drawing.Color]::FromArgb(255, 135, 135)
    } elseif ($Message.StartsWith('BACKUP CONCLUIDO')) {
        $script:LogBox.Select($lineStart, $script:LogBox.TextLength - $lineStart)
        $script:LogBox.SelectionColor = [System.Drawing.Color]::FromArgb(132, 220, 173)
    }
    $script:LogBox.SelectionStart = $script:LogBox.TextLength
    $script:LogBox.SelectionLength = 0
    $script:LogBox.ScrollToCaret()
    [System.Windows.Forms.Application]::DoEvents()
    Write-Host $Message
}

function Check-Cancel {
    if ($CancelSignal -and (Test-Path -LiteralPath $CancelSignal)) {
        $script:CancelRequested = $true
    }
    if ($script:CancelRequested) {
        throw $(if ($Language -eq 'pt-BR') { 'Backup cancelado pelo usuário.' }
                else { 'Backup cancelled by user.' })
    }
}

try {
    $logsDirectory = Join-Path $env:LOCALAPPDATA 'AnkiGitHubBackup\logs'
    New-Item -ItemType Directory -Path $logsDirectory -Force | Out-Null
    $logPath = Join-Path $logsDirectory ((Get-Date -Format 'yyyyMMdd_HHmmss') + '.log')
    Start-Transcript -LiteralPath $logPath -ErrorAction Stop | Out-Null
    $script:TranscriptStarted = $true
    Write-Host "Log: $logPath"
} catch {
    Write-Host "Aviso: nao foi possivel criar arquivo de log: $($_.Exception.Message)" -ForegroundColor Yellow
}

function Is-ProtectedDirectory([string]$Name) {
    $value = $Name.ToLowerInvariant()
    return ($value -eq '.git' -or $value.StartsWith('.git-') -or
            $value.StartsWith('git-backup') -or $value -eq '__pycache__')
}

function Run-Git([string[]]$Arguments) {
    Check-Cancel
    Show-Log ("git " + $Arguments[0]) ("Executando git " + $Arguments[0] + "...")
    $ErrorActionPreference = 'Continue'
    & git -C $Destination @Arguments 2>&1 | ForEach-Object { Show-Log ([string]$_) }
    if ($LASTEXITCODE -ne 0) {
        throw "Falha no Git ($($Arguments[0])), codigo $LASTEXITCODE."
    }
}

function Sync-Files([string]$CurrentSource, [string]$CurrentDestination) {
    Check-Cancel
    if (-not (Test-Path -LiteralPath $CurrentDestination -PathType Container)) {
        New-Item -ItemType Directory -Path $CurrentDestination -Force | Out-Null
    }

    foreach ($file in @(Get-ChildItem -LiteralPath $CurrentSource -File -Force)) {
        Check-Cancel
        if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
        if ($MaxFileMiB -gt 0 -and $file.Length -ge ([long]$MaxFileMiB * 1MB)) {
            $script:LargeFiles.Add("$($file.FullName) - $([Math]::Round($file.Length / 1MB, 1)) MiB")
        }
        $output = Join-Path $CurrentDestination $file.Name
        $mustCopy = $true
        if (Test-Path -LiteralPath $output -PathType Leaf) {
            $existing = Get-Item -LiteralPath $output -Force
            if ($file.Length -eq $existing.Length -and
                $file.LastWriteTimeUtc.Ticks -eq $existing.LastWriteTimeUtc.Ticks) {
                $mustCopy = $false
            }
        }
        if ($mustCopy) {
            if ($file.Name -match '^collection\.anki(2|21b)$') {
                # The Anki process must have released all collection handles.
                try {
                    $handle = [IO.File]::Open($file.FullName, 'Open', 'Read', 'None')
                    $handle.Dispose()
                } catch {
                    throw "Banco ainda bloqueado: $($file.FullName). $($_.Exception.Message)"
                }
            }
            Copy-Item -LiteralPath $file.FullName -Destination $output -Force
            $script:Copied++
            $script:TotalBytes += $file.Length
            if ($script:Copied % 100 -eq 0) {
                $script:StatsLabel.Text = "Atualizados: $script:Copied | Inalterados: $script:Unchanged | Copiados: $([Math]::Round($script:TotalBytes / 1MB, 1)) MiB"
                Show-Log "Arquivos copiados: $script:Copied | Atual: $($file.Name)"
            }
        } else {
            $script:Unchanged++
        }
    }

    foreach ($directory in @(Get-ChildItem -LiteralPath $CurrentSource -Directory -Force)) {
        if ((Is-ProtectedDirectory $directory.Name) -or
            ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
        Sync-Files $directory.FullName (Join-Path $CurrentDestination $directory.Name)
    }

    # Remove stale backup files, but never delete Git metadata or whole folders.
    foreach ($oldFile in @(Get-ChildItem -LiteralPath $CurrentDestination -File -Force)) {
        if ($oldFile.Name -eq '.gitignore') { continue }
        if (-not (Test-Path -LiteralPath (Join-Path $CurrentSource $oldFile.Name) -PathType Leaf)) {
            Remove-Item -LiteralPath $oldFile.FullName -Force
            $script:Removed++
        }
    }
    foreach ($oldDirectory in @(Get-ChildItem -LiteralPath $CurrentDestination -Directory -Force)) {
        if (Is-ProtectedDirectory $oldDirectory.Name) { continue }
        $corresponding = Join-Path $CurrentSource $oldDirectory.Name
        if (Test-Path -LiteralPath $corresponding -PathType Container) {
            # This branch is visited by recursion above, where its stale files
            # were already handled.
            continue
        }
        # Delete only ordinary files in stale folders; preserve directories
        # and any renamed Git metadata a user may have kept inside them.
        Remove-StaleFiles $oldDirectory.FullName
    }
}

function Remove-StaleFiles([string]$Directory) {
    foreach ($file in @(Get-ChildItem -LiteralPath $Directory -File -Force)) {
        Remove-Item -LiteralPath $file.FullName -Force
        $script:Removed++
    }
    foreach ($child in @(Get-ChildItem -LiteralPath $Directory -Directory -Force)) {
        if (-not (Is-ProtectedDirectory $child.Name)) {
            Remove-StaleFiles $child.FullName
        }
    }
}

try {
    Acquire-OperationLock
    Show-Log 'Aguardando o Anki encerrar...' 'Aguardando fechamento do Anki...'
    $limit = (Get-Date).AddMinutes(3)
    while (Get-Process -Id $AnkiProcessId -ErrorAction SilentlyContinue) {
        if ((Get-Date) -gt $limit) { throw 'O Anki nao fechou dentro de 3 minutos. Backup cancelado.' }
        Check-Cancel
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 500
    }

    $Source = [IO.Path]::GetFullPath($Source).TrimEnd([char]92)
    $Destination = [IO.Path]::GetFullPath($Destination).TrimEnd([char]92)
    if ($Destination.StartsWith($Source + [char]92, [StringComparison]::OrdinalIgnoreCase) -or
        $Source.StartsWith($Destination + [char]92, [StringComparison]::OrdinalIgnoreCase) -or
        $Source -eq $Destination) {
        throw 'A pasta de backup precisa ficar fora da Anki2.'
    }
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Pasta de origem inexistente: $Source"
    }
    if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
        New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    }

    # Verify the remote before touching files in the destination.
    & git --version
    if ($LASTEXITCODE -ne 0) { throw 'Git nao instalado.' }
    if (-not (Test-Path -LiteralPath (Join-Path $Destination '.git'))) {
        # Check ancestors directly; probing rev-parse on a non-repository
        # emits stderr that PowerShell can treat as a terminating error.
        $ancestor = Split-Path -Parent $Destination
        while ($ancestor) {
            if (Test-Path -LiteralPath (Join-Path $ancestor '.git')) {
                throw 'Destino dentro de outro repositorio Git.'
            }
            $parent = Split-Path -Parent $ancestor
            if (-not $parent -or $parent -eq $ancestor) { break }
            $ancestor = $parent
        }
        Run-Git -Arguments @('init')
    }
    $remoteNames = @(& git -C $Destination remote)
    if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel listar os remotos Git.' }
    if ($remoteNames -notcontains 'origin') {
        & git -C $Destination remote add origin $Remote
        if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel configurar origin.' }
    } else {
        $existingRemote = (& git -C $Destination remote get-url origin | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel verificar o remoto origin.' }
        if ($existingRemote -ne $Remote) {
            if (-not $AllowRemoteUpdate) {
                throw 'origin possui outra URL. Confirme a alteracao pelo botao Push to Git.'
            }
            & git -C $Destination remote set-url origin $Remote
            if ($LASTEXITCODE -ne 0) { throw 'Falha ao atualizar a URL do remoto origin.' }
            Show-Log 'URL do remoto origin atualizada conforme confirmacao.'
        }
    }

    Show-Log 'Copiando a Anki2 (incremental)...' 'Copiando dados do Anki...'
    Sync-Files $Source $Destination
    Show-Log "Arquivos atualizados: $script:Copied | Inalterados: $script:Unchanged | Removidos: $script:Removed"
    Show-Log ("Dados copiados: {0:N1} MiB" -f ($script:TotalBytes / 1MB))

    # Renamed .git folders must never become Git objects themselves. This
    # prevents accidental nesting in a *new* repository; it cannot rewrite
    # an existing commit that already tracked these files.
    $ignorePath = Join-Path $Destination '.gitignore'
    $ignoreRules = @('/.git-backup/', '**/.git-backup/', '**/.git-*/',
                     '/git-backup*/', '**/git-backup*/', '**/anki-sqlite-*.tmp')
    $ignoreExisting = @()
    if (Test-Path -LiteralPath $ignorePath -PathType Leaf) {
        $ignoreExisting = @(Get-Content -LiteralPath $ignorePath)
    }
    foreach ($rule in $ignoreRules) {
        if ($ignoreExisting -notcontains $rule) {
            Add-Content -LiteralPath $ignorePath -Value $rule -Encoding UTF8
        }
    }

    if ($script:LargeFiles.Count -gt 0) {
        Show-Log "Arquivos acima do limite configurado ($MaxFileMiB MiB):" 'Verificando tamanho dos arquivos...'
        foreach ($item in $script:LargeFiles) { Show-Log $item }
        throw 'Arquivos acima do limite configurado. Ajuste o limite ou configure Git LFS antes de enviar.'
    }

    Run-Git -Arguments @('add','-A')
    & git -C $Destination diff --cached --quiet
    if ($LASTEXITCODE -eq 1) {
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        Run-Git -Arguments @('commit','-m',"Anki backup $stamp")
    } elseif ($LASTEXITCODE -eq 0) {
        Show-Log 'Nenhuma alteracao para novo commit.'
    } else {
        throw 'Falha ao verificar alteracoes Git.'
    }
    Run-Git -Arguments @('push','-u','origin',"HEAD:refs/heads/$Branch")
    $script:Succeeded = $true
    Show-Log 'BACKUP CONCLUIDO E ENVIADO!' 'Backup concluido com sucesso'
} catch {
    Show-Log ("ERRO: " + $_.Exception.Message) 'Falha no backup'
    Show-Log 'Verifique o log para confirmar ate qual etapa o backup chegou.'
} finally {
    if ($script:TranscriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
    }
    if ($ReopenPath -and (Test-Path -LiteralPath $ReopenPath -PathType Leaf) -and
        -not (Get-Process -Id $AnkiProcessId -ErrorAction SilentlyContinue)) {
        try {
            Show-Log 'Reabrindo o Anki...'
            $restarted = Start-Process -FilePath $ReopenPath -PassThru
            if (-not $restarted) { throw 'Windows nao confirmou o inicio do Anki.' }
            Show-Log 'ANKI_REOPENED'
        } catch {
            Show-Log "Nao foi possivel reabrir o Anki: $($_.Exception.Message)"
        }
    }
    $script:Finished = $true
    if ($script:OperationLock) { $script:OperationLock.Dispose(); $script:OperationLock = $null }
    $script:ProgressBar.Style = 'Blocks'
    $script:ProgressBar.Value = $(if ($script:Succeeded) { 100 } else { 0 })
    if ($script:Succeeded) {
        $script:FooterLabel.Text = 'Backup concluido. Seus dados originais permanecem intactos.'
    } else {
        $script:FooterLabel.Text = 'Backup nao concluido. Confira o registro de atividades.'
    }
    $script:CloseButton.Text = 'Fechar'
    $script:CloseButton.Enabled = $true
    [System.Windows.Forms.Application]::DoEvents()
    while (-not $ExternalGui -and $script:Form.Visible) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 100
    }
}
