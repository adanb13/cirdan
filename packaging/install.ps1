#!/usr/bin/env pwsh
# Cirdan installer for Windows — downloads the standalone binary from GitHub
# Releases and drops it in a local bin dir.
#
#   irm https://raw.githubusercontent.com/adanb13/cirdan/main/packaging/install.ps1 | iex
#
# Env overrides:
#   CIRDAN_VERSION   release to install (e.g. 0.7.0); default: latest
#   CIRDAN_BIN_DIR   install dir; default: $HOME\.cirdan\bin
$ErrorActionPreference = "Stop"

$Repo = "adanb13/cirdan"
$BinDir = if ($env:CIRDAN_BIN_DIR) { $env:CIRDAN_BIN_DIR } else { Join-Path $HOME ".cirdan\bin" }
$Version = if ($env:CIRDAN_VERSION) { $env:CIRDAN_VERSION } else { "latest" }

if ($env:PROCESSOR_ARCHITECTURE -ne "AMD64") {
  Write-Error "cirdan: unsupported arch '$($env:PROCESSOR_ARCHITECTURE)' — install via Python: pip install cirdanops"
}

$target = "win32-x64"
$url = if ($Version -eq "latest") {
  "https://github.com/$Repo/releases/latest/download/cirdan-$target.exe"
} else {
  "https://github.com/$Repo/releases/download/v$Version/cirdan-$target.exe"
}

New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$dest = Join-Path $BinDir "cirdan.exe"

Write-Host "cirdan: downloading $target ($Version) from $url"
Invoke-WebRequest -Uri $url -OutFile $dest

Write-Host "cirdan: installed -> $dest"
& $dest --version
Write-Host "cirdan: add $BinDir to your PATH if it isn't already, then run 'cirdan serve-mcp'."
