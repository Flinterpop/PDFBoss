// Check GitHub for a newer release, download it, and hand off to an installer
// or an in-place exe swap.
//
// Ported from app.py's _start_update_check / _fetch_latest_release /
// _prompt_update / _begin_download / _launch_installer_and_exit /
// _spawn_handoff_batch / _swap_portable_and_exit, with the batch-file and
// install-scope logic taken from MDBossCpp/app/Updater.cpp rather than
// re-derived -- see the comments there and in the notes below, all of which
// record bugs that only showed up against a real published release.

#ifndef PDFBOSS_APP_UPDATER_H
#define PDFBOSS_APP_UPDATER_H

#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace pdfboss {

// The two asset names published on every release.  Old installs poll for
// exactly these, which is why the C++ app inherits them rather than picking
// new ones: republishing either name is what migrates existing users.
inline constexpr const char* kSetupAssetName = "PDFBoss-Setup.exe";
inline constexpr const char* kPortableAssetName = "PDFBoss-Portable.zip";
inline constexpr const char* kReleaseApiUrl =
    "https://api.github.com/repos/Flinterpop/PDFBoss/releases/latest";
inline constexpr const char* kReleasesPageUrl =
    "https://github.com/Flinterpop/PDFBoss/releases/latest";

struct ReleaseInfo {
    std::vector<int> version;   // parsed tag, e.g. {1, 3, 12}
    std::string version_str;    // the tag with any leading v stripped
    std::string setup_url;      // installer asset, or a fallback .exe
    std::string portable_url;   // portable zip asset, empty if absent
    std::string html_url;       // the release page, for "What's new"
};

// 'v1.3.3' / '1.3.3' -> {1,3,3}; nullopt for anything else (a beta tag, say).
std::optional<std::vector<int>> parse_version(const std::string& text);

// True when `candidate` is strictly newer than `current`.
bool is_newer(const std::vector<int>& candidate, const std::vector<int>& current);

// True when `app_exe` is a loose portable copy rather than an installed one.
//
// The test is the absence of Inno Setup's uninstaller beside the exe.  When in
// doubt it answers "portable", because an in-place exe swap is less invasive
// than silently running an installer.
//
// The exe path is a parameter rather than looked up internally so this file
// needs no wxWidgets, which is what lets the whole updater live in the
// headless core and be unit-tested without starting a GUI.
bool running_portable(const std::filesystem::path& app_exe);

// Where the installer puts the app, as a path cmd.exe expands.  Installs are
// machine-level only (house rule, 11 Oct 2026), so an install that was
// per-user before the update is here afterwards.
inline constexpr const char* kMachineInstallExe =
    "%ProgramFiles%\\PDFBoss\\PDFBoss.exe";

// True when `app_exe` lives under Program Files -- a machine install, which an
// update replaces in place.
bool installed_machine_wide(const std::filesystem::path& app_exe);

// The installer's command line.  Always /ALLUSERS, never /CURRENTUSER: the
// installer is machine-level only.  /WAITPID=<pid> makes it wait for the
// updating app (process `app_pid`) to exit before it touches a file.
std::string installer_arguments(unsigned long app_pid);

// ERROR_CANCELLED, spelled out so callers need no <windows.h>: the error
// ShellExecuteEx reports when the UAC prompt was declined.
inline constexpr unsigned long kLaunchCancelled = 1223;

// The outcome of starting the installer: its process id, or 0 and the
// Windows error (kLaunchCancelled when the UAC prompt was declined).
struct InstallerLaunch {
    unsigned long pid = 0;
    unsigned long error = 0;
};

// Start the installer ELEVATED (ShellExecuteEx "runas") while the app is still
// running, so a declined prompt leaves the app open instead of closed with
// nothing installed.  CreateProcess cannot do this: it fails with
// ERROR_ELEVATION_REQUIRED on anything that asks for admin.  `owner` is the
// window the UAC prompt belongs to, as an HWND; it may be null.
InstallerLaunch launch_installer_elevated(const std::filesystem::path& setup_path,
                                          const std::string& arguments,
                                          void* owner);

// The batch that waits for the installer (process `installer_pid`) to finish,
// then relaunches the app UNELEVATED -- the installer runs elevated, and an app
// it started would be too.  An update that migrated a per-user install
// relaunches the machine copy, since the per-user exe is gone.
std::string installer_batch(const std::filesystem::path& setup_path,
                            const std::filesystem::path& app_exe,
                            unsigned long installer_pid);

// The batch that waits, unpacks the portable zip, and copies it over the
// install only if it really contains PDFBoss.exe.
std::string portable_batch(const std::filesystem::path& zip_path,
                           const std::filesystem::path& staging_dir,
                           const std::filesystem::path& app_exe,
                           unsigned long pid);

// Write `batch` to a .bat and start it detached, so it outlives this process.
bool spawn_handoff_batch(const std::string& batch,
                         const std::filesystem::path& batch_path);

}  // namespace pdfboss

#endif  // PDFBOSS_APP_UPDATER_H
