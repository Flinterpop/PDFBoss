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
#define AppVersion "2.6.0"
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

; Machine-level only, into Program Files (house rule, 11 Oct 2026): no
; per-user choice, so there is deliberately NO PrivilegesRequiredOverridesAllowed
; line, and /ALLUSERS or /CURRENTUSER on the command line changes nothing.
; Under admin, {autopf} resolves to {commonpf}.
;
; Every install before this one was PER-USER (PrivilegesRequired=lowest, in
; %LOCALAPPDATA%\Programs\PDF Sherpa or ...\PDFBoss), so the first update
; onto this installer is a MIGRATION.  The per-user copy is registered under
; HKCU with the same AppId; after a successful install RemovePerUserCopy (in
; [Code]) runs that copy's own uninstaller -- asking first when the wizard is
; shown, automatically when silent, since an update is the operator's yes --
; so nobody is left with two copies, the old one still polling for updates.
;
; Old updaters (v2.5.0 and earlier, and every pre-rename PDF Sherpa) run this
; silently and then relaunch the per-user exe by path, which the migration has
; just removed.  So when a silent run migrated and no /WAITPID was given (the
; new updater passes one and relaunches for itself), the [Run] entry below
; starts the new copy instead, as the original, unelevated user.
PrivilegesRequired=admin

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
; An old updater's relaunch line names the per-user exe the migration removed;
; start the new copy for it.  runasoriginaluser: never hand the user an
; elevated app (drag and drop from Explorer is blocked into one).
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; \
    Check: RelaunchForOldUpdater

[Code]
const
  SYNCHRONIZE = $00100000;
  WAIT_OBJECT_0 = 0;
  // The updater has closed within a second or two in practice; past this it
  // is not closing, and installing over a running exe is worse than not.
  UpdaterExitTimeoutMs = 60000;
  // A per-user install of the same AppId, made by every installer before
  // this one.  Inno appends "_is1" to the AppId for its uninstall key.
  PerUserUninstallKey =
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppIdValue}_is1';
  PerUserRemovalPolls = 40;  // x 250 ms: the uninstaller's second phase

var
  MigratedPerUser: Boolean;

function OpenProcess(DesiredAccess: Cardinal; InheritHandle: Integer;
  ProcessId: Cardinal): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(Handle: THandle; Milliseconds: Cardinal): Cardinal;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Integer;
  external 'CloseHandle@kernel32.dll stdcall';

function OldExePresent: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\{#OldAppExe}'));
end;

// The updater built into PDFBoss starts this installer elevated while the app
// is still open -- so a declined UAC prompt leaves the app running rather than
// closed -- and passes its own process id as /WAITPID=<pid>.  Wait for that
// process to exit before any file is touched; give up (and install nothing)
// if it does not.
function WaitForUpdaterExit(): Boolean;
var
  Pid: Cardinal;
  Process: THandle;
begin
  Result := True;
  Pid := StrToIntDef(ExpandConstant('{param:WAITPID|0}'), 0);
  if Pid = 0 then
    Exit;
  Process := OpenProcess(SYNCHRONIZE, 0, Pid);
  if Process = 0 then
    Exit;  // already gone
  Result := WaitForSingleObject(Process, UpdaterExitTimeoutMs) = WAIT_OBJECT_0;
  CloseHandle(Process);
  if not Result then
    Log('The updating PDFBoss (pid ' + IntToStr(Pid) + ') did not exit; not installing.');
end;

function InitializeSetup(): Boolean;
begin
  MigratedPerUser := False;
  Result := WaitForUpdaterExit();
end;

// A machine install must never land in a user's profile, whatever directory
// Inno would otherwise reuse from a previous install.
procedure InitializeWizard();
var
  Profile: String;
begin
  Profile := Lowercase(AddBackslash(GetEnv('USERPROFILE')));
  if (Length(Profile) > 1) and
     (Pos(Profile, Lowercase(AddBackslash(WizardForm.DirEdit.Text))) = 1) then
    WizardForm.DirEdit.Text := ExpandConstant('{autopf}\{#AppName}');
end;

// The per-user copy's uninstaller, or '' when there is none.
function PerUserUninstaller(var Dir: String): String;
var
  Value: String;
begin
  Result := '';
  Dir := '';
  if not RegQueryStringValue(HKCU, PerUserUninstallKey, 'UninstallString', Value) then
    Exit;
  Value := RemoveQuotes(Value);
  if not FileExists(Value) then
    Exit;
  if not RegQueryStringValue(HKCU, PerUserUninstallKey, 'InstallLocation', Dir) then
    Dir := ExtractFilePath(Value);
  Result := Value;
end;

procedure RemovePerUserCopy();
var
  Uninstaller, Dir: String;
  Code, I: Integer;
begin
  Uninstaller := PerUserUninstaller(Dir);
  if Uninstaller = '' then
    Exit;
  if SuppressibleMsgBox('An older per-user copy of {#AppName} is installed in' + #13#10 +
       Dir + #13#10 + #13#10 +
       '{#AppName} is now installed for all users in' + #13#10 +
       ExpandConstant('{app}') + #13#10 + #13#10 +
       'Remove the per-user copy? (Recommended: it would keep its own ' +
       'shortcuts and keep offering updates.)',
       mbConfirmation, MB_YESNO, IDYES) <> IDYES then begin
    Log('Per-user copy kept at the operator''s request: ' + Dir);
    Exit;
  end;
  if not Exec(Uninstaller, '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART', '',
              SW_HIDE, ewWaitUntilTerminated, Code) then
    Log('Could not start the per-user uninstaller: ' + SysErrorMessage(Code))
  else
    for I := 1 to PerUserRemovalPolls do begin
      if not RegKeyExists(HKCU, PerUserUninstallKey) then
        Break;
      Sleep(250);
    end;
  MigratedPerUser := not RegKeyExists(HKCU, PerUserUninstallKey);
  if not MigratedPerUser then
    SuppressibleMsgBox('The per-user copy in' + #13#10 + Dir + #13#10 +
       'could not be removed.  Uninstall it from Apps & Features.',
       mbError, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  // After the new copy is in place, so a failed install keeps the old one.
  if CurStep = ssPostInstall then
    RemovePerUserCopy();
end;

function RelaunchForOldUpdater(): Boolean;
begin
  Result := WizardSilent() and MigratedPerUser and
            (ExpandConstant('{param:WAITPID|0}') = '0');
end;
