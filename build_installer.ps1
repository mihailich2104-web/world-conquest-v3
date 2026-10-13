# Полная сборка установщика: зависимости -> данные -> тесты -> PyInstaller (onedir) -> Inno Setup
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
Set-Location $PSScriptRoot

python -m pip install --upgrade pip
python -m pip uninstall -y pygame 2>$null
python -m pip install -r requirements.txt pillow

if (-not (Test-Path src\data\provinces.geojson)) { python tools\prepare_map.py }
if (-not (Test-Path src\data\countries.json))    { python tools\gen_countries.py }
if (-not (Test-Path src\data\regions.json))      { python tools\make_regions.py }
python tools\build_scenarios.py
python tests\test_core.py
python tests\test_map_geo.py

$env:WC_ONEDIR = "1"
python -m PyInstaller build\world_conquest.spec --clean --noconfirm
if (-not (Test-Path dist\WorldConquest\WorldConquest.exe)) { throw "exe не собран" }

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    foreach ($p in "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") {
        if (Test-Path $p) { $iscc = $p; break }
    }
}
if (-not $iscc) {
    winget install -e --id JRSoftware.InnoSetup --silent --accept-package-agreements --accept-source-agreements
    foreach ($p in "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") {
        if (Test-Path $p) { $iscc = $p; break }
    }
}
if (-not $iscc) { throw "Inno Setup не найден: https://jrsoftware.org/isdl.php" }
& $iscc installer.iss
Write-Host "Готово: installer\WorldConquest-Setup.exe"
