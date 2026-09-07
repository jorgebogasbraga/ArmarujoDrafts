# Tuga Champions League. Everything this bot reads and writes lives in
# leagues/tuga-champions — separate state, separate log, separate config.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$env:LEAGUE_DIR = "leagues/tuga-champions"
& .venv\Scripts\python.exe main.py
