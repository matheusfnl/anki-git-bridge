param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Remote,
    [Parameter(Mandatory=$true)][string]$Branch,
    [Parameter(Mandatory=$true)][int]$AnkiProcessId,
    [Parameter(Mandatory=$true)][string]$CurrentAddonPath,
    [string]$ReopenPath = '',
    [string]$CancelSignal = '',
    [ValidateSet('en','pt-BR')][string]$Language = 'en',
    [ValidateSet('dark','light')][string]$Theme = 'dark',
    [switch]$ExternalGui
)

$ErrorActionPreference = 'Stop'
$movedOriginal = $false
$restored = $false
$critical = $false
$script:OperationLock = $null
$Source = [IO.Path]::GetFullPath($Source).TrimEnd([char]92)
$parent = Split-Path -Parent $Source
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$nonce = [Guid]::NewGuid().ToString('N').Substring(0, 8)
$staging = Join-Path $parent ("Anki2-gitbridge-staging-$timestamp-$nonce")
$originalBackup = Join-Path $parent ("Anki2-antes-da-restauracao-$timestamp-$nonce")
$journal = Join-Path $parent '.gitbridge-restore-state.json'

function Check-Cancel {
    if (-not $critical -and $CancelSignal -and (Test-Path -LiteralPath $CancelSignal)) {
        throw $(if ($Language -eq 'pt-BR') { 'Restauração cancelada. A Anki2 original não foi alterada.' }
                else { 'Restore cancelled. The original Anki2 was not changed.' })
    }
}

function Save-Journal([string]$phase) {
    $record = [ordered]@{
        source = $Source; backup = $originalBackup; staging = $staging
        phase = $phase; updatedAt = (Get-Date).ToString('o')
    }
    $tempJournal = "$journal.tmp"
    $record | ConvertTo-Json -Compress | Set-Content -LiteralPath $tempJournal -Encoding UTF8
    Move-Item -LiteralPath $tempJournal -Destination $journal -Force
}

function Check-InterruptedRestore {
    if (-not (Test-Path -LiteralPath $journal -PathType Leaf)) { return }
    $record = Get-Content -LiteralPath $journal -Raw | ConvertFrom-Json
    if ($record.source -ne $Source) { throw "Registro de recuperação pertence a outra Anki2: $($record.source)" }
    if ((Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Foi detectada uma restauração anterior interrompida ($($record.phase)). A Anki2 existe; por segurança, confira seu conteúdo e a cópia $($record.backup) antes de continuar. Registro: $journal"
    }
    if (-not (Test-Path -LiteralPath $record.backup -PathType Container)) {
        throw "Anki2 ausente após interrupção; cópia de segurança não localizada automaticamente. Confira: $journal"
    }
    Report "Recuperando a Anki2 a partir da cópia preservada: $($record.backup)" 'Recuperando restauração interrompida...'
    Copy-Item -LiteralPath $record.backup -Destination $Source -Recurse -Force -ErrorAction Stop
    Report "Anki2 original recuperada; cópia antiga preservada: $($record.backup)"
    Remove-Item -LiteralPath $journal -Force
    throw 'Restauração anterior recuperada. Inicie outra restauração somente depois de conferir o Anki.'
}

function Report([string]$message, [string]$stage = '') {
    if ($stage) { Write-Host "STAGE: $stage" }
    Write-Host $message
}

function Preserve-CurrentGitBridge {
    Check-Cancel
    $current = [IO.Path]::GetFullPath($CurrentAddonPath).TrimEnd([char]92)
    $sourcePrefix = $Source + [char]92
    if (-not $current.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'GitBridge atual está fora da pasta Anki2 configurada; restauração cancelada.'
    }
    $relative = $current.Substring($sourcePrefix.Length)
    if (-not $relative.StartsWith('addons21' + [char]92, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Pasta da extensão GitBridge inesperada; restauração cancelada.'
    }
    $manifestPath = Join-Path $current 'manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw 'Manifesto do GitBridge atual não encontrado; restauração cancelada.'
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.package -ne 'anki_github_backup') {
        throw 'A extensão atual não foi identificada como GitBridge; restauração cancelada.'
    }
    $target = Join-Path $staging $relative
    Report $(if ($Language -eq 'pt-BR') {
        "Preservando GitBridge instalado ($($manifest.human_version))..."
    } else {
        "Preserving installed GitBridge ($($manifest.human_version))..."
    }) 'Preservando a versão atual do GitBridge...'
    # Only the downloaded staging tree is modified. The live add-on remains untouched.
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
    }
    $targetParent = Split-Path -Parent $target
    New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
    Copy-Item -LiteralPath $current -Destination $target -Recurse -Force -ErrorAction Stop
    $copiedManifestPath = Join-Path $target 'manifest.json'
    if (-not (Test-Path -LiteralPath $copiedManifestPath -PathType Leaf)) {
        throw 'Cópia do GitBridge falhou; a Anki2 original não foi substituída.'
    }
    $copiedManifest = Get-Content -LiteralPath $copiedManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($copiedManifest.package -ne $manifest.package -or
        $copiedManifest.human_version -ne $manifest.human_version) {
        throw 'Versão do GitBridge preservada não corresponde à instalada; restauração cancelada.'
    }
    $currentGui = Join-Path $current 'backup_gui\backup_gui.exe'
    if ((Test-Path -LiteralPath $currentGui -PathType Leaf) -and
        -not (Test-Path -LiteralPath (Join-Path $target 'backup_gui\backup_gui.exe') -PathType Leaf)) {
        throw 'Executável do GitBridge não foi preservado; restauração cancelada.'
    }
    Report $(if ($Language -eq 'pt-BR') {
        "GitBridge $($manifest.human_version) preservado na restauração."
    } else {
        "GitBridge $($manifest.human_version) preserved in restored backup."
    })
    Check-Cancel
}

