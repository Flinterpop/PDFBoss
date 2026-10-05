# CLAUDE.md

*Last updated: 25 Sep 2026*

Guidance for Claude Code working in this repository.

## What this repo is

A topic-indexed PDF browser and annotator for Windows. Two implementations live here, but they are **not** peers:

- **`PDFBossCpp/`** — C++20 on wxWidgets and MuPDF. **This is the reference implementation and the only thing that ships** (installer + portable zip). When something is ambiguous, the C++ app defines the behaviour.
- **`app.py` + `tocgen.py`** — Python/Tkinter. **Deprecated.** Kept in-tree as a historical reference and parity record, but no longer the oracle, and not built, packaged, or shipped. Do not treat a difference from the Python app as a bug in the C++ app any more — if a change is wanted, it is made in `PDFBossCpp/` and the Python side is left alone. Touch `app.py`/`tocgen.py` only for an explicit request about the legacy app.

**The repo is public.** Everything here is world-readable the moment it is pushed. The app's own source is releasable; the *documents* it is used on (`C:\ICD`) are export-controlled and must never appear here, in a test fixture, or in a commit message.

## Licence: AGPL, and why it is not MIT

The shipped executable statically links **MuPDF**, which is AGPL-3.0-or-later, so the combined work is conveyed under the AGPL and `LICENSE` matches. This was corrected during the C++ port; earlier releases carried an MIT notice while already bundling MuPDF through PyMuPDF, which was wrong.

Practical consequences:

- Section 13 (the network clause) is inert — this is a desktop app with no network service. The real obligation is that corresponding source stays published, which it is.
- Do not reintroduce an MIT badge or notice anywhere.
- If a permissive licence is ever wanted back, the engine has to change (PDFium is BSD-3), not the label.

## Renamed from PDF Sherpa to PDFBoss (v2.4.0) — the bridge is load-bearing

The app, exe, installer, source tree (`PDFSherpaCpp/` → `PDFBossCpp/`), namespace and GitHub repo (`Flinterpop/PDF_Sherpa` → `Flinterpop/PDFBoss`) were all renamed in one go. Every copy of v2.3.0 and earlier still out there polls `…/PDF_Sherpa/releases/latest` (GitHub redirects it) for **`PDFSherpa-Setup.exe`** / **`PDFSherpa-Portable.zip`**, so three things keep them updating:

- **`release.ps1` attaches both old asset names to every release**: the same installer copied, and a zip whose exe is named `PDFSherpa.exe` (the old portable updater copies nothing without that exact name). Drop it only when no pre-rename copy can still be running.
- **`installer-cpp.iss` pins `AppId` to `PDF Sherpa`.** Inno took the id from `AppName` before, so this keeps the new installer the same product: it upgrades in place, in the old `PDF Sherpa` folder. Never change it.
- **Where `{app}\PDFSherpa.exe` already exists, the installer refreshes it as a copy of the new build** instead of deleting it. The old updater's handoff batch relaunches that path after the silent install, and taskbar pins point at it; deleting it means the app closes and never comes back.

`%APPDATA%\PDFGuide` is unaffected: it outlived the previous rename too. The words "Introduction to the Sherpa" in the test fixtures are baked into the committed PDFs and golden values; they are fixture text, not the app's name. The deprecated `app.py`, `tocgen.py`, `installer.iss`, `PDFSherpa.spec` and the session log `4July2026.txt` keep the old name as frozen history — only their icon paths moved.

## The deprecated Python build is guarded off, not merely unused

`PDFSherpa.spec` exits unless `PDFSHERPA_BUILD_DEPRECATED_PYTHON` is set, and `installer.iss` refuses without `/DAllowDeprecatedPythonBuild`.

This matters because both produce **`PDFSherpa-Setup.exe`** and **`PDFSherpa-Portable.zip`** — the exact two asset names every pre-rename copy polls for, and which every release now carries as the rename bridge. Regenerating one by accident and publishing it would silently "update" every install back onto the dead app. The overrides exist for local archaeology (bisecting an old bug, reproducing a past release); **never publish what they produce.**

Note this is the *opposite* rule from the version lockstep below: a deprecated file keeps getting its version bumped but must not be built.

## Linux and macOS were retired at v2.0.0

Up to v1.3.12 this was a cross-platform app: `app.py` carried Linux/macOS support, `build-appimage.sh` produced a **self-updating** `PDFSherpa-x86_64.AppImage` (plus a `.zsync`), and `install-linux.sh` / `run.sh` installed and launched it. v1.3.12's release assets still include the AppImage and its `.zsync`.

That is over. The C++ app is Windows-only — wxWidgets is portable, but the viewer, the drop target, the shell integration, the updater's handoff batch and the Inno Setup packaging are all Win32 — and the scripts and cross-platform docs were removed in the same commit that says so. The Python code in `app.py` still contains its platform branches; it is frozen reference and is not the place to revive anything.

