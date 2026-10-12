# PDFBoss

[![GitHub repo](https://img.shields.io/badge/GitHub-Flinterpop%2FPDFBoss-181717?logo=github)](https://github.com/Flinterpop/PDFBoss)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)
![Language](https://img.shields.io/badge/C%2B%2B-20-00599C?logo=cplusplus&logoColor=white)
![GUI](https://img.shields.io/badge/GUI-wxWidgets-2A5DB0)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)

*Last updated: 25 Sep 2026*

A small desktop app that lets you browse PDFs by topic. It was called **PDF Sherpa** until v2.4.0; an installed PDF Sherpa updates itself onto PDFBoss.

- **Left pane** — lists every PDF under your top-level folders (up to five, managed with **Folders…**; a `./pdfs` subfolder by default). Each folder is its own heading, with subfolders nested beneath it.
- **Middle pane** — when you select a PDF, shows the topics and page numbers from its companion metadata file (same base name, `.toc` or `.json`), with your own bookmarks in a resizable list above them.
- **Right pane** — an embedded viewer. Click a topic and the PDF jumps to that page.
- **Search everywhere** — filter the PDF list by name, filter the topic list, and full-text search inside the open PDF with highlighted matches.
- **Highlight text** — drag-select words on the page and save them as real PDF highlight annotations, into the original or an `(ann)` copy.
- **Bookmarks** — mark pages with your own names (`Ctrl+B`); they show in their own list above the Topics and are saved beside the PDF in `name.bookmarks.json`. PDFs that have bookmarks are shown in **blue** in the list, so you can spot them at a glance.
- **Drag & drop** — drop a PDF anywhere on the window to file it into an `inbox` subfolder with an auto-generated topics file.
- **Nothing slow happens on the UI thread** — the folder scan, the topic-list builds and the drop handling all run on workers. The list is built in the background so the window is usable immediately, and building topic lists (which reads text from every page) runs behind a progress dialog you can cancel.

## Implementation

**`PDFBossCpp/` is the app.** It is C++20 on wxWidgets and MuPDF, and it is what ships as `PDFBoss-Setup.exe` and `PDFBoss-Portable.zip`.

The original Python/Tkinter implementation (`app.py`, `tocgen.py`) is **deprecated** and kept in-tree only as a historical reference. It is not built, packaged, or shipped, and its build path is guarded off — see [CLAUDE.md](CLAUDE.md). A difference between the two is not automatically a bug in the C++ app.

Both read and write the same `%APPDATA%\PDFGuide\config.json`, and the same `.toc` / `.bookmarks.json` sidecars, so a profile written by either still works in the other.

## Screenshot
<img width="1092" height="985" alt="PDFBoss" src="https://github.com/user-attachments/assets/649ccef3-3849-4581-a10c-01aa30045e4d" />



## Run

```
PDFBoss.exe            # last chosen folder (or the ./pdfs subfolder)
PDFBoss.exe C:\docs    # or point it at any folder
```

Use the **Folders…** button to manage up to five top-level folders. Each appears as its own heading in the PDF list with its subfolders nested beneath it, and the set is remembered and reopened on the next launch. A folder given on the command line is used for that session only and does not disturb the saved list.

## Installing (Windows)

Grab either flavor from the
[latest release](https://github.com/Flinterpop/PDFBoss/releases/latest):

- **`PDFBoss-Setup.exe`** — a machine-wide installer (asks for administrator
  approval): it installs the app into `Program Files\PDFBoss` for all users,
  adds Start-Menu (and optional desktop) shortcuts, and registers an
  uninstaller. There is no per-user install. Releases up to v2.5.0 installed
  per-user, in `%LOCALAPPDATA%\Programs`; that copy is removed when the new
  one is installed — after asking, or automatically during an update.
- **`PDFBoss-Portable.zip`** — no install at all: unzip `PDFBoss.exe`
  anywhere (a folder, a USB stick) and run it. Settings still live in
  `%APPDATA%\PDFGuide\config.json`.

Point the app at your own PDF folders with **Folders…** after installing.

The app **checks for updates at launch** (quietly, in the background) and
offers to download and apply a newer release in place — the installed copy
re-runs the installer silently (after a UAC prompt; decline it and nothing
changes), the portable copy swaps its own exe — or to
skip that version (`"skip_version"` in the config file). A manual **Check for
updates** button lives at the bottom of the Help window. To disable the launch
check, add `"check_updates": false` to `%APPDATA%\PDFGuide\config.json`.

## Standalone build & installer (Windows)

To cut a full release in one step (bump versions, build everything, commit,
push, publish the GitHub release, reinstall locally):

```
.\release.ps1 1.3.8                     # notes auto-generated from commits
.\release.ps1 1.3.8 -NotesFile notes.md # or hand-written notes
```

Or do the steps by hand — build the exe, then compile the installer and zip the portable variant:

```
cmake --preset windows-static                  # in PDFBossCpp\
cmake --build build --config Release           # -> build\app\Release\PDFBoss.exe
ctest --test-dir build -C Release              # the whole suite must be green
iscc PDFBossCpp\installer-cpp.iss            # -> installer\PDFBoss-Setup.exe
powershell Compress-Archive -Force PDFBossCpp\build\app\Release\PDFBoss.exe installer\PDFBoss-Portable.zip
```

The compiled artifacts are not committed to the repo — publish them as GitHub Release assets (both names are what the in-app updater looks for, so keep them exact):

```
gh release create v<version> installer\PDFBoss-Setup.exe installer\PDFBoss-Portable.zip
```

### Build prerequisites

- **Visual Studio 18 (2026)** with the C++ toolset (`v145`), and CMake 3.21+.
- **vcpkg** at `C:\vcpkg`, classic mode, triplet `x64-windows-static`:

  ```
  C:\vcpkg\vcpkg.exe install wxwidgets:x64-windows-static md4c:x64-windows-static catch2:x64-windows-static nlohmann-json:x64-windows-static
  ```

- **MuPDF 1.28.2**, extracted to `C:\source\mupdf` (override with `-DMUPDF_DIR=...`). It is not a vcpkg package and is deliberately not vendored into this repo — it is 190 MB of third-party source. Download `mupdf-1.28.2-source.tar.gz` from the [MuPDF archive](https://mupdf.com/downloads/archive/) and build it with:

  ```
  msbuild platform/win32/mupdf.sln -t:libmupdf -p:Configuration=Release -p:Platform=x64 -p:PlatformToolset=v145 -p:ForceImportBeforeCppTargets=<repo>\PDFBossCpp\third_party\mupdf-static-runtime.props
  ```

  That props file is what forces MuPDF onto the static CRT (`/MT`) and switches off the features PDFBoss never uses — OCR, barcodes, the non-PDF document handlers, and the CJK fallback fonts. Without it the executable is roughly 40 MB instead of 22 MB, and mixes two C runtimes. See `PDFBossCpp/cmake/MuPdf.cmake` for the details.

## Metadata files

Each PDF gets a companion file with the **same base name** and a different
extension. `.toc` is checked first, then `.json`.

`mybook.pdf` → `mybook.toc`:

```
# comments start with '#'
Introduction: 1
Chapter 1 - Setup: 5
Advanced Topics: 42
```

The page is the trailing number, so topics may contain colons and dashes
(`Chapter 1 - Setup: 5` works fine).

Or `mybook.json`:

```json
[
  {"topic": "Introduction", "page": 1},
  {"topic": "Chapter 1 - Setup", "page": 5}
]
```

Page numbers are **1-based** (page 1 = the first page).

## Notes

- PDFs are grouped by subfolder under each top-level folder, nested to any depth. Subfolders **start collapsed**, and the app **remembers which folders you left open/closed** for next time, per top-level folder. (While a search filter is active, folders open automatically to reveal matches.)
- Right-click any folder and tick **Show as flat list** to collapse its structure: it then lists every PDF beneath it at any depth, with no subfolder rows, and is marked `(flat)`. Works on any folder, not just top-level ones, and each remembers its own setting.
- **Search boxes** in every pane (`✕` clears): the PDF list filters by
  filename, the Topics pane filters the topic list, and the viewer's box
  searches the **text of the open PDF** — matches highlight in yellow with a
  live `2 / 17` counter, `Enter`/`F3` steps forward, `Shift+Enter`/`Shift+F3`
  back, `Ctrl+F` focuses the box. The scan runs in background chunks, so even
  a 9000-page document stays responsive while it searches.
- **Highlight text**: drag across text in the viewer (words shade blue), then
  click **Highlight** or right-click → *Highlight selection*. **Save**
  (`Ctrl+S`) offers an annotated copy — `manual.pdf` → `manual(ann).pdf`, with
  the topics and bookmarks files copied along — or writes into the original
  file. Right-click
  an existing highlight to remove it. Highlights are standard PDF annotations,
  visible in any viewer.
- **Bookmarks**: press `Ctrl+B` (or the **🔖** button, or right-click the page)
  to bookmark the current page under a name of your choice. Bookmarks get
  their own list above the Topics (shown only when the PDF has bookmarks; it
  starts at a quarter of the pane and the divider is draggable, with the
  position remembered across runs) — click to jump, right-click to rename or
  delete. They live in a `name.bookmarks.json` sidecar next to the PDF, so
  they travel with the folder and can be hand-edited; an `(ann)` copy gets the
  sidecar copied along too. PDFs that have bookmarks show their name in **blue**
  in the PDF list (the colour updates live as you add the first bookmark or
  delete the last one).
- **PDFs / Topics and Bookmarks** toolbar toggles collapse the left and middle
  panes — hide both for a full-width reading view. The choice persists across
  runs.
- **Full page** fits the whole page and resizes the window to hug it; dragging
  a window edge or corner then zooms the page to follow. Selecting a different
  PDF never moves the window.
- The **title bar** shows the app version and the current folder; the **Help**
  button opens the rendered user guide, and hovering the less obvious buttons
  shows a tooltip.
- **Right-click** a PDF for **Open PDF** / **Reveal in Explorer**; right-click a folder to open it in Explorer.
- The app remembers the **last page you were on in each PDF** and returns there
  when you reopen it (positions are kept for the 200 most recently viewed
  PDFs, so the settings file never grows without bound).
- A PDF without a metadata file is still listed (marked `(no metadata)`) and viewable — you just won't get the topic list. **Refresh** (button or `F5`) offers to **auto-build topic lists** for any PDFs that don't have one, using each PDF's built-in outline bookmarks — or its text headings when there are none. (These built-in bookmarks are separate from your own `Ctrl+B` bookmarks.) Building a list reads text from **every page**, so a batch of large documents runs behind a progress dialog naming the current file, with **Cancel**; the lists already built are kept, and pressing Refresh again resumes, since only PDFs still lacking one are offered.
- **Drop PDFs onto the window** (Windows only — from Explorer, Outlook attachments saved to disk, etc.) and they are **copied into an `inbox` subfolder** of the current folder, a **`.toc` topics file is auto-generated** for each (bookmarks first, text headings as a fallback), and the last one dropped is selected and opened. Dropping a PDF whose name is already in the inbox asks before replacing it, and an existing (hand-edited) `.toc` is never overwritten. Every replace question is asked first, before any copying starts; the copying and indexing then run together behind a cancellable progress dialog.
- Viewer controls: **Prev/Next**, **+/−** zoom, **Fit width**, **Full page**,
  and mouse-wheel scrolling. Your **Fit width / Full page** choice is remembered
  and re-applied to every PDF you open — and it persists across app runs
  (saved to the config file). Manual **+/−** zoom is a temporary
  override that doesn't change the saved preference.
- The app also remembers your **window size/position**, the **folder you
  chose**, the **bookmarks divider position**, and **re-opens the last PDF**
  you were viewing on the next launch (same config file).
- The mouse wheel scrolls within the page and **flips to the next/previous page
  when you scroll past the bottom/top edge**.

## Settings file

Everything the app remembers lives in one JSON file — `%APPDATA%\PDFGuide\config.json` (the folder keeps the app's former name so settings saved before the rename survive). It is safe to edit while the app is closed, or to delete for a fresh start.

| Key | Meaning |
|-----|---------|
| `roots` | Top-level folders shown in the PDF list, as `{"name", "path"}` entries, in display order |
| `folder` | The first entry of `roots`, kept in step so older versions still open something |
| `geometry` | Window size and position |
| `last_pdf` | PDF re-opened on the next launch |
| `last_pages` | Last-viewed page per PDF (200 most recent) |
| `favorites` | Pinned PDFs above the search box (10 max, stored relative to whichever top-level folder contains them, so they survive that folder being moved or renamed) |
| `expanded_folders` | Subfolders left open in the PDF list |
| `fit_pref` | Viewer fit preference: `"width"` or `"page"` |
| `bm_sash` | Bookmarks/Topics divider position |
| `show_pdf_list`, `show_topics` | Pane visibility toggles |
| `check_updates` | `false` disables the launch update check |
| `skip_version` | Release the update prompt should not re-offer |

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `←` / `Page Up` | Previous page |
| `→` / `Page Down` / `Space` | Next page |
| `Home` / `End` | First / last page |
| `+` / `=` / `−` | Zoom in / out |
| `W` | Fit width |
| `P` | Full page |
| `Ctrl+B` | Bookmark the current page |
| `Ctrl+F` | Focus the content search box |
| `Ctrl+S` | Save highlights to the PDF |
| `F3` / `Shift+F3` | Next / previous search match |
| `Enter` / `Shift+Enter` (in search box) | Next / previous search match |
| `F5` | Refresh PDF list |

Arrow, space, and page keys defer to the PDF/topic lists while one of them has
keyboard focus, so you can still navigate the lists with the keyboard.

## License

Released under the [GNU Affero General Public License v3.0 or later](LICENSE).

**Why AGPL and not MIT.** PDFBoss renders, searches and annotates PDFs with [MuPDF](https://mupdf.com/) (Artifex Software), which is licensed AGPL-3.0-or-later. The shipped executable links MuPDF statically, so the combined work is conveyed under the AGPL and this repository is licensed to match. Earlier releases carried an MIT notice while already bundling MuPDF through PyMuPDF; the licence was corrected when the C++ port made that linkage explicit. Section 13 (the network clause) has no practical effect here — PDFBoss is a desktop application and offers no service over a network — so in practice the obligation is simply that the corresponding source stays published, which it is, in this repository.

If you need PDFBoss under different terms, MuPDF is also available under a commercial licence from Artifex.

### Third-party components

| Component | Licence | Used for |
|---|---|---|
| [MuPDF](https://mupdf.com/) | AGPL-3.0-or-later | Rendering, text extraction, search, highlight annotations |
| [wxWidgets](https://www.wxwidgets.org/) | LGPL-2.0-or-later **with the wxWindows exception** | The user interface |
| [md4c](https://github.com/mity/md4c) | MIT | Rendering this help document in-app |
| [nlohmann/json](https://github.com/nlohmann/json) | MIT | Settings and sidecar files |
| [Catch2](https://github.com/catchorg/Catch2) | BSL-1.0 | Tests only; not shipped |

Notes:

- The wxWindows exception is what permits static linking of wxWidgets without imposing further conditions on the combined work.
- Catch2 is a build-time dependency of the test target and is not part of any released binary.
