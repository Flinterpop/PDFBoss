; DEPRECATED -- packages the retired Python/Tkinter app.
;
; PDFSherpaCpp\installer-cpp.iss is the shipping installer.  This one is kept
; only so an old build can be reproduced for archaeology.
;
; It refuses to compile without /DAllowDeprecatedPythonBuild because it emits
; installer\PDFSherpa-Setup.exe -- the exact asset name every installed copy
; polls for.  Publishing it would silently downgrade every install back onto
; the dead app.  NEVER publish what this produces.
;
; Build (archaeology only):
;   iscc /DAllowDeprecatedPythonBuild installer.iss
; Requires:  set PDFSHERPA_BUILD_DEPRECATED_PYTHON=1 && python -m PyInstaller PDFSherpa.spec

#ifndef AllowDeprecatedPythonBuild
  #error This script builds the DEPRECATED Python app. The shipping installer is PDFSherpaCpp\installer-cpp.iss. For local archaeology only, pass /DAllowDeprecatedPythonBuild -- and never publish the result.
#endif

#define AppName "PDF Sherpa"
#define AppVersion "2.4.1"
#define AppExe "PDFSherpa.exe"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=RabidFox
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=installer
OutputBaseFilename=PDFSherpa-Setup
Compression=lzma2
SolidCompression=yes
; Per-user install so no admin rights are needed.
PrivilegesRequired=lowest
WizardStyle=modern

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

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
