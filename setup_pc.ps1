# One-time setup of Grindstone on a new Windows PC.
#
# 1. Install Git + GitHub CLI and sign in:
#      winget install -e --id Git.Git ;  winget install -e --id GitHub.cli ;  gh auth login
# 2. Get the project:   git clone <this repository's URL> $env:USERPROFILE\Grindstone
# 3. Install the 2009scape launcher and start Singleplayer once (creates %USERPROFILE%\2009scape),
#    then close it. After this the launcher isn't needed - the Grindstone icon starts everything.
# 4. Run this:          powershell -ExecutionPolicy Bypass -File $env:USERPROFILE\Grindstone\setup_pc.ps1

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# Python 3.12 and a JDK (the JDK builds the in-game input add-on)
if (-not (Test-Path "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")) {
    Write-Host "Installing Python 3.12..."
    winget install -e --id Python.Python.3.12 --source winget
}
if (-not (Get-ChildItem 'C:\Program Files\Eclipse Adoptium' -Filter 'jdk-*' -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Temurin JDK 11..."
    winget install -e --id EclipseAdoptium.Temurin.11.JDK --source winget
}

# git reuses the GitHub CLI's sign-in, so it never asks for a password again
if (Get-Command gh -ErrorAction SilentlyContinue) {
    gh auth status 2>$null
    if ($LASTEXITCODE -eq 0) { gh auth setup-git }
}

# The bot's Python environment (lives outside the repo)
$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$venv = Join-Path $env:USERPROFILE '.venvs\lumberjack'
if (-not (Test-Path "$venv\Scripts\python.exe")) { & $py -m venv $venv }
& "$venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
& "$venv\Scripts\python.exe" -m pip install --quiet opencv-python numpy mss pywin32 pyyaml fastapi "uvicorn[standard]" websockets
Write-Host "Python packages installed."

# Desktop icon: double-click to start the server, the game and the control panel
$desktop = [Environment]::GetFolderPath('Desktop')
$old = Join-Path $desktop 'Lumberjack.lnk'                 # the icon's name before the rename
if (Test-Path $old) { Remove-Item $old }
$lnk = Join-Path $desktop 'Grindstone.lnk'
$sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$sc.TargetPath = Join-Path $here 'lumberjack.bat'
$sc.WorkingDirectory = $here
$sc.IconLocation = Join-Path $here 'lumberjack\assets\grindstone.ico'
$sc.Save()
Write-Host "Desktop icon 'Grindstone' created."

$game = if ($env:LUMBERJACK_GAME_DIR) { $env:LUMBERJACK_GAME_DIR } else { Join-Path $env:USERPROFILE '2009scape' }
if (-not (Test-Path (Join-Path $game 'singleplayer\game\data'))) {
    Write-Host "2009scape singleplayer isn't installed yet - install it and start Singleplayer once."
}

Write-Host ""
Write-Host "All set. To play, double-click 'Grindstone' on the desktop (or run: lumberjack)."
Write-Host "It starts the server, the game and the control panel. In game use SD mode + Fixed screen."
Write-Host "Closing the game window stops the server and the panel."
