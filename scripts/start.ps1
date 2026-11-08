<#
.SYNOPSIS
anonchains one-shot launcher for Windows PowerShell (also works with pwsh on Linux/macOS).
.DESCRIPTION
Checks the interpreter, creates/uses .venv, verifies and installs dependencies,
then starts the CLI, runs the offline tests, or only reports the environment.

Output is deliberately ASCII so Windows PowerShell 5.1 reads the file correctly.
Arguments are parsed manually from $args (no param block) so CLI options such as
--config keep their original order when forwarded to "python -m anonchains".

Modes (leading words, also accepted with - or --):
  check   | -CheckOnly    report environment only (exit 1 if incomplete)
  install | -InstallOnly  create .venv, install dependencies, then exit
  test    | -Test         run the offline pytest suite
  (none)                 start the CLI (default: interactive numeric menu)
Switches (must appear before CLI arguments):
  -Enhanced / --enhanced  also install requirements-optional.txt
  -NoVenv  / --no-venv    use the current/system Python instead of .venv
  -h / --help             show this help
  --                      stop parsing; the rest is forwarded to the CLI verbatim
.EXAMPLE
.\scripts\start.ps1
.\scripts\start.ps1 status
.\scripts\start.ps1 install -Enhanced
.\scripts\start.ps1 check
.\scripts\start.ps1 test
.\scripts\start.ps1 -NoVenv -- --config config/anonchains.conf status
#>
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VenvDir = Join-Path $Root ".venv"
$Requirements = Join-Path $Root "requirements.txt"
$Optional = Join-Path $Root "requirements-optional.txt"
$MainConfig = Join-Path $Root "config/anonchains.conf"