try {
    $lockFolder = Join-Path $env:LOCALAPPDATA 'GitBridge'
    New-Item -ItemType Directory -Path $lockFolder -Force | Out-Null
    try {
        $script:OperationLock = [IO.File]::Open((Join-Path $lockFolder 'operation.lock'),
            'OpenOrCreate', 'ReadWrite', 'None')
    } catch {
        throw $(if ($Language -eq 'pt-BR') { 'Outra operação do GitBridge já está em andamento.' }
                else { 'Another GitBridge operation is already running.' })
    }
    Check-InterruptedRestore
    Check-Cancel
    Report 'Preparando restauracao completa da Anki2...' 'Baixando backup do Git...'
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "A pasta Anki2 original nao existe: $Source"
    }
    if ((Split-Path -Leaf $Source).ToLowerInvariant() -ne 'anki2') {
        throw 'A origem nao e uma pasta chamada Anki2.'
    }
    if (Test-Path -LiteralPath $staging) { throw 'Destino temporario ja existe.' }
    if (Test-Path -LiteralPath $originalBackup) { throw 'Destino da copia antiga ja existe.' }

    & git --version
    if ($LASTEXITCODE -ne 0) { throw 'Git nao encontrado.' }
    Report 'Clonando repositorio em uma pasta temporaria...'
    $ErrorActionPreference = 'Continue'
    & git clone --single-branch -b $Branch -- $Remote $staging 2>&1 | ForEach-Object { Write-Host ([string]$_) }
    $cloneCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($cloneCode -ne 0) { throw "git clone falhou (codigo $cloneCode). A Anki2 original nao foi alterada." }
    Check-Cancel

    Report 'Validando os arquivos restaurados...' 'Validando backup...'
    if (-not (Test-Path -LiteralPath (Join-Path $staging 'prefs21.db') -PathType Leaf)) {
        throw 'Backup incompleto: prefs21.db nao encontrado. A Anki2 original nao foi alterada.'
    }
    $collections = @(Get-ChildItem -LiteralPath $staging -File -Recurse -Filter 'collection.anki*' -Force |
        Where-Object { $_.Name -match '^collection\.anki(2|21|21b)$' })
    if ($collections.Count -eq 0) {
        throw 'Backup incompleto: banco collection.anki2/collection.anki21 nao encontrado.'
    }
    foreach ($collection in $collections) {
        Check-Cancel
        $stream = [IO.File]::OpenRead($collection.FullName)
        try {
            if ($stream.Length -lt 16) { throw "Banco incompleto: $($collection.FullName)" }
            $headerBytes = New-Object byte[] 16
            $read = $stream.Read($headerBytes, 0, 16)
            $header = [Text.Encoding]::ASCII.GetString($headerBytes)
            if ($read -ne 16 -or $header -ne "SQLite format 3`0") {
                throw "Banco nao e SQLite valido (ou e um ponteiro Git LFS): $($collection.FullName)"
            }
        } finally { $stream.Dispose() }
    }
    # A clone without Git LFS may contain small pointer files instead of the
    # original images/audio. Reject those before touching the installed Anki2.
    foreach ($candidate in @(Get-ChildItem -LiteralPath $staging -Recurse -File -Force |
            Where-Object { $_.Length -ge 40 -and $_.Length -le 512 })) {
        Check-Cancel
        if ($candidate.FullName.StartsWith((Join-Path $staging '.git') + [char]92,
                [StringComparison]::OrdinalIgnoreCase)) { continue }
        $reader = [IO.File]::OpenRead($candidate.FullName)
        try {
            $buffer = New-Object byte[] 48
            $count = $reader.Read($buffer, 0, 48)
            if ([Text.Encoding]::ASCII.GetString($buffer, 0, $count).StartsWith(
                    'version https://git-lfs.github.com/spec/v1')) {
                throw "Arquivo Git LFS nao baixado: $($candidate.FullName). Instale/configure Git LFS."
            }
        } finally { $reader.Dispose() }
    }

    # Do not install Git's repository metadata inside the live Anki2.
    $gitMetadata = Join-Path $staging '.git'
    if (Test-Path -LiteralPath $gitMetadata) {
        Remove-Item -LiteralPath $gitMetadata -Recurse -Force
    }
    Preserve-CurrentGitBridge
    Report 'Backup validado. Aguardando o Anki encerrar...' 'Aguardando fechamento do Anki...'
    $limit = (Get-Date).AddMinutes(4)
    while (Get-Process -Id $AnkiProcessId -ErrorAction SilentlyContinue) {
        Check-Cancel
        if ((Get-Date) -gt $limit) { throw 'O Anki nao fechou em 4 minutos. Nada foi substituido.' }
        Start-Sleep -Milliseconds 500
    }

    Check-Cancel
    Save-Journal 'before_move'
    $critical = $true
    Write-Host 'CRITICAL_BEGIN'
    Report $(if ($Language -eq 'pt-BR') {
        'ETAPA CRÍTICA: NÃO FECHE A JANELA. O Anki2 poderá ficar ausente ou incompleto se a operação for interrompida.'
    } else {
        'CRITICAL STEP: DO NOT CLOSE THIS WINDOW. Interrupting may leave Anki2 missing or incomplete.'
    })
    Report "Guardando a Anki2 atual em: $originalBackup" 'Preservando instalacao atual...'
    Move-Item -LiteralPath $Source -Destination $originalBackup -ErrorAction Stop
    $movedOriginal = $true
    try {
        Save-Journal 'original_preserved'
        Report 'Colocando a Anki2 restaurada no lugar...' 'Restaurando Anki2...'
        Move-Item -LiteralPath $staging -Destination $Source -ErrorAction Stop
        $restored = $true
        Save-Journal 'restored'
    } catch {
        Report 'Falha na substituicao; tentando COPIAR a Anki2 anterior de volta...'
        if (-not (Test-Path -LiteralPath $Source)) {
            Copy-Item -LiteralPath $originalBackup -Destination $Source -Recurse -Force -ErrorAction Stop
            Report "Copia de seguranca PRESERVADA em: $originalBackup"
        }
        throw
    }

    Remove-Item -LiteralPath $journal -Force
    Report 'RESTORE_COMPLETED'
    Report "COPIA_ANTIGA: $originalBackup"
    Report 'Caso queira apagar o backup antigo, acesse a pasta e apague manualmente.'
    if ($ReopenPath -and (Test-Path -LiteralPath $ReopenPath -PathType Leaf)) {
        try {
            Report 'Iniciando o Anki restaurado...'
            $started = Start-Process -FilePath $ReopenPath -PassThru
            if ($started) { Report 'ANKI_REOPENED' }
        } catch {
            Report "Nao foi possivel reabrir o Anki: $($_.Exception.Message)"
        }
    }
} catch {
    $restoreFailure = $_.Exception.Message
    if ($movedOriginal -and -not $restored -and
        -not (Test-Path -LiteralPath $Source -PathType Container) -and
        (Test-Path -LiteralPath $originalBackup -PathType Container)) {
        try {
            Report 'Tentando recuperar automaticamente a Anki2 original sem apagar a cópia de segurança...'
            Copy-Item -LiteralPath $originalBackup -Destination $Source -Recurse -Force -ErrorAction Stop
            Report "Anki2 original recuperada. Cópia preservada em: $originalBackup"
        } catch {
            Report "RECUPERAÇÃO MANUAL NECESSÁRIA: $($_.Exception.Message)"
        }
    }
    Report "ERRO: $restoreFailure" 'Falha na restauracao'
    if ($movedOriginal -and -not $restored) {
        Report "ATENCAO: a copia de seguranca anterior continua em: $originalBackup"
    } else {
        Report 'A Anki2 anterior nao foi substituida (ou foi recuperada).'
    }
    Report "Arquivos baixados, se existentes: $staging"
    if ($movedOriginal -and -not $restored -and
        (Test-Path -LiteralPath $Source -PathType Container)) {
        # Keep the preserved backup and clear the recovery journal only when
        # the source was successfully repopulated.
        Remove-Item -LiteralPath $journal -Force -ErrorAction SilentlyContinue
    } elseif (-not $movedOriginal -and (Test-Path -LiteralPath $Source -PathType Container) -and
             (Test-Path -LiteralPath $journal)) {
        Remove-Item -LiteralPath $journal -Force -ErrorAction SilentlyContinue
    }
    if ((Test-Path -LiteralPath $Source -PathType Container) -and
        -not (Get-Process -Id $AnkiProcessId -ErrorAction SilentlyContinue) -and
        $ReopenPath -and (Test-Path -LiteralPath $ReopenPath -PathType Leaf)) {
        try { Start-Process -FilePath $ReopenPath | Out-Null; Report 'ANKI_REOPENED' } catch { }
    }
    exit 1
} finally {
    if ($script:OperationLock) { $script:OperationLock.Dispose() }
}
