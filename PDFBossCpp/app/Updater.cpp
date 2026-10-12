#include "Updater.h"

#include <algorithm>
#include <cassert>
#include <cctype>
#include <cstdlib>
#include <fstream>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <shellapi.h>  // ShellExecuteExW; WIN32_LEAN_AND_MEAN leaves it out

#include "PathUtf8.h"

namespace pdfboss {
namespace {

namespace fs = std::filesystem;

static_assert(kLaunchCancelled == ERROR_CANCELLED,
              "kLaunchCancelled must be the error a declined UAC prompt gives");

std::string environment(const char* name)
{
    assert(name != nullptr);
    char* value = nullptr;
    std::size_t size = 0;
    if (_dupenv_s(&value, &size, name) != 0 || value == nullptr) {
        return {};
    }
    std::string out(value);
    std::free(value);
    return out;
}

std::string to_lower(std::string text)
{
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return text;
}

bool under_root(const fs::path& candidate, const std::string& root)
{
    if (root.empty()) {
        return false;
    }
    const std::string a = to_lower(path_to_utf8(candidate.lexically_normal()));
    std::string b = to_lower(path_to_utf8(path_from_utf8(root).lexically_normal()));
    if (!b.empty() && b.back() != '\\' && b.back() != '/') {
        b += '\\';
    }
    return a.rfind(b, 0) == 0;
}

// How long a handoff batch waits, in polls of about a second each.  The app
// closes within seconds.  The installer waits up to a minute for the app,
// then installs and may remove a per-user copy, so it gets a larger bound.
constexpr int kAppExitPolls = 60;
constexpr int kInstallerExitPolls = 300;

// The batch preamble that waits for process `pid` to exit.
//
// Every detail here is load-bearing and was learned the hard way in MDBoss:
//   - `timeout` cannot provide the delay: with no console it exits at once
//     with "Input redirection is not supported", collapsing the wait.  ping
//     does the job.
//   - Every tool is called by absolute %SystemRoot%\System32 path.  Git for
//     Windows puts a GNU `find` on PATH which fails on the /I-style arguments,
//     and that failure reads as "already exited" -- so the wait is skipped and
//     the installer races the still-running app.
//   - It waits on the PID, not the image name, or a second build of the same
//     app sitting open makes the loop burn its full timeout.
std::string wait_for_exit_header(unsigned long pid, int max_polls)
{
    assert(pid != 0 && "a pid of 0 would match nothing and never wait");
    assert(max_polls > 0 && "the wait loop needs a bound");
    const std::string sys32 = "%SystemRoot%\\System32";
    const std::string pid_text = std::to_string(pid);

    std::string batch;
    batch += "@echo off\r\n";
    batch += "\"" + sys32 + "\\PING.EXE\" -n 3 127.0.0.1 >nul\r\n";
    batch += "set /a _n=0\r\n";
    batch += ":pswait\r\n";
    batch += "\"" + sys32 + "\\tasklist.exe\" /FI \"PID eq " + pid_text +
             "\" 2>nul | \"" + sys32 + "\\find.exe\" \"" + pid_text +
             "\" >nul\r\n";
    batch += "if errorlevel 1 goto psgo\r\n";
    batch += "set /a _n+=1\r\n";
    batch += "if %_n% GEQ " + std::to_string(max_polls) + " goto psgo\r\n";
    batch += "\"" + sys32 + "\\PING.EXE\" -n 2 127.0.0.1 >nul\r\n";
    batch += "goto pswait\r\n";
    batch += ":psgo\r\n";
    return batch;
}

}  // namespace

std::optional<std::vector<int>> parse_version(const std::string& text)
{
    std::string trimmed = text;
    while (!trimmed.empty() && (trimmed.front() == 'v' || trimmed.front() == 'V')) {
        trimmed.erase(0, 1);
    }
    if (trimmed.empty()) {
        return std::nullopt;
    }

    std::vector<int> parts;
    std::size_t start = 0;
    while (start <= trimmed.size()) {
        const std::size_t dot = trimmed.find('.', start);
        const std::string piece = trimmed.substr(
            start, (dot == std::string::npos) ? std::string::npos : dot - start);
        if (piece.empty()) {
            return std::nullopt;
        }
        for (const char c : piece) {
            if (c < '0' || c > '9') {
                return std::nullopt;  // a beta tag, not a version
            }
        }
        parts.push_back(std::atoi(piece.c_str()));
        if (dot == std::string::npos) {
            break;
        }
        start = dot + 1;
    }
    return parts;
}

bool is_newer(const std::vector<int>& candidate, const std::vector<int>& current)
{
    // Compares element-wise, treating a missing element as 0, so 1.4 beats
    // 1.3.9 and 1.3 does not beat 1.3.0.
    const std::size_t count = std::max(candidate.size(), current.size());
    for (std::size_t i = 0; i < count; ++i) {
        const int a = (i < candidate.size()) ? candidate[i] : 0;
        const int b = (i < current.size()) ? current[i] : 0;
        if (a != b) {
            return a > b;
        }
    }
    return false;
}

bool running_portable(const fs::path& app_exe)
{
    const fs::path dir = app_exe.parent_path();

    std::error_code ec;
    fs::directory_iterator it(dir, ec);
    if (ec) {
        return true;  // when in doubt, prefer the less invasive path
    }
    for (const fs::directory_entry& entry : it) {
        const std::string name = to_lower(path_to_utf8(entry.path().filename()));
        if (name.rfind("unins", 0) == 0 &&
            name.size() > 4 && name.compare(name.size() - 4, 4, ".exe") == 0) {
            return false;  // Inno Setup's uninstaller: this is an install
        }
    }
    return true;
}

bool installed_machine_wide(const fs::path& app_exe)
{
    assert(!app_exe.empty() && "no exe path to classify");
    // ProgramW6432 covers a 32-bit process on 64-bit Windows; the (x86) root
    // covers an exe that was installed there anyway.
    const char* const roots[] = {"ProgramFiles", "ProgramW6432",
                                 "ProgramFiles(x86)"};
    for (const char* const name : roots) {
        if (under_root(app_exe, environment(name))) {
            return true;
        }
    }
    return false;
}

std::string installer_arguments(unsigned long app_pid)
{
    assert(app_pid != 0 && "the installer must have a process to wait for");
    // /ALLUSERS states the scope even though the installer allows no other,
    // so a script that ever regains a per-user choice cannot silently take it.
    std::string arguments =
        "/VERYSILENT /NORESTART /SUPPRESSMSGBOXES /ALLUSERS /WAITPID=" +
        std::to_string(app_pid);
    assert(arguments.find("/CURRENTUSER") == std::string::npos);
    return arguments;
}

InstallerLaunch launch_installer_elevated(const fs::path& setup_path,
                                          const std::string& arguments,
                                          void* owner)
{
    assert(!setup_path.empty() && "nothing to launch");
    assert(!arguments.empty() && "a silent install needs its switches");

    // The arguments are plain ASCII switches and digits (installer_arguments).
    std::wstring wide_arguments;
    for (const char c : arguments) {
        assert(static_cast<unsigned char>(c) < 0x80 && "arguments are ASCII");
        wide_arguments.push_back(static_cast<wchar_t>(c));
    }
    const std::wstring file = setup_path.wstring();

    SHELLEXECUTEINFOW info = {};
    info.cbSize = sizeof(info);
    // NOASYNC: the call returns only once the launch has finished (UAC
    // answered).  FLAG_NO_UI: the caller reports a failure in its own words.
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI;
    info.hwnd = static_cast<HWND>(owner);
    info.lpVerb = L"runas";
    info.lpFile = file.c_str();
    info.lpParameters = wide_arguments.c_str();
    info.nShow = SW_SHOWNORMAL;

    InstallerLaunch result;
    if (::ShellExecuteExW(&info) == FALSE) {
        result.error = ::GetLastError();
        return result;
    }
    if (info.hProcess == nullptr) {
        // Started, but with no handle to wait on.  The installer waits a
        // minute for this app and then gives up, so reporting failure and
        // staying open is still safe.
        result.error = ERROR_INVALID_HANDLE;
        return result;
    }
    result.pid = ::GetProcessId(info.hProcess);
    if (result.pid == 0) {
        result.error = ::GetLastError();
    }
    ::CloseHandle(info.hProcess);
    return result;
}

std::string installer_batch(const fs::path& setup_path, const fs::path& app_exe,
                            unsigned long installer_pid)
{
    assert(!setup_path.empty() && "nothing to clean up");
    assert(!app_exe.empty() && "nothing to relaunch");

    const std::string setup = path_to_utf8(setup_path);
    const std::string exe = path_to_utf8(app_exe);
    const std::string machine = kMachineInstallExe;

    // The installer itself is already running, elevated; this batch only
    // waits for it and relaunches unelevated.  goto, not a parenthesised if
    // block: a path such as "Program Files (x86)" would close the block.
    std::string batch = wait_for_exit_header(installer_pid, kInstallerExitPolls);
    if (!installed_machine_wide(app_exe)) {
        // A per-user or stray copy: the update installed to Program Files and
        // removed the per-user copy, so start the machine one if it is there.
        batch += "if not exist \"" + machine + "\" goto psown\r\n";
        batch += "start \"\" \"" + machine + "\"\r\n";
        batch += "goto psclean\r\n";
    }
    batch += ":psown\r\n";
    batch += "start \"\" \"" + exe + "\"\r\n";
    batch += ":psclean\r\n";
    batch += "del /q \"" + setup + "\"\r\n";
    batch += "del /q \"%~f0\"\r\n";
    return batch;
}

std::string portable_batch(const fs::path& zip_path, const fs::path& staging_dir,
                           const fs::path& app_exe, unsigned long pid)
{
    assert(!zip_path.empty() && "nothing to unpack");
    assert(!staging_dir.empty() && "nowhere to unpack to");
    assert(!app_exe.empty() && "nothing to relaunch");

    const std::string zip = path_to_utf8(zip_path);
    const std::string staging = path_to_utf8(staging_dir);
    const std::string exe = path_to_utf8(app_exe);
    const std::string app_dir = path_to_utf8(app_exe.parent_path());
    const std::string sys32 = "%SystemRoot%\\System32";

    // Extract, check, and only then copy.  A zip with no PDFBoss.exe -- at
    // the root or one folder down, the two layouts app.py accepts -- copies
    // nothing, and the relaunch line runs either way: a failed update is a
    // no-op, not a brick.  robocopy copies OVER the install rather than
    // replacing it, for the same reason _swap_portable_and_exit does.
    std::string batch = wait_for_exit_header(pid, kAppExitPolls);
    batch += "md \"" + staging + "\" 2>nul\r\n";
    batch += "\"" + sys32 + "\\tar.exe\" -xf \"" + zip + "\" -C \"" + staging +
             "\"\r\n";
    batch += "set \"_src=\"\r\n";
    batch += "if exist \"" + staging + "\\PDFBoss.exe\" set \"_src=" +
             staging + "\"\r\n";
    batch += "if exist \"" + staging + "\\PDFBoss\\PDFBoss.exe\" set \"_src=" +
             staging + "\\PDFBoss\"\r\n";
    batch += "if \"%_src%\"==\"\" goto psrelaunch\r\n";
    batch += "\"" + sys32 + "\\robocopy.exe\" \"%_src%\" \"" + app_dir +
             "\" /E /IS /NFL /NDL /NJH /NJS /NP >nul\r\n";
    batch += ":psrelaunch\r\n";
    batch += "start \"\" \"" + exe + "\"\r\n";
    batch += "\"" + sys32 + "\\timeout.exe\" /t 1 >nul 2>&1\r\n";
    batch += "rd /s /q \"" + staging + "\" 2>nul\r\n";
    batch += "del /q \"" + zip + "\" 2>nul\r\n";
    batch += "del /q \"%~f0\"\r\n";
    return batch;
}

bool spawn_handoff_batch(const std::string& batch, const fs::path& batch_path)
{
    {
        std::ofstream stream(batch_path, std::ios::binary | std::ios::trunc);
        if (!stream) {
            return false;
        }
        stream << batch;
        if (!stream.good()) {
            return false;
        }
    }

    std::wstring command = L"cmd.exe /c \"" + batch_path.wstring() + L"\"";

    STARTUPINFOW startup = {};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process = {};

    // These flags exactly, and no others.
    //
    // DETACHED_PROCESS must NOT be added: it is mutually exclusive with
    // CREATE_NO_WINDOW, and the child then gets no usable stdin -- so the wait
    // loop's `tasklist | find` blocks in find.exe forever and the install line
    // is never reached.  The symptom is maximally unhelpful: the app closes
    // and nothing else happens at all.  CREATE_BREAKAWAY_FROM_JOB is a second
    // trap, failing the whole call when the job object forbids breakaway.
    // Neither is needed: cmd.exe is not tied to this process's lifetime.
    const BOOL ok = ::CreateProcessW(
        nullptr, command.data(), nullptr, nullptr, FALSE,
        CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, nullptr, nullptr,
        &startup, &process);

    if (ok == FALSE) {
        return false;
    }
    ::CloseHandle(process.hProcess);
    ::CloseHandle(process.hThread);
    return true;
}

}  // namespace pdfboss