function Info($msg) { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "[+] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "[x] $msg" -ForegroundColor Red }

function Show-Usage {
    Write-Host @'
Usage: start.ps1 [mode|options] [-- <anonchains args>]

  check   | -CheckOnly    report environment only (exit 1 if incomplete)
  install | -InstallOnly  create .venv, install dependencies, then exit
  test    | -Test         run the offline pytest suite
  (none)                 start the CLI (interactive numeric menu)
  -Enhanced              also install requirements-optional.txt
  -NoVenv                use the current/system Python
  -h, --help             show this help
  --                     stop parsing; forward the rest to the CLI

  All other arguments are forwarded to "python -m anonchains" in order.
  Examples: start.ps1 status
            start.ps1 -NoVenv -- --config config/anonchains.conf status
'@
}

# --- argument parsing (order preserving) --------------------------------
$CheckOnly = $false
$InstallOnly = $false
$Test = $false
$Enhanced = $false
$NoVenv = $false
[string[]]$CliArgs = @()
$parsing = $true

foreach ($token in $args) {
    $text = [string]$token
    if ($parsing -and $text -eq '--') { $parsing = $false; continue }
    $handled = $false
    if ($parsing) {
        $key = $text.TrimStart('-').ToLowerInvariant()
        switch ($key) {
            'check'       { $CheckOnly = $true; $handled = $true }
            'checkonly'   { $CheckOnly = $true; $handled = $true }
            'install'     { $InstallOnly = $true; $handled = $true }
            'installonly' { $InstallOnly = $true; $handled = $true }
            'test'        { $Test = $true; $handled = $true }
            'enhanced'    { $Enhanced = $true; $handled = $true }
            'novenv'      { $NoVenv = $true; $handled = $true }
            'no-venv'     { $NoVenv = $true; $handled = $true }
            'h'           { Show-Usage; exit 0 }
            'help'        { Show-Usage; exit 0 }
        }
    }
    if ($handled) { continue }
    if ($parsing) { $parsing = $false }
    $CliArgs += $text
}

function Get-VenvPython {
    if ($env:OS -eq "Windows_NT") { return (Join-Path $VenvDir "Scripts/python.exe") }
    return (Join-Path $VenvDir "bin/python")
}

function Get-BasePython {
    foreach ($name in @("python", "python3")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw "Python not found. Install Python >= 3.10 and make sure it is on PATH."
}

function Resolve-Python {
    if (-not $NoVenv -and $env:VIRTUAL_ENV) {
        $candidate = if ($env:OS -eq "Windows_NT") {
            Join-Path $env:VIRTUAL_ENV "Scripts/python.exe"
        } else {
            Join-Path $env:VIRTUAL_ENV "bin/python"
        }
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    if ($NoVenv) { return (Get-BasePython) }

    $venvPython = Get-VenvPython
    if (Test-Path -LiteralPath $venvPython) { return $venvPython }
    $base = Get-BasePython
    if ($CheckOnly) { return $base }

    # Already-installed system Python is the fastest, fully offline path.
    if ((Get-CoreStatus $base) -eq "CORE=OK") {
        Info "Using system Python (core dependencies already present)"
        return $base
    }
    Info "Creating virtual environment at .venv ..."
    & $base -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment." }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "venv was created but no interpreter was found under .venv"
    }
    Ok "Virtual environment ready"
    return $venvPython
}

function Get-PythonLabel($python) {
    $text = [string]$python
    if ($text.StartsWith($VenvDir, [System.StringComparison]::OrdinalIgnoreCase)) {
        return ".venv/" + $text.Substring($VenvDir.Length).TrimStart('\', '/').Replace('\', '/')
    }
    return (Split-Path -Path $text -Leaf)
}

function Assert-PythonVersion($python) {
    $label = Get-PythonLabel $python
    $version = & $python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    if ($LASTEXITCODE -ne 0) { throw "Cannot run Python: $label" }
    & $python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Python >= 3.10 is required (found $version)." }
    Ok "Python $version ($label)"
}

# Only single quotes inside the Python snippets: PowerShell 5.1 strips double
# quotes when passing a multi-line string to a native executable.
$Probe = @'
core = ('aiohttp', 'cryptography', 'yaml')
optional = ('argon2.low_level', 'curl_cffi', 'pytest')
def missing(names):
    out = []
    for name in names:
        try:
            __import__(name)
        except Exception:
            out.append(name)
    return out
mc = missing(core)
mo = missing(optional)
print('CORE=' + ('OK' if not mc else 'MISSING:' + ','.join(mc)))
print('OPTIONAL=' + ','.join(mo))
'@

function Get-CoreStatus($python) {
    $lines = & $python -c $Probe
    if ($LASTEXITCODE -ne 0) { return "CORE=MISSING:probe-failed" }
    $line = @($lines | Where-Object { $_ -like "CORE=*" } | Select-Object -First 1)
    if ($line.Count) { return [string]$line[0] }
    return "CORE=MISSING:unknown"
}

Set-Location -LiteralPath $Root
Info "Project root: . ($(Split-Path -Path $Root -Leaf))"

$Python = Resolve-Python
Assert-PythonVersion $Python

$report = & $Python -c $Probe
if ($LASTEXITCODE -ne 0) { throw "Dependency probe failed." }
$coreLine = @($report | Where-Object { $_ -like "CORE=*" } | Select-Object -First 1)
$optionalLine = @($report | Where-Object { $_ -like "OPTIONAL=*" } | Select-Object -First 1)
$coreText = if ($coreLine.Count) { [string]$coreLine[0] } else { "CORE=MISSING:unknown" }
$optionalText = if ($optionalLine.Count) { ([string]$optionalLine[0]).Replace("OPTIONAL=", "") } else { "" }

if ($coreText -eq "CORE=OK") {
    Ok "Core dependencies: installed"
} else {
    Warn "Core dependencies: $coreText"
}
if ($optionalText) {
    Info "Optional packages missing: $optionalText"
} else {
    Ok "Optional packages: installed"
}

# --- check-only ---------------------------------------------------------
if ($CheckOnly) {
    if (Test-Path -LiteralPath $VenvDir) {
        Ok "Virtual environment: .venv"
    } else {
        Warn "Virtual environment: not created (run install.ps1)"
    }
    $checks = @{
        "requirements.txt"           = $Requirements
        "requirements-optional.txt"  = $Optional
        "config/anonchains.conf"     = $MainConfig
    }
    foreach ($key in $checks.Keys) {
        if (Test-Path -LiteralPath $checks[$key]) { Ok "${key}: found" } else { Fail "${key}: MISSING" }
    }
    $code = 0
    if ($coreText -ne "CORE=OK") { $code = 1 }
    if (-not (Test-Path -LiteralPath $MainConfig)) { $code = 1 }
    if ($code -eq 0) { Ok "Environment check passed" } else { Warn "Environment check incomplete; run install.ps1" }
    exit $code
}

# --- install ------------------------------------------------------------
foreach ($file in @($Requirements, $Optional)) {
    if (-not (Test-Path -LiteralPath $file)) {
        throw "Missing dependency file: $(Split-Path -Path $file -Leaf)"
    }
}

if ($coreText -ne "CORE=OK") {
    Info "Installing core dependencies from requirements.txt ..."
    & $Python -m pip install --disable-pip-version-check -r $Requirements
    if ($LASTEXITCODE -ne 0) { throw "pip install failed (check network access or proxy settings)." }
    Ok "Core dependencies installed"
} else {
    Info "Core dependencies already present; skipping install"
}

if ($Enhanced) {
    Info "Installing optional dependencies from requirements-optional.txt ..."
    & $Python -m pip install --disable-pip-version-check -r $Optional
    if ($LASTEXITCODE -ne 0) { throw "Optional dependency install failed." }
    Ok "Optional dependencies installed"
}

if ($InstallOnly) {
    Ok "Install finished. Start with: .\scripts\start.bat"
    exit 0
}

# --- tests --------------------------------------------------------------
if ($Test) {
    $pytestProbe = & $Python -c "import importlib.util; print('OK' if importlib.util.find_spec('pytest') else 'MISSING')"
    if ("$pytestProbe" -ne "OK") {
        Info "pytest not found; installing requirements-optional.txt ..."
        & $Python -m pip install --disable-pip-version-check -r $Optional
        if ($LASTEXITCODE -ne 0) { throw "Could not install pytest." }
    }
    Info "Running offline test suite ..."
    & $Python -m pytest tests -q
    exit $LASTEXITCODE
}

# --- start CLI ----------------------------------------------------------
Info "Starting anonchains ..."
if ($CliArgs.Count -gt 0) {
    & $Python -m anonchains @CliArgs
} else {
    & $Python -m anonchains
}
exit $LASTEXITCODE
