<#
AmSaJ Used Laptops - local deployment script for Windows PowerShell

Usage (run from the project folder, next to docker-compose.yml):
  .\deploy.ps1                 backup, build, start, migrate, check
  .\deploy.ps1 -Pull           also run "git pull" first
  .\deploy.ps1 -Test           also run the automated tests
  .\deploy.ps1 -NoBackup       skip the database backup
  .\deploy.ps1 -Update "C:\path\amsaj-phase4.zip"
                               copy new project files (from a zip or folder) into this
                               folder first. Your docker-compose.yml, .env and media are kept.
                               If the "orders" folder (Phase 4) is missing, the script asks
                               for this path automatically.

If PowerShell blocks the script, run this once in the same window:
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

This script NEVER deletes the database. It does not touch docker-compose.yml, so your
PostgreSQL port setting for DBeaver stays exactly as you configured it.
#>
param(
    [switch]$Pull,
    [switch]$Test,
    [switch]$NoBackup,
    [string]$Update = ""
)

# Docker writes progress messages to stderr, so errors are detected through exit codes instead.
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Step($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host ""; Write-Host "ERROR: $text" -ForegroundColor Red; exit 1 }

# Run a command and stop if it fails. Output is shown as normal.
function Run {
    & $args[0] $args[1..($args.Count - 1)]
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $($args -join ' ')" }
}

# Run a command quietly and return its text output (empty if it fails).
function Capture {
    $out = & $args[0] $args[1..($args.Count - 1)] 2>$null
    if ($LASTEXITCODE -ne 0) { return "" }
    return (($out | Out-String).Trim())
}


