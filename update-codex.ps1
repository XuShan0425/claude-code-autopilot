#requires -Version 5.1
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# The official installer calls Get-FileHash. Windows PowerShell can fail to
# autoload Microsoft.PowerShell.Utility in customized or damaged sessions, so
# load it explicitly before evaluating the installer.
if (-not (Get-Command -Name Get-FileHash -ErrorAction SilentlyContinue)) {
    Import-Module Microsoft.PowerShell.Utility -Force -ErrorAction Stop
}

if (-not (Get-Command -Name Get-FileHash -ErrorAction SilentlyContinue)) {
    throw 'Get-FileHash is unavailable. Repair Windows PowerShell or install PowerShell 7, then retry.'
}

$env:CODEX_NON_INTERACTIVE = '1'
$installerUrl = 'https://chatgpt.com/codex/install.ps1'
$installer = Invoke-RestMethod -Uri $installerUrl -Method Get

# The URL is the official Codex installer; execute only the downloaded script.
Invoke-Expression ([string]$installer)
