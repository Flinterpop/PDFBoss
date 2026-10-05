; Inno Setup script for PDFBoss (C++ build).
;
; Build with:  iscc PDFBossCpp\installer-cpp.iss
; Requires the app to be built first:
;   cmake --preset windows-static && cmake --build build --config Release
;
; Produces installer\PDFBoss-Setup.exe.  Releases also ship
; installer\PDFBoss-Portable.zip (just the exe plus HELP.md, zipped);
; release.ps1 does the whole cycle -- see the README's build section.
;
; NOTE the asset name.  PDFBoss-Setup.exe is exactly what every installed
; copy polls for.  Copies from before the rename (v2.3.0 and earlier) poll
; for PDFSherpa-Setup.exe instead, so release.ps1 also attaches this same
; installer under that old name -- see the bridge note there.  The old
; installer.iss at the repo root refuses to run without an explicit override,
; so the deprecated Python build can never produce either file by accident.

#define AppName "PDFBoss"
#define AppVersion "2.5.0"
#define AppExe "PDFBoss.exe"
#define BuildDir "build\app\Release"

; The app was called "PDF Sherpa" until v2.4.0, and with no AppId set Inno
; takes AppName as the id.  Pinned to the OLD name so this installer is the
; same product to Windows: it upgrades an existing install in place (same
; uninstall entry, same folder via UsePreviousAppDir) instead of planting a
; second copy beside it.  Never change this value.
#define AppIdValue "PDF Sherpa"
#define OldAppName "PDF Sherpa"
#define OldAppExe "PDFSherpa.exe"

[Setup]
AppId={#AppIdValue}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=RabidFox
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
; An existing install keeps its "PDF Sherpa" folder (UsePreviousAppDir is on
; by default, and moving an install is not a silent update's job), but the
; Start-menu group follows the new name; the old one is removed below.
UsePreviousGroup=no
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\installer
OutputBaseFilename=PDFBoss-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

; The AGPL text is shown during install.  The shipped exe statically links
; MuPDF, so the binary is conveyed under AGPL-3.0 and the user is entitled to
; see the terms before installing.
LicenseFile=..\LICENSE

; Per-user, no elevation -- deliberately NOT the workspace default of
; per-machine-with-a-dialog, and this must not be "corrected" in this release.
;
; The v2.0.0 release is a MIGRATION release: it is applied by the updater
; built into the deprecated Python app, which every existing install is still
; running.  That updater invokes the installer as
;
;     "<setup>" /VERYSILENT /NORESTART /SUPPRESSMSGBOXES
;
; with no install-scope flag, because install_scope_flag did not exist when it
; shipped.  Against PrivilegesRequired=admin that silent run either fails to
; elevate, or elevates and installs to {commonpf}\PDF Sherpa while the
; existing copy stays in {userpf}\PDF Sherpa -- a second copy, with the old
; one still polling for updates.  Either way the migration breaks, for every
; existing user, silently.
;
; {autopf} under PrivilegesRequired=lowest resolves to {userpf}
; (%LOCALAPPDATA%\Programs), which is exactly where the Python installer put
; it, so the update lands on top of the existing install as intended.
;
; WHEN TO CHANGE THIS: once a release has shipped that everyone is running the
; C++ updater from, this can become PrivilegesRequired=admin plus
; PrivilegesRequiredOverridesAllowed=dialog.  That updater passes
; /ALLUSERS or /CURRENTUSER matching where the running exe actually lives --
; see install_scope_flag in PDFBossCpp\app\Updater.cpp, which exists for
; this and is tested for it.  Do not make that change in the same release
; that migrates people onto it.
PrivilegesRequired=lowest

[Files]
Source: "{#BuildDir}\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
; HELP.md is loaded at runtime by the Help window, which looks for it beside
; the executable first.  Omitting it turns F1 into "Help not found".
Source: "..\HELP.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme
; Rename bridge.  An install from before the rename is updated by a copy of
; PDFSherpa.exe, whose handoff batch relaunches PDFSherpa.exe by path once
; this installer exits -- and taskbar pins point at it too.  So where that
; file already exists it is kept, as a copy of the new build, rather than
; deleted: deleting it turns every silent update into "the app closed and
; never came back".  Fresh installs never get it.
Source: "{#BuildDir}\{#AppExe}"; DestDir: "{app}"; DestName: "{#OldAppExe}"; \
    Flags: ignoreversion; Check: OldExePresent

[InstallDelete]
; The old Start-menu group and desktop shortcut, which now point at an exe
; name the new shortcuts replace.  Only ever our own names.
Type: filesandordirs; Name: "{autoprograms}\{#OldAppName}"
Type: files; Name: "{autodesktop}\{#OldAppName}.lnk"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
    Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[Code]
function OldExePresent: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\{#OldAppExe}'));
end;
