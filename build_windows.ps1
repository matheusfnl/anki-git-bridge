# Run on Windows with Python 3.12 installed. Produces a self-contained
# .ankiaddon: end users never need Python or PyQt6.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Python Launcher (py.exe) nao encontrado. Instale o Python 3.12 para Windows.'
}
& py -3.12 --version
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.12 nao encontrado. Instale Python 3.12 e tente novamente. Python 3.15 nao e suportado por esta compilacao.'
}

$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & py -3.12 -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao criar ambiente de compilacao Python 3.12.' }
}
& $venvPython -m pip install --disable-pip-version-check --upgrade 'PyInstaller==6.11.1' 'PyQt6==6.7.1'
if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar PyInstaller e PyQt6. Nao foi gerado nenhum addon.' }

& $venvPython -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name backup_gui --distpath (Join-Path $projectRoot 'dist') `
    --workpath (Join-Path $projectRoot 'build') `
    (Join-Path $projectRoot 'backup_qt.py')
if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar a interface Qt. Nao foi gerado nenhum addon.' }
$guiBinary = Join-Path $projectRoot 'dist\backup_gui\backup_gui.exe'
if (-not (Test-Path -LiteralPath $guiBinary -PathType Leaf)) {
    throw 'PyInstaller terminou sem gerar backup_gui.exe.'
}

$releaseDirectory = Join-Path $projectRoot 'release-onedir'
New-Item -ItemType Directory -Path $releaseDirectory -Force | Out-Null
$items = @('__init__.py', 'backup_worker.ps1', 'restore_worker.ps1', 'backup_qt.py', 'manifest.json', 'config.json', 'README.md')
foreach ($item in $items) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $item) -Destination $releaseDirectory -Force
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'dist\backup_gui') -Destination $releaseDirectory -Recurse -Force

$zipPath = Join-Path $projectRoot 'AnkiGitHubBackup.zip'
$addonPath = Join-Path $projectRoot 'GitBridge.ankiaddon'
if (Test-Path $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
if (Test-Path $addonPath) { Remove-Item -LiteralPath $addonPath -Force }
Compress-Archive -Path (Join-Path $releaseDirectory '*') -DestinationPath $zipPath
Move-Item -LiteralPath $zipPath -Destination $addonPath
Write-Host "Pacote Windows pronto: $addonPath"
$digest = (Get-FileHash -LiteralPath $addonPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath (Join-Path $projectRoot 'GitBridge.ankiaddon.sha256') `
    -Value "$digest  GitBridge.ankiaddon" -Encoding Ascii
Write-Host "SHA-256: $digest"
