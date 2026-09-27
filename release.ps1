<#
Release PDFBoss: bump the version everywhere, build the C++ app, the
installer and the portable zip, commit and push the bump, publish a GitHub
release with both assets, then reinstall locally.

Usage:
  .\release.ps1 1.3.13
  .\release.ps1 1.3.13 -NotesFile notes.md      # release notes from a file
  .\release.ps1 1.3.13 -Notes "- fixed X"       # inline release notes
  .\release.ps1 1.3.13 -SkipInstall             # don't reinstall/relaunch here

Without -Notes/-NotesFile the GitHub notes are auto-generated from commits.

Builds PDFBossCpp (C++20 / wxWidgets / MuPDF).  The Python app is deprecated
and is NOT built here; its build path is guarded off deliberately, because it
would produce the same two asset names and downgrade every install.

Requires: cmake, Visual Studio 18 (2026) toolset v145, a built MuPDF at
C:\source\mupdf (see README), Inno Setup 6, gh (authenticated), git.
Windows PowerShell 5.1 compatible.
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,

    [string]$Notes = "",
    [string]$NotesFile = "",
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
# BOTH lines, and the second is not redundant.  Set-Location moves PowerShell's
# own location; it does NOT touch .NET's working directory, which is whatever
# the shell PROCESS was started in.  Bump() below pairs Test-Path (PowerShell,
# so it finds the file) with [IO.File]::ReadAllText (.NET, so it resolves the
# same relative path somewhere else entirely) -- the failure is a
# DirectoryNotFoundException naming a path that is half this repo and half the
# directory the shell happened to start in.  Running this script from a shell
# opened anywhere but the repo root is enough to hit it.
[Environment]::CurrentDirectory = $PSScriptRoot

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function CheckExit($what) {
    if ($LASTEXITCODE -ne 0) { Fail "$what failed (exit $LASTEXITCODE)" }
}

# Every native tool goes through this.  Windows PowerShell 5.1 turns each
# line a native command writes to stderr into an ErrorRecord whenever that
# stream is redirected -- including when the CALLER runs this whole script
# with `*>` or `2>&1` into a log -- and under the "Stop" above the first one
# is fatal.  git's "LF will be replaced by CRLF" warning on `git add` killed
# the v2.4.0 release that way, after the build and the artifacts but before
# the commit.  Native tools report failure through their exit code, which
# CheckExit reads, so their stderr is relaxed to non-fatal here and only
# here; cmdlets everywhere else still stop on error.
function Native {
    $ErrorActionPreference = "Continue"   # function scope: restored on return
    # Not `$exe, $rest = $args`: with exactly one argument after the exe that
    # makes $rest a plain STRING, and splatting a string passes it one
    # CHARACTER at a time -- ISCC then saw every letter of the .iss path as a
    # separate script and refused ("You may not specify more than one script
    # filename").  @(...) keeps it an array whatever the count.
    $exe = $args[0]
    $rest = @($args | Select-Object -Skip 1)
    & $exe @rest
}

# --- Preflight ---------------------------------------------------------------
# Inno Setup has moved between homes on this machine, so try each in turn
# rather than trusting one path. %LOCALAPPDATA% is NOT among them: the
# workspace rule puts AppData out of reach, and a fallback that quietly
# found ISCC there would be that rule broken by a path nobody typed. The
# per-user install was uninstalled on 2026-09-22; C:\bin\InnoSetup6 is home.
$isccCandidates = @(
    "C:\bin\InnoSetup6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { $iscc = $cmd.Source } else { Fail "ISCC.exe not found. Tried: $($isccCandidates -join '; ')" }
}
foreach ($tool in "cmake", "git", "gh") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { Fail "$tool not on PATH" }
}
if ($NotesFile -and -not (Test-Path $NotesFile)) { Fail "notes file not found: $NotesFile" }

# MuPDF is a pinned sibling tree, not a vcpkg package; without it the configure
# step fails with a long message and it is better to say so up front.
$mupdfLib = "C:\source\mupdf\platform\win32\x64\Release\libmupdf.lib"
if (-not (Test-Path $mupdfLib)) {
    Fail "MuPDF is not built: $mupdfLib is missing.`nSee the build prerequisites in README.md."
}

$dirty = Native git status --porcelain
if ($dirty) { Fail "working tree not clean -- commit or stash first:`n$dirty" }

