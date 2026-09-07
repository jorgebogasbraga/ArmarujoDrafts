# Arma league. Everything this bot reads and writes lives in leagues/arma.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$env:LEAGUE_DIR = "leagues/arma"
& .venv\Scripts\python.exe main.py