# Copy new project files (from a zip or an unzipped folder) into the current folder.
# Your own docker-compose.yml, .env, override file, media and backups are never overwritten.
function Install-ProjectUpdate([string]$path) {
    $path = $path.Trim().Trim('"').Trim("'")
    if (-not (Test-Path $path)) { Fail "Path not found: $path" }
    $temp = $null
    if ((Get-Item $path).PSIsContainer) {
        $root = $path
    } else {
        if ([IO.Path]::GetExtension($path) -ne ".zip") { Fail "Please give a .zip file or a folder." }
        $temp = Join-Path ([IO.Path]::GetTempPath()) ("amsaj_update_" + [guid]::NewGuid().ToString("N"))
        Expand-Archive -Path $path -DestinationPath $temp -Force
        $root = $temp
    }
    $manage = Get-ChildItem -Path $root -Recurse -Filter manage.py -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $manage) { Fail "manage.py was not found there. Is it the AmSaJ project zip or folder?" }
    $src = $manage.DirectoryName
    if ((Resolve-Path $src).Path -eq (Resolve-Path ".").Path) { Fail "That is the folder you are already in." }
    robocopy $src . /E /XF docker-compose.yml .env docker-compose.override.yml db.sqlite3 deploy.ps1 deploy.sh `
        /XD media backups .git __pycache__ /NJH /NJS /NDL | Out-Host
    if ($LASTEXITCODE -ge 8) { Fail "Copying the files failed (robocopy code $LASTEXITCODE)." }
    $global:LASTEXITCODE = 0
    if ($temp) { Remove-Item -Recurse -Force $temp }
    Write-Host "Project files updated. Your docker-compose.yml, .env and media were kept." -ForegroundColor Green
}

# Phase 4 needs a few extra settings in the web service of docker-compose.yml. Offer to add them.
function Ensure-ComposeSettings {
    $text = Get-Content "docker-compose.yml"
    if ($text | Select-String -SimpleMatch "BANK_ACCOUNT_NAME") { return }
    $index = -1
    for ($i = 0; $i -lt $text.Count; $i++) {
        if ($text[$i] -match '^\s*POSTGRES_HOST:\s*db\s*$') { $index = $i; break }
    }
    $wanted = @(
        'SITE_URL: ${SITE_URL:-http://localhost:8000}',
        'PAYMENT_GATEWAY: ${PAYMENT_GATEWAY:-test}',
        'BANK_ACCOUNT_NAME: ${BANK_ACCOUNT_NAME:-}',
        'BANK_NAME: ${BANK_NAME:-}',
        'BANK_ACCOUNT_NUMBER: ${BANK_ACCOUNT_NUMBER:-}',
        'BANK_IFSC: ${BANK_IFSC:-}',
        'BANK_UPI_ID: ${BANK_UPI_ID:-}'
    )
    Write-Host ""
    Write-Host "Your docker-compose.yml does not have the Phase 4 settings (payment gateway and bank details)." -ForegroundColor Yellow
    if ($index -lt 0) {
        Write-Host "Add these lines under 'environment:' of the web service, then run this script again:"
        $wanted | ForEach-Object { Write-Host "      $_" }
        return
    }
    $answer = Read-Host "Add them to the web service now? A copy of your file is saved as docker-compose.yml.bak (Y/n)"
    if ($answer -match '^[nN]') {
        Write-Host "Skipped. Without them the bank transfer details will not show on the order page."
        return
    }
    Copy-Item "docker-compose.yml" "docker-compose.yml.bak" -Force
    $indent = ([regex]::Match($text[$index], '^\s*')).Value
    $new = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt $text.Count; $i++) {
        $new.Add($text[$i])
        if ($i -eq $index) { foreach ($line in $wanted) { $new.Add($indent + $line) } }
    }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllLines((Join-Path (Get-Location) "docker-compose.yml"), $new, $utf8)
    Write-Host "docker-compose.yml updated (backup: docker-compose.yml.bak)." -ForegroundColor Green
}

# 1. Pre-flight checks ------------------------------------------------------
Step "Checking Docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Fail "Docker is not installed or not in PATH." }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail "Docker is not running. Start Docker Desktop and try again." }
docker compose version *> $null
if ($LASTEXITCODE -ne 0) { Fail "'docker compose' is not available. Update Docker Desktop." }
if (-not (Test-Path "docker-compose.yml")) { Fail "docker-compose.yml not found. Run this script from the project folder." }


# 1b. Make sure the project files are complete -------------------------------
$needsUpdate = $Update -ne "" -or -not (Test-Path "orders")
if ($needsUpdate) {
    if ($Update -eq "") {
        Step "Phase 4 files not found"
        Write-Host "This folder does not contain the 'orders' app (cart, checkout and orders)."
        Write-Host "Enter the path to amsaj-phase4.zip (or to the unzipped amsaj folder) and I will copy the files here."
        Write-Host "Your docker-compose.yml, .env and media will not be touched."
        $Update = Read-Host "Path (press Enter to skip)"
    }
    if ($Update -ne "") {
        Step "Copying project files"
        Install-ProjectUpdate $Update
        Ensure-ComposeSettings
    } else {
        Write-Host "Skipped. The shop will run without cart, checkout and orders." -ForegroundColor Yellow
    }
}

# 2. Optional: get the latest code ------------------------------------------
if ($Pull) {
    Step "Pulling the latest code from Git"
    $changes = git status --porcelain
    if ($changes) { Fail "You have uncommitted changes. Commit or stash them first (git status shows them)." }
    Run git pull --ff-only
}

# 3. Back up the database before changing anything --------------------------
if (-not $NoBackup) {
    Step "Backing up the database"
    $dbContainer = Capture docker compose ps -q db
    if ($dbContainer) {
        New-Item -ItemType Directory -Force -Path "backups" | Out-Null
        $dbUser = Capture docker compose exec -T db printenv POSTGRES_USER
        $dbName = Capture docker compose exec -T db printenv POSTGRES_DB
        $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        Run docker compose exec -T db pg_dump -U $dbUser -d $dbName -f "/tmp/backup_$stamp.sql"
        Run docker compose cp "db:/tmp/backup_$stamp.sql" "backups/amsaj_$stamp.sql"
        docker compose exec -T db rm -f "/tmp/backup_$stamp.sql" *> $null
        Write-Host "Saved backups/amsaj_$stamp.sql"
        # keep only the 10 newest backups
        Get-ChildItem "backups/amsaj_*.sql" | Sort-Object LastWriteTime -Descending |
            Select-Object -Skip 10 | Remove-Item -Force
    } else {
        Write-Host "Database container is not running yet, nothing to back up."
    }
}

# 4. Build and start --------------------------------------------------------
Step "Building and starting the containers"
Run docker compose up -d --build

# 5. Wait until the website answers -----------------------------------------
Step "Waiting for the website to be ready"
$ready = $false
$check = "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health/', timeout=3).read()==b'ok' else 1)"
for ($i = 1; $i -le 40; $i++) {
    docker compose exec -T web python -c $check *> $null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Write-Host -NoNewline "."
    Start-Sleep -Seconds 3
}
Write-Host ""
if (-not $ready) {
    Write-Host "The website did not become ready. Last log lines:"
    docker compose logs --tail 40 web
    Fail "Deployment failed. Your data is safe (see the backups folder)."
}
Write-Host "Website is up."

# 6. Database setup (safe to run every time) --------------------------------
Step "Applying database migrations and starter data"
Run docker compose exec -T web python manage.py migrate --noinput
# The starter-data and photo-resize steps are optional: a failure here is shown as a warning and does not stop the deployment.
foreach ($seed in @("seed_brands", "seed_shipping", "regenerate_images")) {
    docker compose exec -T web python manage.py $seed
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARNING: '$seed' failed or does not exist in this version of the project. Skipping it." -ForegroundColor Yellow
    }
}

if ($Test) {
    Step "Running automated tests"
    Run docker compose exec -T web python manage.py test
}

# 7. PostgreSQL access for DBeaver ------------------------------------------
Step "Checking PostgreSQL access for DBeaver"
$dbUser = Capture docker compose exec -T db printenv POSTGRES_USER
$dbName = Capture docker compose exec -T db printenv POSTGRES_DB
Run docker compose exec -T db pg_isready -U $dbUser -d $dbName
$mapping = Capture docker compose port db 5432
if (-not $mapping) {
    Write-Host ""
    Write-Host "WARNING: PostgreSQL is not published to your computer, so DBeaver cannot connect." -ForegroundColor Yellow
    Write-Host "Add this to the db service in docker-compose.yml, then run this script again:"
    Write-Host '    ports:'
    Write-Host '      - "127.0.0.1:5432:5432"'
} else {
    $hostPort = ($mapping -split ":")[-1]
    $open = Test-NetConnection -ComputerName 127.0.0.1 -Port $hostPort -InformationLevel Quiet -WarningAction SilentlyContinue
    if ($open) { Write-Host "Port $hostPort is open on this computer." }
    else { Write-Host "WARNING: port $hostPort is mapped but not answering yet." -ForegroundColor Yellow }
    Write-Host ""
    Write-Host "DBeaver connection (PostgreSQL):"
    Write-Host "  Host:      localhost"
    Write-Host "  Port:      $hostPort"
    Write-Host "  Database:  $dbName"
    Write-Host "  Username:  $dbUser"
    Write-Host "  Password:  the POSTGRES_PASSWORD from your .env or docker-compose.yml"
}

# 8. Summary ----------------------------------------------------------------
Step "Containers"
docker compose ps
Write-Host ""
Write-Host "Deployment finished." -ForegroundColor Green
Write-Host "  Shop:   http://localhost:8000/"
Write-Host "  Admin:  http://localhost:8000/admin/"
Write-Host "  Logs:   logs\activity.log (every activity) and logs\app.log (problems); live view: docker compose logs -f web"
Write-Host "  Trace:  docker compose exec web python manage.py trace ORD-000001"
Write-Host "  Stop:   docker compose down        (keeps your data; never add -v)"