# --- Bump versions -----------------------------------------------------------
# Version lockstep: every place the number appears moves together, in one
# commit.  That deliberately INCLUDES the deprecated Python app, so a file
# resurrected for archaeology never reports a version that never shipped.
Write-Host "==> Bumping version to $Version" -ForegroundColor Cyan

function Bump($path, $pattern, $replacement) {
    if (-not (Test-Path $path)) { Fail "not found: $path" }
    $text = [IO.File]::ReadAllText($path)
    if ($text -notmatch $pattern) { Fail "pattern not found in $path : $pattern" }
    [IO.File]::WriteAllText($path, ($text -replace $pattern, $replacement),
                            (New-Object Text.UTF8Encoding($false)))
}

Bump "PDFBossCpp\app\Version.h" `
     '#define PDFBOSS_VERSION_STRING "[^"]+"' "#define PDFBOSS_VERSION_STRING `"$Version`""
Bump "PDFBossCpp\CMakeLists.txt" `
     'project\(PDFBossCpp VERSION [0-9]+\.[0-9]+\.[0-9]+' "project(PDFBossCpp VERSION $Version"

# The .rc carries the numeric tuples Explorer shows, which cannot reference a
# string macro and so must be rewritten separately.  test_version.cpp asserts
# they stay in step with Version.h.
$tupleVersion = ($Version -replace '\.', ',') + ",0"
Bump "PDFBossCpp\app\PDFBoss.rc" `
     'FILEVERSION     [0-9]+,[0-9]+,[0-9]+,[0-9]+' "FILEVERSION     $tupleVersion"
Bump "PDFBossCpp\app\PDFBoss.rc" `
     'PRODUCTVERSION  [0-9]+,[0-9]+,[0-9]+,[0-9]+' "PRODUCTVERSION  $tupleVersion"
Bump "PDFBossCpp\installer-cpp.iss" `
     '#define AppVersion "[^"]+"' "#define AppVersion `"$Version`""
# Deprecated, still bumped -- see the note above.
Bump "app.py"        'APP_VERSION = "[^"]+"'      "APP_VERSION = `"$Version`""
Bump "installer.iss" '#define AppVersion "[^"]+"' "#define AppVersion `"$Version`""

# --- Build -------------------------------------------------------------------
# A running instance holds a lock on the exe and the link fails with LNK1104,
# which reads as "my change did nothing" rather than "close the app".
$running = Get-Process PDFBoss -ErrorAction SilentlyContinue
if ($running) {
    Fail "PDFBoss is running (pid $($running.Id -join ', ')). Close it and re-run: it holds a lock on the exe and the link would fail with LNK1104."
}

Write-Host "==> Configuring (CMake)" -ForegroundColor Cyan
Native cmake --preset windows-static -S PDFBossCpp
CheckExit "cmake configure"

Write-Host "==> Building (Release)" -ForegroundColor Cyan
Native cmake --build PDFBossCpp\build --config Release
CheckExit "cmake build"

Write-Host "==> Running tests" -ForegroundColor Cyan
Native ctest --test-dir PDFBossCpp\build -C Release --output-on-failure
CheckExit "ctest"

$exe = "PDFBossCpp\build\app\Release\PDFBoss.exe"
if (-not (Test-Path $exe)) { Fail "expected build output missing: $exe" }

# The shipped exe must need no VC++ redistributable.  Checked rather than
# assumed, because a stray /MD dependency only shows up on a machine that has
# never had Visual Studio installed -- i.e. a user's.
$dumpbin = Get-ChildItem "C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\MSVC" `
    -Filter dumpbin.exe -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match 'HostX64\\x64' } | Select-Object -First 1
if ($dumpbin) {
    $deps = Native $dumpbin.FullName /nologo /dependents $exe
    $bad = $deps | Select-String -Pattern 'VCRUNTIME|MSVCP\d|api-ms-win-crt'
    if ($bad) { Fail "exe depends on the VC++ runtime:`n$bad" }
    Write-Host "    static CRT verified" -ForegroundColor DarkGray
} else {
    Write-Host "    dumpbin not found -- skipping static-CRT check" -ForegroundColor Yellow
}

Write-Host "==> Building installer (ISCC)" -ForegroundColor Cyan
Native $iscc "PDFBossCpp\installer-cpp.iss"
CheckExit "ISCC"