Consequences worth knowing:

- A Linux user on the self-updating AppImage will find no AppImage asset in releases from v2.0.0 on. Their updater goes quiet and they stay on v1.3.12; the assets remain on that release for anyone who needs them.
- Do not describe this app as cross-platform anywhere, and do not restore `~/.config/PDFGuide/config.json` as a documented path.
- Reviving Linux means a new implementation, not a revert: it would need a non-Win32 viewer, drop target, shell integration and packaging path.

## Version lockstep

One version number, bumped across every one of these in a single commit, always greater than the highest existing tag:

`PDFBossCpp/app/Version.h` · `PDFBossCpp/CMakeLists.txt` (`project(... VERSION ...)`) · `PDFBossCpp/installer-cpp.iss` · `app.py` (`APP_VERSION`) · `installer.iss`

`release.ps1 <version>` does all of it. The lockstep includes the deprecated files on purpose, so a resurrected file never reports a version that never shipped.

## Building

MuPDF is **not** vendored and **not** a vcpkg package. It lives beside the repo at `C:\source\mupdf` and is consumed by absolute path — the same convention TacPlot uses for WireCodecs — because it is 190 MB of third-party source and this repo is public. `PDFBossCpp/cmake/MuPdf.cmake` pins **1.28.2** and warns at configure time if the tree disagrees.

Full prerequisites and the msbuild line are in [README.md](README.md). Two things about that build are easy to get wrong:

- MuPDF's projects pin PlatformToolset `v142`; VS 18 (2026) provides **`v145`**. Retarget on the command line, never by editing the pinned tree.
- `third_party/mupdf-static-runtime.props` forces `/MT` and switches off OCR, barcodes, the non-PDF document handlers, and the CJK/SIL fallback fonts. Without it the exe is ~40 MB instead of ~22 MB and mixes two C runtimes. The feature defines are visible in MuPDF's *public* headers, so `MuPdf.cmake` passes the identical list to app code — if the two drift, the app and the library disagree about which functions exist.

Everything else comes from vcpkg in **classic** mode. Do not add a `vcpkg.json`: it switches the toolchain to manifest mode and rebuilds wxWidgets from source for no gain.

## This tree is also an upstream: DocBoss compiles parts of it

**DocBoss** (`C:\source\DocBoss`) compiles `PdfDocument`, `TocGen`, `Metadata`, `ViewerPane`, `TopicsPane` and `PathUtf8` straight out of `PDFBossCpp/app/`, and includes `PDFBossCpp/cmake/MuPdf.cmake`, by absolute path, pinned by `PDFBOSS_EXPECTED_COMMIT` in its `cmake/Siblings.cmake`. `DocBoss/CLAUDE.md` holds the list. Two consequences here:

- **A new `#include` in one of those units breaks DocBoss at link time, not here.** DocBoss lists the `.cpp` files it compiles; a pulled unit that starts depending on a sibling it does not list leaves undefined symbols. Today all of them are leaves apart from each other and `PathUtf8`, and none touches `Config`, `Updater` or `Version.h` -- keep it that way, because each of those carries PDFBoss's own identity.
- **They share one binary with MD Boss's units**, which is safe only because the two apps' code lives in different namespaces (`pdfboss::` and `mdboss::`) with different include guards. Both have a `PathUtf8`; both are compiled. Do not move anything out of `pdfboss::`.

## No names are compiled in: the topic noise list is local

*Since v2.5.0 (5 Oct 2026).* `TocGen`'s author/organisation filter used to be a compiled-in list carrying the author's own name and organisation abbreviations, in this public repo and in every DocBoss binary that pulls `TocGen`. It is now **empty by default**. The apps load it at startup from `toc-noise.txt` in their profile folder (`set_author_noise(read_noise_words(...))`; `kNoiseWordsFile`): PDFBoss beside its `config.json`, and DocBoss in its own profile. The tests supply the fixture's author word themselves in `generate()`, because the Python-oracle goldens assumed it was filtered. The deprecated `tocgen.py` had the same literal and now matches nothing. **The old words remain in this repo's git history**; that was accepted rather than rewriting public history. Never put a person's or organisation's name back into source, tests or fixtures.

## Nothing that touches the filesystem in bulk runs on the UI thread

`ViewerPane::start_search` set the shape and everything else now follows it: a worker thread, an atomic generation counter the worker polls so a superseded result is discarded rather than applied out of order, an `alive_` flag the destructor clears so a completion lambda can tell the pane is gone, and results applied only through `CallAfter`. The whole worker body is wrapped in `try/catch`, because an uncaught exception on a detached thread is a silent `std::terminate` — exit code `0xC0000409`, no dialog.

What moved, and why it had to:

- **`PdfListPane::rescan()`.** Not just a directory listing: per PDF it stats for a topics file and reads and JSON-parses the sidecar bookmarks file. It ran in the `MainFrame` constructor, *before the first paint*. Because the tree is no longer populated when `rescan()` returns, anything that wants to select a row after a rescan uses `select_pdf_when_ready()` — `select_pdf()` alone silently does nothing, there being no rows yet.
- **`write_toc()` on every PDF without a topics file**, from Refresh and from the drop handler. It reads text from every page, so a handful of large documents froze the window for minutes behind a `wxBusyCursor`. Both callers now go through `run_progress_job()` (`app/ProgressJob.h`), which owns the worker and a cancellable `wxPD_APP_MODAL` progress dialog.

Two rules that fall out of this:

- **A worker may not put up a dialog.** The drop handler therefore answers every "replace this file?" question on the UI thread first, building a worklist, and only then hands the copying and indexing to the worker. Interleaving the two would reintroduce exactly the freeze being fixed.
- **One `PdfDocument` owns one `fz_context` and is not shared across threads.** A worker opens its own document; `fz_new_context(nullptr, nullptr, ...)` makes a fully independent context, so this is safe, and reopening costs microseconds against the work being done.

`wxPD_APP_MODAL` is load-bearing rather than cosmetic: it is what stops a second job, a folder change or a close being started on top of a running one, which is what guarantees the parent window outlives the job.

## Traps that have already cost time here

- **`wxTreeCtrl::SetItemData` takes ownership and deletes the pointer.** Never cast a row index into it. Index 0 becomes `nullptr` and deletes harmlessly, so a one-row list works and a two-row list crashes with an access violation, presenting as a startup crash nowhere near the tree code. Use a real `wxTreeItemData` subclass.
- **`last_pages` in `config.json` uses JSON key insertion order as an LRU.** `Config` is written against `nlohmann::ordered_json` for exactly this reason; plain `nlohmann::json` sorts keys and would silently evict the wrong entries with no error.
- **Never drop a config key you do not understand.** Both apps read the same `%APPDATA%\PDFGuide\config.json` (the folder keeps the app's pre-rename name — do not "fix" it), so every write is a read-modify-write.
- **Non-ASCII in a narrow string literal renders as mojibake.** `tests/test_source_literals.cpp` fails the build on it; write `L"…"` or `wxString::FromUTF8(...)`. It has already caught two real occurrences.
- **All MuPDF calls stay in `PdfDocument.cpp`.** `fz_try`/`fz_catch` are `setjmp` macros: they trip C4611 (an error under `/W4 /WX`) in whatever translation unit expands them, and `longjmp` does not run destructors. The CMake dependency is `PRIVATE` to enforce this at build time.
- **A running instance locks the exe** and the link fails with `LNK1104`, which reads as "my change did nothing". `release.ps1` checks and asks you to close it rather than killing it.
- **`Set-Location` does not move .NET's working directory**, and `release.ps1` sets `[Environment]::CurrentDirectory` as well for that reason. `Bump()` pairs `Test-Path` (PowerShell — finds the file) with `[IO.File]::ReadAllText` (.NET — resolves the same relative path against wherever the shell *process* started), so running the script from a shell opened anywhere but the repo root killed it on the first version bump, with a `DirectoryNotFoundException` naming a path half from this repo and half from the start directory. Native tools invoked from here (`ISCC`) inherit the process directory too.
- **A wxFrame does not inherit the exe's icon.** `PDFBoss.rc` gives the executable one, so Explorer, the shortcut and the uninstall entry all looked right while the window itself wore the generic default — which is why this went unnoticed. `MainFrame` loads it as a *resource*, `icon.LoadFile("#1", wxBITMAP_TYPE_ICO_RESOURCE)`, `"#1"` being the ordinal the `.rc` assigns. Loading it as a file instead needs an ICO image handler registered and, without one, pops "No image handler for type 3 defined" at every launch.
- **Driving this app from a script needs `SetProcessDPIAware()` first.** A DPI-unaware harness is handed virtualised coordinates: `GetWindowRect` answers in logical pixels while `CopyFromScreen` and `SetCursorPos` work in physical ones, so captures come out offset and scaled and clicks land on the wrong control. A click aimed at Refresh opened the Folders dialog instead.

## Testing

`ctest --test-dir PDFBossCpp/build -C Release`. The suite is headless — `pdfboss_core` deliberately excludes the GUI so tests neither link nor initialise wxWidgets.

The `tests/fixture*.pdf` files are **synthetic**, generated by `tests/make_fixture.py`, and committed so the build is hermetic. They exist so no test ever depends on a controlled document. `tests/tocgen_oracle.py` regenerates the golden values in `test_tocgen.cpp` by running the Python `tocgen.py` — pin `pymupdf==1.28.2` when doing so, since that is the release wrapping the MuPDF the C++ side links, and comparing across engine versions proves nothing.
