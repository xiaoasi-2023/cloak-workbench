$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:PIP_CACHE_DIR = Join-Path $projectRoot '.local\pip-cache'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required.' }
}
& $pythonExe -m pip install -r worker\requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependencies failed to install.' }
$sdkExe = Join-Path $projectRoot '.tools\dotnet\dotnet.exe'
if (-not (Test-Path -LiteralPath $sdkExe)) {
    New-Item -ItemType Directory -Path '.local' -Force | Out-Null
    $installer = Join-Path $projectRoot '.local\dotnet-install.ps1'
    Invoke-WebRequest -Uri 'https://dot.net/v1/dotnet-install.ps1' -OutFile $installer
    & $installer -Channel 8.0 -InstallDir (Join-Path $projectRoot '.tools\dotnet') -NoPath
    if ($LASTEXITCODE -ne 0) { throw '.NET SDK setup failed.' }
}
& $sdkExe build desktop\CloakWorkbench.csproj -c Release --nologo
if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed.' }
Write-Output 'Setup complete. Double-click start-workbench.bat.'