Write-Host "==> Building portable zip" -ForegroundColor Cyan
# HELP.md rides along: the Help window looks for it beside the exe, and a
# portable copy without it answers F1 with "Help not found".
Compress-Archive -Force -Path $exe, "HELP.md" `
                 -DestinationPath "installer\PDFBoss-Portable.zip"

# Rename bridge.  Every copy from before the rename (v2.3.0 and earlier) polls
# the latest release for PDFSherpa-Setup.exe / PDFSherpa-Portable.zip, and a
# release without them leaves those installs silently stranded.  So both are
# shipped again under the old names, carrying the new app:
#   - the setup is the same installer, byte for byte.  It keeps the old AppId,
#     so it upgrades the existing install in place.
#   - the zip must hold an exe named PDFSherpa.exe, because the old portable
#     updater copies nothing unless it finds that exact name.  It is the new
#     build, renamed.
# Drop the bridge only once nobody can still be running a pre-rename copy.
Write-Host "==> Building rename-bridge assets" -ForegroundColor Cyan
Copy-Item -Force "installer\PDFBoss-Setup.exe" "installer\PDFSherpa-Setup.exe"
$bridgeDir = "installer\bridge"
if (Test-Path $bridgeDir) { Remove-Item -Recurse -Force $bridgeDir }
New-Item -ItemType Directory -Force $bridgeDir | Out-Null
Copy-Item $exe (Join-Path $bridgeDir "PDFSherpa.exe")
Copy-Item "HELP.md" $bridgeDir
Compress-Archive -Force -Path (Join-Path $bridgeDir "PDFSherpa.exe"), (Join-Path $bridgeDir "HELP.md") `
                 -DestinationPath "installer\PDFSherpa-Portable.zip"

# All four asset names are load-bearing: the in-app updaters match them exactly.
$assets = @("installer\PDFBoss-Setup.exe", "installer\PDFBoss-Portable.zip",
            "installer\PDFSherpa-Setup.exe", "installer\PDFSherpa-Portable.zip")
foreach ($asset in $assets) {
    if (-not (Test-Path $asset)) { Fail "expected artifact missing: $asset" }
}

# --- Commit + push -----------------------------------------------------------
Native git add PDFBossCpp\app\Version.h PDFBossCpp\app\PDFBoss.rc `
        PDFBossCpp\CMakeLists.txt `
        PDFBossCpp\installer-cpp.iss app.py installer.iss
CheckExit "git add"
$staged = Native git diff --cached --name-only
if ($staged) {
    Native git commit -m "Bump version to $Version"
    CheckExit "git commit"
} else {
    Write-Host "==> Versions already at $Version, nothing to commit" -ForegroundColor Yellow
}

Write-Host "==> Syncing with origin (README is sometimes edited on the web)" -ForegroundColor Cyan
Native git pull --rebase origin main
CheckExit "git pull --rebase"
Native git push origin main
CheckExit "git push"

# --- Publish release ---------------------------------------------------------
Write-Host "==> Publishing GitHub release v$Version" -ForegroundColor Cyan
$ghArgs = @("release", "create", "v$Version") + $assets + @("--title", "v$Version")
if ($NotesFile)  { $ghArgs += @("--notes-file", $NotesFile) }
elseif ($Notes)  { $ghArgs += @("--notes", $Notes) }
else             { $ghArgs += "--generate-notes" }
Native gh @ghArgs
CheckExit "gh release create"

# --- Local reinstall ---------------------------------------------------------
if (-not $SkipInstall) {
    Write-Host "==> Reinstalling locally and relaunching" -ForegroundColor Cyan
    # The installer now defaults to per-machine and asks, so a silent re-run
    # needs an explicit scope or it may land somewhere other than the existing
    # install.  /CURRENTUSER matches the historical per-user location.
    Start-Process (Join-Path $PSScriptRoot "installer\PDFBoss-Setup.exe") `
        -ArgumentList "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES", "/CURRENTUSER" -Wait
    # An install from before the rename keeps its "PDF Sherpa" folder (the
    # installer reuses the previous directory), so look in both.
    $installed = @("$env:LOCALAPPDATA\Programs\PDFBoss\PDFBoss.exe",
                   "$env:LOCALAPPDATA\Programs\PDF Sherpa\PDFBoss.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($installed) { Start-Process $installed }
}

Write-Host "==> Done: https://github.com/Flinterpop/PDFBoss/releases/tag/v$Version" -ForegroundColor Green
