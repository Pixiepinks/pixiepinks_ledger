# Reviewed local Windows build only. Does not deploy, pair, print or open a drawer.
$ErrorActionPreference = 'Stop'
if (Test-Path .venv-print-agent) { throw 'Use a clean build workspace; do not overwrite an existing agent environment.' }
py -3.12 -m venv .venv-print-agent
if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
& .\.venv-print-agent\Scripts\python.exe -m pip install -r windows_print_agent\requirements.txt pyinstaller==6.16.0
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& .\.venv-print-agent\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --name PixiePinksPrintAgent --collect-all pypdfium2 --collect-all pypdfium2_raw --hidden-import win32timezone --paths . windows_print_agent\entrypoint.py
if ($LASTEXITCODE -ne 0) { throw 'Windows executable build failed; no package is available.' }
Write-Output 'Unsigned build in dist\PixiePinksPrintAgent. Inspect/test/sign before distribution; no installer/signature is claimed.'
