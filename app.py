"""
PDF Sherpa
==========

A small desktop app that:

1. Lists every PDF found in a folder (the last chosen folder, or ./pdfs).
2. When a PDF is selected, loads its companion metadata file -- same base
   filename, different extension (.toc or .json) -- and shows the list of
   topics with their page numbers.
3. Clicking a topic/page opens the PDF (embedded viewer) at that page.
4. Drop a PDF anywhere on the window to copy it into an 'inbox' subfolder
   of the current folder and auto-generate its .toc topics file.
5. Bookmark pages (Ctrl+B) with your own names; bookmarks show in their own
   list above the Topics and are saved next to the PDF (name.bookmarks.json).
   PDFs that have bookmarks are shown in blue in the PDF list.
6. Checks GitHub for a newer release at launch and can update itself in
   place -- the installed copy re-runs the installer, a portable copy swaps
   its own exe (opt out with "check_updates": false in config.json).

Metadata file formats (auto-detected by extension, .toc preferred):

  book.toc  (simple text, one entry per line -- easiest to hand-edit)
  ---------------------------------------------------------------
      # lines starting with '#' are comments
      Introduction: 1
      Chapter 1 - Setup: 5
      Advanced Topics: 42

  book.json (structured)
  ---------------------------------------------------------------
      [
        {"topic": "Introduction", "page": 1},
        {"topic": "Chapter 1 - Setup", "page": 5}
      ]

Requirements:  pip install pymupdf pillow
Run:           python app.py  [optional_folder]
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import webbrowser
import zipfile
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

import tocgen

# ---- Optional heavy deps: fail with a friendly message rather than a crash ---
try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None

try:
    from PIL import Image, ImageTk
except ImportError:  # pragma: no cover
    Image = None
    ImageTk = None


# Shown in the window title; keep in sync with AppVersion in installer.iss.
APP_VERSION = "2.5.0"

# Where the launch-time update check looks for new releases.
UPDATE_API_URL = ("https://api.github.com/repos/"
                  "Flinterpop/PDF_Sherpa/releases/latest")
UPDATE_ASSET_NAME = "PDFSherpa-Setup.exe"
# The no-install variant: a zip holding just PDFSherpa.exe.  A portable copy
# updates itself from this asset instead of running the installer.
UPDATE_PORTABLE_ASSET_NAME = "PDFSherpa-Portable.zip"
# The Linux build.  A running AppImage self-updates from this asset by
# replacing its own file in place (see MainWindow._swap_appimage_and_exit).
# The name is version-less so the release "latest" always carries this exact
# asset (and its .zsync) for the updater to match.
UPDATE_APPIMAGE_ASSET_NAME = "PDFSherpa-x86_64.AppImage"

# Metadata extensions we look for, in order of preference.
METADATA_EXTENSIONS = (".toc", ".json")

# Zoom limits for the embedded viewer.
MIN_ZOOM = 0.25
MAX_ZOOM = 4.0
ZOOM_STEP = 1.25

# How many PDFs' last-viewed page to remember (oldest entries are pruned
# so config.json can't grow without bound).
MAX_REMEMBERED_PAGES = 200

# How many favorite PDFs to pin above the PDF search box (newest first;
# the oldest is dropped once this many are pinned).
MAX_FAVORITES = 10

# Label for the "show this file in the OS file manager" action -- the file
# manager differs per platform, so name it accordingly.
if sys.platform == "win32":
    REVEAL_LABEL = "Reveal in Explorer"
elif sys.platform == "darwin":
    REVEAL_LABEL = "Reveal in Finder"
else:
    REVEAL_LABEL = "Show in file manager"


def _add_tooltip(widget, text: str, delay_ms: int = 500) -> None:
    """Attach a hover tooltip to a widget (Tk has none built in).  Works on
    disabled widgets too -- they still receive Enter/Leave events."""
    state = {"after": None, "tip": None}

    def show():
        state["after"] = None
        tip = tk.Toplevel(widget)
        tip.wm_overrideredirect(True)     # no title bar / border
        tip.wm_geometry(f"+{widget.winfo_rootx() + 10}"
                        f"+{widget.winfo_rooty() + widget.winfo_height() + 4}")
        tk.Label(tip, text=text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, padx=6, pady=4).pack()
        state["tip"] = tip

    def enter(_event):
        state["after"] = widget.after(delay_ms, show)

    def leave(_event):
        if state["after"] is not None:
            widget.after_cancel(state["after"])
            state["after"] = None
        if state["tip"] is not None:
            state["tip"].destroy()
            state["tip"] = None

    widget.bind("<Enter>", enter, add="+")
    widget.bind("<Leave>", leave, add="+")
    widget.bind("<ButtonPress>", leave, add="+")


# ----------------------------------------------------------------------------
# Metadata loading
# ----------------------------------------------------------------------------
def find_metadata_path(pdf_path: str) -> str | None:
    """Return the companion metadata file for a PDF, or None if none exists."""
    base, _ = os.path.splitext(pdf_path)
    for ext in METADATA_EXTENSIONS:
        candidate = base + ext
        if os.path.isfile(candidate):
            return candidate
    return None


def _parse_toc_text(text: str) -> list[tuple[str, int]]:
    """Parse the simple '.toc' text format into (topic, page) tuples.

    Each non-comment, non-blank line must end with a page number.  The page is
    taken from the trailing integer (after the last ':' if present, otherwise
    after the last run of whitespace), so topics may themselves contain colons
    and spaces, e.g. "Chapter 1 - Setup: 5".
    """
    entries: list[tuple[str, int]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        if ":" in line:
            topic, _, page_part = line.rpartition(":")
        else:
            topic, _, page_part = line.rpartition(" ")

        topic = topic.strip()
        match = re.search(r"\d+", page_part)
        if not topic or not match:
            raise ValueError(
                f"Line {lineno}: expected 'Topic: page', got {raw!r}"
            )
        entries.append((topic, int(match.group())))
    return entries


def _parse_json_metadata(text: str) -> list[tuple[str, int]]:
    """Parse the '.json' metadata format into (topic, page) tuples."""
    data = json.loads(text)
    entries: list[tuple[str, int]] = []
    for item in data:
        topic = str(item["topic"]).strip()
        page = int(item["page"])
        entries.append((topic, page))
    return entries


def load_metadata(metadata_path: str) -> list[tuple[str, int]]:
    """Load a metadata file and return a list of (topic, page) tuples."""
    with open(metadata_path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if metadata_path.lower().endswith(".json"):
        return _parse_json_metadata(text)
    return _parse_toc_text(text)


# ----------------------------------------------------------------------------
# Bookmarks (per-PDF sidecar: name.bookmarks.json next to the PDF)
# ----------------------------------------------------------------------------
def bookmarks_path(pdf_path: str) -> str:
    """Return the bookmarks sidecar path for a PDF (manual.pdf ->
    manual.bookmarks.json).  The double extension keeps it clear of the
    .toc/.json metadata lookup in find_metadata_path."""
    return os.path.splitext(pdf_path)[0] + ".bookmarks.json"


def load_bookmarks(pdf_path: str) -> list[dict]:
    """Load a PDF's bookmarks: a list of {"page": int, "title": str} dicts,
    sorted by page.  Missing sidecar is the normal case -> []."""
    path = bookmarks_path(pdf_path)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        bookmarks = []
        for item in data:
            page = int(item["page"])
            title = str(item["title"]).strip()
            if page >= 1 and title:
                bookmarks.append({"page": page, "title": title})
        bookmarks.sort(key=lambda b: b["page"])
        return bookmarks
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Corrupt or unreadable sidecar; the next save overwrites it.
        messagebox.showwarning(
            "Bookmarks",
            f"Could not read {os.path.basename(path)}:\n\n{exc}\n\n"
            "Starting with no bookmarks for this PDF.")
        return []


def save_bookmarks(pdf_path: str, bookmarks: list[dict]) -> bool:
    """Write a PDF's bookmarks sidecar; an empty list deletes the file
    (no file = no bookmarks).  Returns False if the write failed."""
    path = bookmarks_path(pdf_path)
    try:
        if not bookmarks:
            if os.path.isfile(path):
                os.remove(path)
            return True
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(bookmarks, fh, indent=2)
        return True
    except OSError as exc:
        messagebox.showwarning(
            "Bookmarks",
            f"Could not write {path}:\n\n{exc}\n\n"
            "Bookmarks will be kept for this session only.")
        return False


# ----------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------
class PDFSherpaApp(ttk.Frame):
    def __init__(self, master: tk.Tk, folder: str):
        super().__init__(master, padding=6)
        self.master = master
        self.folder = folder

        # Viewer state
        self.doc = None                 # fitz.Document currently open
        self.current_pdf_path: str | None = None
        self.current_page = 0           # 0-based
        self.zoom = 1.0
        # Fit preference persists across PDFs and app runs; fit_mode is the
        # currently-applied fit (may be None after a manual +/- zoom).
        pref = load_config().get("fit_pref", "width")
        self.fit_pref = pref if pref in ("width", "page") else "width"
        self.fit_mode: str | None = self.fit_pref  # "width" | "page" | None
        self._photo = None              # keep a ref so Tk doesn't GC the image
        self.filter_text = ""           # current PDF-list search filter
        self.topic_filter_text = ""     # current topic-list search filter
        self._topic_entries: list[tuple[str, int]] = []  # current PDF's topics
        self._topic_placeholder: str | None = None  # shown instead of topics
        # Current PDF's bookmarks ({"page", "title"} dicts, page-sorted) and
        # the list index targeted by the tree's right-click menu.
        self._bookmarks: list[dict] = []
        self._bm_menu_index: int | None = None
        # Where the user last dragged the bookmarks/topics sash (pixels).
        # Persisted across runs; None means "use the default 25%".
        saved_sash = load_config().get("bm_sash")
        self._bm_sash: int | None = (
            saved_sash if isinstance(saved_sash, int) and saved_sash > 0
            else None)
        # Content search over the open PDF: (page_index, fitz.Rect) per hit.
        # The scan runs in chunks on the Tk event loop so big documents don't
        # freeze the UI; matches/label fill in progressively.
        self.content_matches: list[tuple[int, object]] = []
        self.content_match_idx = -1     # index into content_matches (-1 = none)
        self._content_search_after: str | None = None  # debounce timer id
        self._content_scan_after: str | None = None    # next-chunk timer id
        self._content_scan_active = False
        self._content_scan_needle = ""
        self._content_scan_page = 0     # next page the scan will read
        self._content_scan_jump = False  # jump to the first hit when found
        # Text selection / highlight annotations on the open PDF.
        self._select_anchor: tuple[float, float] | None = None  # canvas px
        self._select_drag: tuple[float, float] | None = None
        self._selected_rects: list = []  # word boxes (PDF points) selected
        self._annots_dirty = False       # highlights not yet saved to disk
        self._canvas_menu_pt: tuple[float, float] | None = None  # PDF points
        # Re-fit the page when a window resize settles (debounced).
        self._resize_after: str | None = None
        self._snap_after: str | None = None   # full-page window-snap pass
        # Full-page fit: which canvas dimension drives the zoom during a
        # user resize ("w"/"h"), and the canvas size of the last fit.
        self._fit_master: str | None = None
        self._fitted_canvas_size: tuple[int, int] | None = None
        self._saved_sashes: list[int] | None = None  # pane widths pre-snap
        self._last_pix: tuple[int, int] | None = None  # last rendered size
        self._last_canvas_size = (0, 0)
        # Remembered open folders (relative paths); folders start closed unless
        # listed here.  Persisted across runs.
        saved = load_config().get("expanded_folders", [])
        self._expanded = set(saved) if isinstance(saved, list) else set()
        # Favorite PDFs pinned above the search box (absolute paths, newest
        # first, capped at MAX_FAVORITES).  Persisted across runs.
        fav = load_config().get("favorites", [])
        # Canonicalise to the root-scoped stored form (migrates favorites
        # saved as absolute paths by older versions to root-relative) and
        # write the shallower form back so the saved file matches.
        raw_fav = ([p for p in fav if isinstance(p, str) and p.strip()]
                   if isinstance(fav, list) else [])
        self._favorites: list[str] = self._dedup_favorites(raw_fav)[:MAX_FAVORITES]
        if self._favorites != raw_fav[:MAX_FAVORITES]:
            update_config({"favorites": self._favorites})
        self._fav_placeholder = False   # True while the list shows the hint row
        self._fav_ctx_path: str | None = None   # favorites context-menu target
        # State owned by later flows, declared here so the object's full
        # state is visible in one place.
        self._pdf_by_iid: dict[str, str] = {}   # tree item id -> pdf path
        self._folder_rel_by_iid: dict[str, str] = {}  # folder iid -> rel path
        self._ctx_path: str | None = None       # PDF-list context-menu target
        self._help_win: tk.Toplevel | None = None
        self._dl_win: tk.Toplevel | None = None      # download progress window
        self._dl_portable = False    # downloading the portable zip, not setup
        self._update_thread: threading.Thread | None = None
        self._update_result: tuple | None = None     # set by the worker thread
        self._download_result: tuple | None = None   # set by the worker thread
        self._dl_appimage = False                    # AppImage self-update run
        self._appimage_target: str | None = None     # the .AppImage to replace
        # Win32 drag-drop plumbing (_enable_file_drop fills these in).
        self._drop_queue: list[str] = []
        self._wndproc_ref = None
        self._orig_wndproc = None

        self.pack(fill="both", expand=True)
        self._build_ui()
        self._bind_shortcuts()
        self._enable_file_drop()
        self.refresh_pdf_list()
        self._restore_session()
        # Check for a newer release once the window has settled.  Opt out
        # with "check_updates": false in config.json.  Auto-checks where the
        # app can install the update itself: Windows (installer/portable) and
        # a Linux AppImage (self-replace).  A plain source/macOS run can't, so
        # only its Help-window "Check for updates" button reaches the check.
        if sys.platform == "win32" or _running_appimage():
            self.after(2000, self._start_update_check)

    # -- Layout ---------------------------------------------------------------
    def _build_ui(self) -> None:
        """Assemble the toolbar and the three resizable panes
        (PDFs | topics | viewer), each built by its own helper."""
        self._build_toolbar()
        panes = self.panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True)
        panes.add(self._build_pdf_pane(panes), weight=1)
        panes.add(self._build_topics_pane(panes), weight=1)
        panes.add(self._build_viewer_pane(panes), weight=3)
        self._apply_pane_visibility(persist=False)   # restore saved toggles

    def _build_toolbar(self) -> None:
        toolbar = ttk.Frame(self)
        toolbar.pack(side="top", fill="x", pady=(0, 6))

        ttk.Button(toolbar, text="Choose folder…",
                   command=self.choose_folder).pack(side="left")
        refresh_btn = ttk.Button(toolbar, text="Refresh",
                                 command=self.on_refresh_clicked)
        refresh_btn.pack(side="left", padx=(4, 0))
        _add_tooltip(refresh_btn,
                     "Rescan the folder for PDFs (F5) and offer to build\n"
                     "topic lists for any PDFs that don't have one yet.")
        ttk.Button(toolbar, text="Help",
                   command=self.show_help).pack(side="right")

        # Pane visibility toggles (pressed = shown); with both off only the
        # viewer remains.  Persisted across runs.
        cfg = load_config()
        self.show_pdfs_var = tk.BooleanVar(
            value=bool(cfg.get("show_pdf_list", True)))
        self.show_topics_var = tk.BooleanVar(
            value=bool(cfg.get("show_topics", True)))
        pdfs_toggle = ttk.Checkbutton(toolbar, text="PDFs",
                                      style="Toolbutton",
                                      variable=self.show_pdfs_var,
                                      command=self._apply_pane_visibility)
        pdfs_toggle.pack(side="left", padx=(12, 0))
        _add_tooltip(pdfs_toggle,
                     "Show or hide the PDF list pane.\n"
                     "Hide both panes for a full-width reading view.")
        topics_toggle = ttk.Checkbutton(toolbar, text="Topics and Bookmarks",
                                        style="Toolbutton",
                                        variable=self.show_topics_var,
                                        command=self._apply_pane_visibility)
        topics_toggle.pack(side="left", padx=(2, 0))
        _add_tooltip(topics_toggle,
                     "Show or hide the Topics and Bookmarks pane.\n"
                     "Hide both panes for a full-width reading view.")

    def _build_pdf_pane(self, parent) -> ttk.Frame:
        """PDF list pane (grouped by subfolder under the top-level folder)."""
        left = ttk.Frame(parent)

        # Favorites: up to MAX_FAVORITES pinned PDFs, above the search box.
        # Right-click a PDF in the list below to add or remove one.  The "⋯"
        # button carries the list-level actions (clear / export / import),
        # which stay reachable even when the list is empty.
        fav_header = ttk.Frame(left)
        fav_header.pack(side="top", fill="x")
        ttk.Label(fav_header, text="Favorites").pack(side="left")
        fav_mb = ttk.Menubutton(fav_header, text="⋯", width=2)
        fav_mb.pack(side="right")
        fav_manage = tk.Menu(fav_mb, tearoff=0)
        fav_manage.add_command(label="Export favorites…",
                               command=self._export_favorites)
        fav_manage.add_command(label="Import favorites…",
                               command=self._import_favorites)
        fav_manage.add_separator()
        fav_manage.add_command(label="Clear all favorites",
                               command=self._clear_favorites)
        fav_mb.config(menu=fav_manage)
        _add_tooltip(fav_mb, "Clear, export, or import your favorites.")

        fav_wrap = ttk.Frame(left)
        fav_wrap.pack(side="top", fill="x", pady=(0, 6))
        self.fav_list = tk.Listbox(fav_wrap, height=1, activestyle="none",
                                   exportselection=False, borderwidth=1,
                                   highlightthickness=0)
        self.fav_list.pack(side="left", fill="x", expand=True)
        self.fav_list.bind("<<ListboxSelect>>", self._on_favorite_selected)
        self.fav_list.bind("<Button-3>", self._on_favorite_right_click)
        self.fav_menu = tk.Menu(self, tearoff=0)
        self.fav_menu.add_command(label="Open PDF", command=self._fav_ctx_open)
        self.fav_menu.add_command(label="Remove from favorites",
                                  command=self._fav_ctx_remove)
        self.fav_menu.add_command(label=REVEAL_LABEL,
                                  command=self._fav_ctx_reveal)

        ttk.Label(left, text="PDFs").pack(anchor="w")

        # Search / filter box
        search = ttk.Frame(left)
        search.pack(side="top", fill="x", pady=(0, 4))
        ttk.Label(search, text="🔍").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search_changed)
        search_entry = ttk.Entry(search, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(search, text="✕", width=2,
                   command=lambda: self.search_var.set("")).pack(side="left")

        tree_wrap = ttk.Frame(left)
        tree_wrap.pack(side="top", fill="both", expand=True)
        self.pdf_tree = ttk.Treeview(tree_wrap, show="tree",
                                     selectmode="browse")
        self.pdf_tree.column("#0", width=240, anchor="w")
        self.pdf_tree.tag_configure("folder", foreground="#333")
        self.pdf_tree.tag_configure("nometa", foreground="#999")
        # PDFs with saved bookmarks stand out in blue (see _pdf_row_tags).
        self.pdf_tree.tag_configure("bookmarked", foreground="#1a5fb4")
        self.pdf_tree.pack(side="left", fill="both", expand=True)
        pdf_sb = ttk.Scrollbar(tree_wrap, orient="vertical",
                               command=self.pdf_tree.yview)
        pdf_sb.pack(side="right", fill="y")
        self.pdf_tree.config(yscrollcommand=pdf_sb.set)
        self.pdf_tree.bind("<<TreeviewSelect>>", self.on_pdf_selected)
        self.pdf_tree.bind("<Button-3>", self._on_pdf_right_click)
        self.pdf_tree.bind("<<TreeviewOpen>>",
                           lambda e: self._on_folder_toggle(True))
        self.pdf_tree.bind("<<TreeviewClose>>",
                           lambda e: self._on_folder_toggle(False))

        # Right-click context menu for the PDF list
        self.pdf_menu = tk.Menu(self, tearoff=0)
        self.pdf_menu.add_command(label="Open in default viewer",
                                  command=self._ctx_open)
        self.pdf_menu.add_command(label=REVEAL_LABEL,
                                  command=self._ctx_reveal)
        self.pdf_menu.add_separator()
        self.pdf_menu.add_command(label="Add to favorites",
                                  command=self._ctx_toggle_favorite)
        self._left_pane = left
        self._refresh_favorites_list()
        return left

    def _build_topics_pane(self, parent) -> ttk.Frame:
        """Topics pane; the bookmarks list rides above it behind a sash."""
        mid = ttk.Frame(parent)
        # Vertical split: Bookmarks over Topics, with a draggable sash.  The
        # Bookmarks pane is only attached while the PDF has bookmarks (it
        # starts at 25% of the height); otherwise Topics takes it all.
        self._mid_split = ttk.PanedWindow(mid, orient="vertical")
        self._mid_split.pack(fill="both", expand=True)
        self._build_bookmarks_ui()

        topics = ttk.Frame(self._mid_split)
        self._mid_split.add(topics, weight=3)
        ttk.Label(topics, text="Topics").pack(anchor="w")

        # Search / filter box (filters the current PDF's topics only)
        topic_search = ttk.Frame(topics)
        topic_search.pack(side="top", fill="x", pady=(0, 4))
        ttk.Label(topic_search, text="🔍").pack(side="left")
        self.topic_search_var = tk.StringVar()
        self.topic_search_var.trace_add("write", self._on_topic_search_changed)
        topic_entry = ttk.Entry(topic_search,
                                textvariable=self.topic_search_var)
        topic_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
        ttk.Button(topic_search, text="✕", width=2,
                   command=lambda: self.topic_search_var.set("")
                   ).pack(side="left")

        topic_wrap = ttk.Frame(topics)
        topic_wrap.pack(side="top", fill="both", expand=True)
        self.topic_tree = ttk.Treeview(topic_wrap, columns=("page",),
                                       show="tree headings",
                                       selectmode="browse")
        self.topic_tree.heading("#0", text="Topic")
        self.topic_tree.heading("page", text="Page")
        self.topic_tree.column("#0", width=200, anchor="w")
        self.topic_tree.column("page", width=50, anchor="center", stretch=False)
        self.topic_tree.pack(side="left", fill="both", expand=True)
        topic_sb = ttk.Scrollbar(topic_wrap, orient="vertical",
                                 command=self.topic_tree.yview)
        topic_sb.pack(side="right", fill="y")
        self.topic_tree.config(yscrollcommand=topic_sb.set)
        self.topic_tree.bind("<<TreeviewSelect>>", self.on_topic_selected)

        self._mid_pane = mid
        return mid

    def _build_bookmarks_ui(self) -> None:
        """Bookmark list widgets (shown above Topics while a PDF has any)."""
        self._bm_frame = ttk.Frame(self._mid_split)
        # Not added to the split here -- _populate_bookmark_tree attaches and
        # detaches it as bookmarks come and go.
        ttk.Label(self._bm_frame, text="Bookmarks").pack(anchor="w")
        bm_wrap = ttk.Frame(self._bm_frame)
        bm_wrap.pack(side="top", fill="both", expand=True)
        self.bm_tree = ttk.Treeview(bm_wrap, columns=("page",),
                                    show="tree headings",
                                    selectmode="browse", height=3)
        self.bm_tree.heading("#0", text="Bookmark")
        self.bm_tree.heading("page", text="Page")
        self.bm_tree.column("#0", width=200, anchor="w")
        self.bm_tree.column("page", width=50, anchor="center", stretch=False)
        self.bm_tree.pack(side="left", fill="both", expand=True)
        bm_sb = ttk.Scrollbar(bm_wrap, orient="vertical",
                              command=self.bm_tree.yview)
        bm_sb.pack(side="right", fill="y")
        self.bm_tree.config(yscrollcommand=bm_sb.set)
        self.bm_tree.bind("<<TreeviewSelect>>", self.on_bookmark_selected)
        self.bm_tree.bind("<Button-3>", self._on_bm_right_click)
        # Right-click context menu for rows in the bookmark list
        self.bookmark_menu = tk.Menu(self, tearoff=0)
        self.bookmark_menu.add_command(label="Rename",
                                       command=self._rename_bookmark)
        self.bookmark_menu.add_command(label="Delete",
                                       command=self._delete_bookmark)

    def _build_viewer_pane(self, parent) -> ttk.Frame:
        """Embedded viewer: find bar, nav bar, and the page canvas."""
        right = ttk.Frame(parent)
        self._build_content_search_bar(right)
        self._build_viewer_nav_bar(right)
        self._build_viewer_canvas(right)
        return right

    def _build_content_search_bar(self, parent) -> None:
        """Find box that searches the text of the currently open PDF."""
        content_search = ttk.Frame(parent)
        content_search.pack(side="top", fill="x", pady=(0, 4))
        ttk.Label(content_search, text="🔍").pack(side="left")
        self.content_search_var = tk.StringVar()
        self.content_search_var.trace_add("write",
                                          self._on_content_search_changed)
        self.content_search_entry = ttk.Entry(
            content_search, textvariable=self.content_search_var)
        self.content_search_entry.pack(side="left", fill="x", expand=True,
                                       padx=(4, 0))
        self.content_search_entry.bind("<Return>", self._on_content_return)
        self.content_search_entry.bind("<Shift-Return>",
                                       lambda e: self.prev_match() or "break")
        prev_btn = ttk.Button(content_search, text="▲", width=2,
                              command=self.prev_match)
        prev_btn.pack(side="left", padx=(4, 0))
        _add_tooltip(prev_btn, "Previous search match (Shift+F3)")
        next_btn = ttk.Button(content_search, text="▼", width=2,
                              command=self.next_match)
        next_btn.pack(side="left")
        _add_tooltip(next_btn, "Next search match (F3)")
        self.match_var = tk.StringVar()
        ttk.Label(content_search, textvariable=self.match_var,
                  width=10, anchor="center").pack(side="left")
        ttk.Button(content_search, text="✕", width=2,
                   command=lambda: self.content_search_var.set("")
                   ).pack(side="left")

    def _build_viewer_nav_bar(self, parent) -> None:
        """Paging, zoom/fit, highlight, save, and bookmark controls."""
        nav = ttk.Frame(parent)
        nav.pack(side="top", fill="x")
        ttk.Button(nav, text="◀ Prev",
                   command=self.prev_page).pack(side="left")
        ttk.Button(nav, text="Next ▶",
                   command=self.next_page).pack(side="left", padx=(4, 8))
        self.page_var = tk.StringVar(value="—")
        ttk.Label(nav, textvariable=self.page_var).pack(side="left")
        self.highlight_btn = ttk.Button(nav, text="Highlight",
                                        state="disabled",
                                        command=self.highlight_selection)
        self.highlight_btn.pack(side="left", padx=(12, 0))
        _add_tooltip(self.highlight_btn,
                     "Hold the left mouse button and drag across text on\n"
                     "the page — selected words shade blue — then click\n"
                     "Highlight. Save writes the highlights into the PDF.")
        self.save_btn = ttk.Button(nav, text="Save", state="disabled",
                                   command=self.save_annotations)
        self.save_btn.pack(side="left", padx=(4, 0))
        _add_tooltip(self.save_btn,
                     "Write highlights to disk (Ctrl+S): choose between an\n"
                     "annotated copy — name(ann).pdf — or the original file.")
        bookmark_btn = ttk.Button(nav, text="🔖", width=3,
                                  command=self.add_bookmark)
        bookmark_btn.pack(side="left", padx=(4, 0))
        _add_tooltip(bookmark_btn,
                     "Bookmark the current page (Ctrl+B). Bookmarks appear\n"
                     "in their own list above the Topics; right-click one\n"
                     "there to rename or delete it.")
        ttk.Button(nav, text="−", width=3,
                   command=lambda: self.change_zoom(1 / ZOOM_STEP)
                   ).pack(side="right")
        ttk.Button(nav, text="+", width=3,
                   command=lambda: self.change_zoom(ZOOM_STEP)
                   ).pack(side="right", padx=(0, 4))
        full_btn = ttk.Button(nav, text="Full page", command=self.fit_page)
        full_btn.pack(side="right", padx=(0, 4))
        _add_tooltip(full_btn,
                     "Fit the whole page in the viewer and resize the\n"
                     "window to match it (P). Dragging a window edge then\n"
                     "zooms the page to follow.")
        fit_btn = ttk.Button(nav, text="Fit width", command=self.fit_width)
        fit_btn.pack(side="right", padx=4)
        _add_tooltip(fit_btn, "Scale the page to the viewer width (W)")

    def _build_viewer_canvas(self, parent) -> None:
        """Scrollable page canvas plus its right-click menu."""
        canvas_wrap = ttk.Frame(parent)
        canvas_wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(canvas_wrap, background="#3a3a3a",
                                highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        v_sb = ttk.Scrollbar(canvas_wrap, orient="vertical",
                             command=self.canvas.yview)
        v_sb.grid(row=0, column=1, sticky="ns")
        h_sb = ttk.Scrollbar(canvas_wrap, orient="horizontal",
                             command=self.canvas.xview)
        h_sb.grid(row=1, column=0, sticky="ew")
        self.canvas.config(yscrollcommand=v_sb.set, xscrollcommand=h_sb.set)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<ButtonPress-1>", self._on_select_start)
        self.canvas.bind("<B1-Motion>", self._on_select_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_select_end)
        self.canvas.bind("<Button-3>", self._on_canvas_right_click)

        # Right-click context menu for the viewer
        self.canvas_menu = tk.Menu(self, tearoff=0)
        self.canvas_menu.add_command(label="Highlight selection",
                                     command=self.highlight_selection)
        self.canvas_menu.add_command(label="Remove highlight",
                                     command=self._remove_highlight_here)
        self.canvas_menu.add_separator()
        self.canvas_menu.add_command(label="Bookmark this page",
                                     command=self.add_bookmark)
        canvas_wrap.rowconfigure(0, weight=1)
        canvas_wrap.columnconfigure(0, weight=1)

    # -- Pane visibility -------------------------------------------------------
    def _apply_pane_visibility(self, persist: bool = True) -> None:
        """Add/remove the PDF-list and Topics panes to match the toolbar
        toggles.  Removing a pane keeps its widgets (and state) intact, so
        re-showing it is instant."""
        show_left = self.show_pdfs_var.get()
        show_mid = self.show_topics_var.get()

        def present(widget) -> bool:
            return str(widget) in self.panes.panes()

        if show_left and not present(self._left_pane):
            self.panes.insert(0, self._left_pane, weight=1)
        elif not show_left and present(self._left_pane):
            self.panes.forget(self._left_pane)

        if show_mid and not present(self._mid_pane):
            self.panes.insert(1 if show_left else 0, self._mid_pane, weight=1)
        elif not show_mid and present(self._mid_pane):
            self.panes.forget(self._mid_pane)

        if persist:
            update_config({"show_pdf_list": show_left,
                           "show_topics": show_mid})

    # -- Keyboard shortcuts ---------------------------------------------------
    def _bind_shortcuts(self) -> None:
        """Global viewer shortcuts. Arrow/space/page keys defer to a focused
        tree so list navigation still works; the rest are viewer-only keys."""
        top = self.winfo_toplevel()

        # Paging (guarded -- let a focused tree keep its own navigation).
        for seq in ("<Left>", "<Prior>"):          # Prior = Page Up
            top.bind(seq, self._key_guarded(self.prev_page))
        for seq in ("<Right>", "<Next>", "<space>"):  # Next = Page Down
            top.bind(seq, self._key_guarded(self.next_page))
        top.bind("<Home>", self._key_guarded(lambda: self.goto_page(0)))
        top.bind("<End>", self._key_guarded(
            lambda: self.goto_page((self.doc.page_count - 1) if self.doc else 0)))

        # Zoom (ignored while typing in the search box).
        for seq in ("<plus>", "<KP_Add>", "<equal>"):
            top.bind(seq, self._viewer_key(lambda: self.change_zoom(ZOOM_STEP)))
        for seq in ("<minus>", "<KP_Subtract>"):
            top.bind(seq, self._viewer_key(
                lambda: self.change_zoom(1 / ZOOM_STEP)))

        # Fit modes.
        top.bind("<Key-w>", self._viewer_key(self.fit_width))
        top.bind("<Key-W>", self._viewer_key(self.fit_width))
        top.bind("<Key-p>", self._viewer_key(self.fit_page))
        top.bind("<Key-P>", self._viewer_key(self.fit_page))

        # Refresh the PDF list (offers to build missing topic lists).
        top.bind("<F5>", lambda e: self.on_refresh_clicked())

        # Save highlight annotations.
        top.bind("<Control-s>",
                 lambda e: (self.save_annotations(), "break")[1])

        # Bookmark the current page.  _viewer_key (not a bare bind): entries
        # have a default emacs Control-b binding we must not shadow.
        top.bind("<Control-b>", self._viewer_key(self.add_bookmark))

        # Content search: Ctrl+F focuses the box, F3 / Shift+F3 step matches.
        top.bind("<Control-f>",
                 lambda e: (self.content_search_entry.focus_set(), "break")[1])
        top.bind("<F3>", lambda e: (self.next_match(), "break")[1])
        top.bind("<Shift-F3>", lambda e: (self.prev_match(), "break")[1])

        # Mouse wheel over the viewer scrolls the page and flips at the edges.
        top.bind_all("<MouseWheel>", self._on_mousewheel)

    def _key_guarded(self, action):
        """Return a key handler that runs `action` unless a tree or text entry
        has focus (so arrows/space/page keys still drive the lists and let you
        type in the search box)."""
        def handler(event):
            if isinstance(self.focus_get(), (ttk.Treeview, ttk.Entry, tk.Entry)):
                return None          # let the widget handle it
            action()
            return "break"
        return handler

    def _viewer_key(self, action):
        """Key handler for viewer-only keys; ignored while typing in an entry."""
        def handler(event):
            if isinstance(self.focus_get(), (ttk.Entry, tk.Entry)):
                return None
            action()
            return "break"
        return handler

    # -- Drag & drop (Windows) -------------------------------------------------
    def _enable_file_drop(self) -> None:
        """Accept files dropped from Explorer anywhere on the window.

        Uses the classic Win32 WM_DROPFILES route: register the toplevel's
        client window with DragAcceptFiles, then subclass its window procedure
        to catch the drop message.  Drops over child widgets bubble up to the
        nearest ancestor that accepts files, so one hook covers the whole UI.

        The window procedure only *queues* the dropped paths -- it must not call
        into Tk/Tcl, because it runs reentrantly inside Tcl's own event dispatch
        and doing so crashes the interpreter.  A periodic poller (started here in
        the normal main-loop context) drains the queue and does the real work.
        """
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32

        WM_DROPFILES = 0x0233
        GWLP_WNDPROC = -4
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM)

        shell32.DragAcceptFiles.argtypes = (wintypes.HWND, wintypes.BOOL)
        shell32.DragQueryFileW.argtypes = (wintypes.WPARAM, wintypes.UINT,
                                           ctypes.c_wchar_p, wintypes.UINT)
        shell32.DragFinish.argtypes = (wintypes.WPARAM,)
        set_wndproc = getattr(user32, "SetWindowLongPtrW",
                              user32.SetWindowLongW)
        set_wndproc.restype = LRESULT
        set_wndproc.argtypes = (wintypes.HWND, ctypes.c_int, WNDPROC)
        user32.CallWindowProcW.restype = LRESULT
        user32.CallWindowProcW.argtypes = (LRESULT, wintypes.HWND,
                                           wintypes.UINT, wintypes.WPARAM,
                                           wintypes.LPARAM)

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                for i in range(count):
                    n = shell32.DragQueryFileW(wparam, i, None, 0) + 1
                    buf = ctypes.create_unicode_buffer(n)
                    shell32.DragQueryFileW(wparam, i, buf, n)
                    self._drop_queue.append(buf.value)
                shell32.DragFinish(wparam)
                # NOTE: no Tk calls here -- the poller handles the queue.
                return 0
            return user32.CallWindowProcW(self._orig_wndproc, hwnd, msg,
                                          wparam, lparam)

        hwnd = self.winfo_toplevel().winfo_id()
        shell32.DragAcceptFiles(hwnd, True)
        self._wndproc_ref = WNDPROC(wndproc)   # keep alive or the hook crashes
        self._orig_wndproc = set_wndproc(hwnd, GWLP_WNDPROC, self._wndproc_ref)
        self.after(200, self._poll_drop_queue)

    def _poll_drop_queue(self) -> None:
        """Drain paths queued by the drop window procedure (main-loop context)."""
        if self._drop_queue:
            batch, self._drop_queue = self._drop_queue, []
            self._on_files_dropped(batch)
        self.after(200, self._poll_drop_queue)

    def _on_files_dropped(self, paths: list[str]) -> None:
        """Copy dropped PDFs into <folder>/inbox and build missing .toc files."""
        pdfs = [p for p in paths
                if p.lower().endswith(".pdf") and os.path.isfile(p)]
        if not pdfs:
            messagebox.showinfo(
                "Drop PDFs",
                "Drop one or more .pdf files to add them to the inbox.")
            return

        inbox = os.path.join(self.folder, "inbox")
        try:
            os.makedirs(inbox, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Drop PDFs",
                                 f"Could not create {inbox}:\n\n{exc}")
            return

        added: list[str] = []
        failed: list[str] = []
        for src in pdfs:
            name = os.path.basename(src)
            dest = os.path.join(inbox, name)
            try:
                if os.path.exists(dest):
                    if os.path.samefile(src, dest):
                        added.append(dest)   # already in the inbox
                        continue
                    if not messagebox.askyesno(
                            "Replace PDF?",
                            f"{name} is already in the inbox.\nReplace it?"):
                        continue
                shutil.copy2(src, dest)
            except OSError as exc:
                failed.append(f"{name}: {exc}")
                continue
            added.append(dest)
            # Keep an existing (possibly hand-edited) topics file; only
            # generate one when the PDF has no metadata yet.
            if find_metadata_path(dest) is None:
                try:
                    tocgen.write_toc(dest)
                except (OSError, RuntimeError, ValueError) as exc:
                    # PyMuPDF signals bad/encrypted PDFs via RuntimeError.
                    failed.append(f"{name}: added, but no topics built ({exc})")

        if added:
            # Show the inbox folder open so the new files are visible.
            self._expanded.add("inbox")
            update_config({"expanded_folders": sorted(self._expanded)})
            if self.filter_text:
                self.search_var.set("")   # clears filter and refreshes
            else:
                self.refresh_pdf_list()
            self._select_pdf(added[-1])

        if failed:
            messagebox.showwarning(
                "Drop PDFs",
                f"Added {len(added)} PDF{'s' if len(added) != 1 else ''} "
                "to the inbox.\n\nProblems:\n" + "\n".join(failed[:10]))

    # -- Help -----------------------------------------------------------------
    def show_help(self) -> None:
        """Open a window that renders the bundled HELP.md."""
        existing = self._help_win
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_set()
            return

        try:
            with open(_resource_path("HELP.md"), "r", encoding="utf-8") as fh:
                md = fh.read()
        except OSError:
            md = ("# Help\n\nThe help file (HELP.md) could not be found.\n\n"
                  "See the project page for documentation.")

        win = tk.Toplevel(self)
        self._help_win = win
        win.title("PDF Sherpa — Help")
        # Open centered over the main window (clamped to the screen).
        w, h = 660, 740
        top = self.winfo_toplevel()
        x = top.winfo_rootx() + (top.winfo_width() - w) // 2
        y = top.winfo_rooty() + (top.winfo_height() - h) // 2
        x = max(0, min(x, win.winfo_screenwidth() - w))
        y = max(0, min(y, win.winfo_screenheight() - h))
        win.geometry(f"{w}x{h}+{x}+{y}")
        _apply_window_icon(win)

        # Version + update check live at the bottom of the Help window
        # (packed first so the bar keeps its space when the window shrinks).
        bottom = ttk.Frame(win, padding=(16, 6))
        bottom.pack(side="bottom", fill="x")
        ttk.Label(bottom, text=f"PDF Sherpa V{APP_VERSION}").pack(side="left")
        ttk.Button(bottom, text="Check for updates",
                   command=lambda: self._start_update_check(manual=True)
                   ).pack(side="right")

        wrap = ttk.Frame(win)
        wrap.pack(fill="both", expand=True)
        text = tk.Text(wrap, wrap="word", padx=16, pady=12, borderwidth=0,
                       background="#ffffff", foreground="#1a1a1a",
                       font=("Segoe UI", 11), cursor="arrow", spacing3=2)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=text.yview)
        text.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)

        _render_markdown(text, md)
        text.config(state="disabled")

        win.bind("<Escape>", lambda e: win.destroy())

    # -- Updates ---------------------------------------------------------------
    #
    # The check runs on a daemon thread because urllib blocks.  Like the
    # drag-drop hook above, the thread NEVER touches Tk: it only assigns a
    # plain attribute, and an after()-scheduled poller on the main loop picks
    # the result up and does the UI work.

    def _start_update_check(self, manual: bool = False) -> None:
        """Kick off a release check.  Auto checks (launch) fail silently and
        honor the config opt-outs; manual checks (Help window) always run and
        always report an outcome."""
        if self._update_thread is not None and self._update_thread.is_alive():
            return
        if not manual and not load_config().get("check_updates", True):
            return
        self._update_result = None
        self._update_thread = threading.Thread(target=self._update_worker,
                                               daemon=True)
        self._update_thread.start()
        self.after(500, lambda: self._poll_update_result(manual))

    def _update_worker(self) -> None:
        """Thread body -- no Tk calls here (see _enable_file_drop's note)."""
        try:
            self._update_result = ("ok", _fetch_latest_release())
        except (OSError, ValueError, http.client.HTTPException) as exc:
            # Offline, timeout, rate limit, bad JSON, or a bad release tag.
            self._update_result = ("error", str(exc))

    def _poll_update_result(self, manual: bool) -> None:
        """Main-loop poller: wait for the worker, then act on its result."""
        if not self.winfo_exists():
            return
        if self._update_result is None:
            self.after(500, lambda: self._poll_update_result(manual))
            return
        status, payload = self._update_result
        if status == "error":
            if manual:
                messagebox.showwarning(
                    "Check for updates",
                    f"Could not check for updates:\n{payload}")
            return
        info = payload
        current = _parse_version(APP_VERSION)
        if current is not None and info["version"] <= current:
            if manual:
                messagebox.showinfo("Check for updates",
                                    f"You're up to date (V{APP_VERSION}).")
            return
        if (not manual
                and load_config().get("skip_version") == info["version_str"]):
            return
        self._prompt_update(info)

    def _prompt_update(self, info: dict) -> None:
        choice = messagebox.askyesnocancel(
            "Update available",
            f"PDF Sherpa V{info['version_str']} is available "
            f"(you have V{APP_VERSION}).\n\n"
            "Yes  –  download and install it now\n"
            "No  –  skip this version (won't ask again)\n"
            "Cancel  –  remind me next time")
        if choice is None:
            return  # remind again next launch
        if not choice:
            update_config({"skip_version": info["version_str"]})
            return
        appimage = _running_appimage()
        if appimage:
            url = info.get("appimage_url")
            if url:
                self._begin_appimage_download(info, url, appimage)
            else:
                webbrowser.open(info["html_url"])
            return
        portable = _running_portable()
        url = info["portable_url"] if portable else info["asset_url"]
        if getattr(sys, "frozen", False) and url:
            self._begin_download(info, url, portable)
        else:
            # Running from source (or no matching asset): don't install over
            # a dev checkout -- just open the releases page.
            webbrowser.open(info["html_url"])

    def _begin_appimage_download(self, info: dict, url: str,
                                 current_path: str) -> None:
        """Download the new AppImage beside the running one, then swap it in.

        The download lands in the target's own directory so the final swap is
        an atomic same-filesystem rename onto a fresh inode -- the running
        (mounted) AppImage keeps its old inode until this process exits."""
        self._dl_appimage = True
        self._dl_portable = False
        # Resolve symlinks (the --appimage install mode launches via one) so
        # we replace the real file and leave the symlink pointing at it.
        self._appimage_target = os.path.realpath(current_path)
        dest = os.path.join(
            os.path.dirname(self._appimage_target),
            f".PDFSherpa-update-{info['version_str']}.AppImage")
        win = tk.Toplevel(self)
        self._dl_win = win
        win.title("Updating PDF Sherpa")
        win.resizable(False, False)
        top = self.winfo_toplevel()
        win.geometry(f"+{top.winfo_rootx() + 80}+{top.winfo_rooty() + 80}")
        ttk.Label(win, text="Downloading update… PDF Sherpa will "
                            "restart when it's ready.",
                  padding=(16, 12)).pack()
        bar = ttk.Progressbar(win, mode="indeterminate", length=280)
        bar.pack(padx=16, pady=(0, 14))
        bar.start(12)
        self._download_result = None
        threading.Thread(target=self._download_worker,
                         args=(url, dest), daemon=True).start()
        self.after(500, self._poll_download_result)

    def _swap_appimage_and_exit(self, new_path: str) -> None:
        """Install the downloaded AppImage over the running one and relaunch."""
        target = self._appimage_target
        if not target:
            return
        try:
            os.chmod(new_path, 0o755)
            os.replace(new_path, target)         # atomic, same filesystem
        except OSError as exc:
            try:
                os.remove(new_path)
            except OSError:
                pass
            messagebox.showerror(
                "Update failed",
                f"Could not install the update:\n{exc}\n\n"
                "You can download it manually from the releases page on "
                "GitHub.")
            return
        subprocess.Popen([target], start_new_session=True)
        self._on_close()

    def _begin_download(self, info: dict, url: str, portable: bool) -> None:
        """Download the installer (or portable zip) in the background with a
        small progress window; the app stays usable underneath."""
        self._dl_portable = portable
        self._dl_appimage = False
        if portable:
            dest = os.path.join(tempfile.gettempdir(),
                                f"PDFSherpa-Portable-{info['version_str']}.zip")
        else:
            dest = os.path.join(tempfile.gettempdir(),
                                f"PDFSherpa-Setup-{info['version_str']}.exe")
        win = tk.Toplevel(self)
        self._dl_win = win
        win.title("Updating PDF Sherpa")
        win.resizable(False, False)
        top = self.winfo_toplevel()
        win.geometry(f"+{top.winfo_rootx() + 80}+{top.winfo_rooty() + 80}")
        ttk.Label(win, text="Downloading update… PDF Sherpa will "
                            "restart when it's ready.",
                  padding=(16, 12)).pack()
        bar = ttk.Progressbar(win, mode="indeterminate", length=280)
        bar.pack(padx=16, pady=(0, 14))
        bar.start(12)
        self._download_result = None
        threading.Thread(target=self._download_worker,
                         args=(url, dest), daemon=True).start()
        self.after(500, self._poll_download_result)

    def _download_worker(self, url: str, dest: str) -> None:
        """Thread body -- no Tk calls here."""
        try:
            req = urllib.request.Request(url,
                                         headers={"User-Agent": "PDFSherpa"})
            with urllib.request.urlopen(req, timeout=30) as resp, \
                    open(dest + ".part", "wb") as out:
                shutil.copyfileobj(resp, out, 256 * 1024)
            os.replace(dest + ".part", dest)
            self._download_result = ("ok", dest)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            self._download_result = ("error", str(exc))

    def _poll_download_result(self) -> None:
        if not self.winfo_exists():
            return
        if self._download_result is None:
            self.after(500, self._poll_download_result)
            return
        if self._dl_win is not None and self._dl_win.winfo_exists():
            self._dl_win.destroy()
        status, payload = self._download_result
        if status == "error":
            # The user explicitly asked for this download, so don't be silent.
            messagebox.showerror(
                "Update failed",
                f"The download failed:\n{payload}\n\n"
                "You can download the update manually from the releases "
                "page on GitHub.")
            return
        if self._dl_appimage:
            self._swap_appimage_and_exit(payload)
        elif self._dl_portable:
            self._swap_portable_and_exit(payload)
        else:
            self._launch_installer_and_exit(payload)

    def _launch_installer_and_exit(self, setup_path: str) -> None:
        """Hand off to the installer and quit so it can replace the exe.

        A detached hidden cmd runs a small batch file: wait ~2 s for this
        process to exit and release the exe lock, run the installer silently,
        relaunch the app, clean up.  Each line runs even if an earlier one
        failed, so a failed install still relaunches the intact old exe.
        The steps live in a batch file rather than an inline `cmd /c` string
        because list2cmdline escapes quotes as \" -- which cmd.exe does not
        understand -- garbling any quoted path.  installer.iss's [Run] entry
        is `skipifsilent`, so the silent install won't also launch the app
        (no double launch).  CREATE_NO_WINDOW rather than DETACHED_PROCESS
        because timeout.exe needs a (hidden) console to run.
        """
        app_exe = sys.executable  # the installer overwrites this same path
        batch_path = setup_path + ".cmd"
        with open(batch_path, "w", encoding="ascii", errors="replace") as fh:
            fh.write(f'timeout /t 2 /nobreak >nul\r\n'
                     f'"{setup_path}" /VERYSILENT /NORESTART '
                     f'/SUPPRESSMSGBOXES\r\n'
                     f'start "" "{app_exe}"\r\n'
                     f'del /q "{setup_path}"\r\n'
                     f'del /q "%~f0"\r\n')
        self._spawn_handoff_batch(batch_path)
        self._on_close()

    def _spawn_handoff_batch(self, batch_path: str) -> None:
        """Run an update-handoff batch hidden and detached.

        PyInstaller's runtime env vars (_PYI_*, _MEIPASS2) MUST be stripped:
        inherited through cmd, they make the relaunched exe reuse THIS
        process's _MEIxxxx extraction dir -- deleted the moment we exit --
        and it dies with "Failed to load Python DLL" instead of extracting
        its own.
        """
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("_PYI_") and k != "_MEIPASS2"}
        subprocess.Popen(
            ["cmd", "/c", batch_path],
            creationflags=(subprocess.CREATE_NO_WINDOW
                           | subprocess.CREATE_NEW_PROCESS_GROUP),
            close_fds=True,
            env=env,
            cwd=os.path.dirname(batch_path))

    def _swap_portable_and_exit(self, zip_path: str) -> None:
        """Portable self-update: replace this exe with the one from the
        downloaded zip, then relaunch.

        Same batch-file handoff as _launch_installer_and_exit (and for the
        same list2cmdline reason).  The new exe is unpacked next to the zip
        and `move`d over the running copy once it has exited; if the move
        fails (exe still locked), the batch carries on and relaunches the
        intact old exe.  `move` works across volumes, so a portable copy on
        a USB stick updates too.
        """
        app_exe = sys.executable
        new_exe = zip_path + ".new.exe"
        try:
            with zipfile.ZipFile(zip_path) as zf:
                member = next((n for n in zf.namelist()
                               if n.lower().endswith(".exe")), None)
                if member is None:
                    raise ValueError("no exe inside the update zip")
                with zf.open(member) as src, open(new_exe, "wb") as out:
                    shutil.copyfileobj(src, out)
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
            messagebox.showerror(
                "Update failed",
                f"Could not unpack the update:\n{exc}\n\n"
                "You can download the update manually from the releases "
                "page on GitHub.")
            return
        batch_path = zip_path + ".cmd"
        with open(batch_path, "w", encoding="ascii", errors="replace") as fh:
            fh.write(f'timeout /t 2 /nobreak >nul\r\n'
                     f'move /y "{new_exe}" "{app_exe}"\r\n'
                     f'start "" "{app_exe}"\r\n'
                     f'del /q "{zip_path}"\r\n'
                     f'del /q "{new_exe}"\r\n'
                     f'del /q "%~f0"\r\n')
        self._spawn_handoff_batch(batch_path)
        self._on_close()

    # -- PDF list -------------------------------------------------------------
    def choose_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.folder,
                                         title="Choose folder containing PDFs")
        if chosen:
            self.folder = chosen
            update_config({"folder": os.path.abspath(chosen)})
            self.refresh_pdf_list()
            self._refresh_favorites_list()   # re-scope relative favorites

    def refresh_pdf_list(self) -> None:
        self.winfo_toplevel().title(
            f"PDF Sherpa - V{APP_VERSION} - {os.path.abspath(self.folder)}")
        self.pdf_tree.delete(*self.pdf_tree.get_children())
        self._pdf_by_iid = {}
        self._folder_rel_by_iid = {}

        if not os.path.isdir(self.folder):
            self.pdf_tree.insert("", "end", text="(folder not found)")
            return

        # While filtering, force folders open so matches are visible; otherwise
        # honour the remembered open/closed state (closed by default).
        filtering = bool(self.filter_text)

        # Map each subfolder's relative path to its tree node, creating parent
        # nodes on demand.  "" is the root (the top-level folder itself).
        folder_nodes: dict[str, str] = {"": ""}

        def ensure_folder(rel: str) -> str:
            if rel in folder_nodes:
                return folder_nodes[rel]
            parent_rel, _, name = rel.rpartition(os.sep)
            parent = ensure_folder(parent_rel)
            is_open = filtering or rel in self._expanded
            node = self.pdf_tree.insert(parent, "end", text=name,
                                        open=is_open, tags=("folder",))
            folder_nodes[rel] = node
            self._folder_rel_by_iid[node] = rel
            return node

        needle = self.filter_text.lower()
        count = 0
        for dirpath, dirnames, filenames in os.walk(self.folder):
            dirnames.sort(key=str.lower)
            rel = os.path.relpath(dirpath, self.folder)
            rel = "" if rel == "." else rel
            pdfs = sorted((f for f in filenames if f.lower().endswith(".pdf")),
                          key=str.lower)
            if needle:   # filter by filename substring (folders follow content)
                pdfs = [f for f in pdfs if needle in f.lower()]
            if not pdfs:
                continue
            parent = ensure_folder(rel)
            for name in pdfs:
                path = os.path.join(dirpath, name)
                has_meta = find_metadata_path(path) is not None
                text = name if has_meta else name + "   (no metadata)"
                iid = self.pdf_tree.insert(
                    parent, "end", text=text,
                    tags=self._pdf_row_tags(path, has_meta))
                self._pdf_by_iid[iid] = path
                count += 1

        if count == 0:
            msg = "(no matches)" if needle else "(no PDFs found)"
            self.pdf_tree.insert("", "end", text=msg)

    def _pdf_row_tags(self, pdf_path: str, has_meta: bool) -> tuple[str, ...]:
        """Tree tags (and thus colour) for a PDF row.  A PDF with saved
        bookmarks shows blue; otherwise a PDF with no .toc/.json metadata shows
        grey; otherwise the default colour.  Bookmarks win over the no-metadata
        cue -- the "(no metadata)" text still says so -- so a bookmarked PDF
        always stands out.  Checking the sidecar's existence is cheap: an empty
        bookmark list deletes the file (see save_bookmarks), so file present
        means the PDF has bookmarks."""
        if os.path.isfile(bookmarks_path(pdf_path)):
            return ("bookmarked",)
        if not has_meta:
            return ("nometa",)
        return ()

    def _recolor_pdf_row(self, pdf_path: str) -> None:
        """Recompute a PDF's row colour after its bookmarks change (so adding
        the first bookmark turns it blue, and removing the last reverts it).
        No-op if the PDF isn't currently listed."""
        if not pdf_path:
            return
        if self._is_favorite(pdf_path):
            self._refresh_favorites_list()   # blue-cue may have changed
        want = os.path.normcase(os.path.abspath(pdf_path))
        for iid, path in self._pdf_by_iid.items():
            if os.path.normcase(os.path.abspath(path)) == want:
                has_meta = find_metadata_path(path) is not None
                self.pdf_tree.item(iid, tags=self._pdf_row_tags(path, has_meta))
                return

    def on_pdf_selected(self, _event=None) -> None:
        selection = self.pdf_tree.selection()
        if not selection:
            return
        path = self._pdf_by_iid.get(selection[0])
        if path is None:
            return  # a folder node or placeholder row, not a PDF
        self.load_pdf(path)
        self.load_topics(path)

    # -- Search / filter ------------------------------------------------------
    def _on_search_changed(self, *_args) -> None:
        self.filter_text = self.search_var.get().strip()
        self.refresh_pdf_list()

    # -- Remember folder open/closed state ------------------------------------
    def _on_folder_toggle(self, is_open: bool) -> None:
        if self.filter_text:
            return   # filter forces folders open -- don't persist that
        rel = self._folder_rel_by_iid.get(self.pdf_tree.focus())
        if rel is None:
            return
        if is_open:
            self._expanded.add(rel)
        else:
            self._expanded.discard(rel)
        update_config({"expanded_folders": sorted(self._expanded)})

    # -- Refresh (with optional TOC generation) -------------------------------
    def on_refresh_clicked(self) -> None:
        self.refresh_pdf_list()
        missing = self._pdfs_without_metadata()
        if not missing:
            return
        n = len(missing)
        if not messagebox.askyesno(
                "Build topic lists?",
                f"{n} PDF{'s' if n > 1 else ''} in this folder "
                f"{'have' if n > 1 else 'has'} no topics (.toc) file.\n\n"
                "Generate topic lists now from each PDF's bookmarks, or from "
                "its text when there are no bookmarks?"):
            return

        made, failed = 0, []
        for pdf in missing:
            try:
                tocgen.write_toc(pdf)
                made += 1
            except (OSError, RuntimeError, ValueError) as exc:
                # Keep going on a bad PDF (PyMuPDF raises RuntimeError).
                failed.append(f"{os.path.basename(pdf)}: {exc}")

        self.refresh_pdf_list()               # pick up the new .toc files
        summary = f"Created {made} topics file{'s' if made != 1 else ''}."
        if failed:
            summary += "\n\nCould not process:\n" + "\n".join(failed[:10])
            if len(failed) > 10:
                summary += f"\n…and {len(failed) - 10} more."
        messagebox.showinfo("Topic lists", summary)

    def _pdfs_without_metadata(self) -> list[str]:
        """All PDFs under the folder that have no companion .toc/.json."""
        out: list[str] = []
        if not os.path.isdir(self.folder):
            return out
        for dirpath, _dirs, filenames in os.walk(self.folder):
            for name in sorted(filenames, key=str.lower):
                if name.lower().endswith(".pdf"):
                    path = os.path.join(dirpath, name)
                    if find_metadata_path(path) is None:
                        out.append(path)
        return out

    # -- Right-click context menu ---------------------------------------------
    def _on_pdf_right_click(self, event) -> None:
        iid = self.pdf_tree.identify_row(event.y)
        if not iid:
            return
        self.pdf_tree.selection_set(iid)
        self.pdf_tree.focus(iid)
        # A PDF row maps to a file; a folder row maps to its directory.
        path = self._pdf_by_iid.get(iid)
        if path is not None:
            self._ctx_path = path
            self.pdf_menu.entryconfigure(0, state="normal", label="Open PDF")
            self.pdf_menu.entryconfigure(
                3, state="normal",
                label=("Remove from favorites" if self._is_favorite(path)
                       else "Add to favorites"))
        else:
            self._ctx_path = self._folder_path_for(iid)
            self.pdf_menu.entryconfigure(0, state="disabled", label="Open")
            self.pdf_menu.entryconfigure(3, state="disabled")
        if self._ctx_path:
            self.pdf_menu.tk_popup(event.x_root, event.y_root)

    def _folder_path_for(self, iid: str) -> str:
        """Reconstruct the on-disk path of a folder node from the tree."""
        parts = []
        node = iid
        while node:
            parts.append(self.pdf_tree.item(node, "text"))
            node = self.pdf_tree.parent(node)
        return os.path.join(self.folder, *reversed(parts))

    def _ctx_open(self) -> None:
        if self._ctx_path and os.path.exists(self._ctx_path):
            try:
                _open_path(self._ctx_path)        # OS default handler
            except (OSError, subprocess.SubprocessError) as exc:
                messagebox.showerror("Open", str(exc))

    def _ctx_reveal(self) -> None:
        path = self._ctx_path
        if not path or not os.path.exists(path):
            return
        try:
            _reveal_path(path)
        except (OSError, subprocess.SubprocessError) as exc:
            messagebox.showerror(REVEAL_LABEL, str(exc))

    # -- Favorites ------------------------------------------------------------
    # Favorites are stored relative to the current root folder when the file
    # lives under it (absolute otherwise), so they follow the folder and are
    # portable.  self._favorites holds these stored forms; they are resolved
    # against the *current* root at open/display time, so switching folders
    # re-scopes them.
    def _fav_store_form(self, pdf_path: str) -> str:
        """The favorite's saved form: relative to the root folder when the
        file lives under it, else an absolute path."""
        ap = os.path.abspath(pdf_path)
        root = os.path.abspath(self.folder)
        try:
            rel = os.path.relpath(ap, root)
        except ValueError:
            return ap                           # different drive -> absolute
        if rel == os.pardir or rel.startswith(os.pardir + os.sep):
            return ap                           # outside the root -> absolute
        return rel

    def _fav_abs_path(self, stored: str) -> str:
        """Resolve a stored favorite to an absolute path against the current
        root folder (relative forms join the root; absolute forms pass through)."""
        if os.path.isabs(stored):
            return os.path.normpath(stored)
        return os.path.normpath(os.path.join(os.path.abspath(self.folder),
                                             stored))

    def _fav_key(self, stored: str) -> str:
        """Identity key for dedup / membership: the resolved absolute path,
        case-folded (so a relative and absolute form of one file match)."""
        return os.path.normcase(self._fav_abs_path(stored))

    def _is_favorite(self, pdf_path: str) -> bool:
        want = os.path.normcase(os.path.abspath(pdf_path))
        return any(self._fav_key(f) == want for f in self._favorites)

    def _add_favorite(self, pdf_path: str) -> None:
        """Pin a PDF to the top of the favorites list (deduped, capped)."""
        if not pdf_path:
            return
        want = os.path.normcase(os.path.abspath(pdf_path))
        self._favorites = [f for f in self._favorites if self._fav_key(f) != want]
        self._favorites.insert(0, self._fav_store_form(pdf_path))
        del self._favorites[MAX_FAVORITES:]     # drop the oldest over the cap
        update_config({"favorites": self._favorites})
        self._refresh_favorites_list()

    def _remove_favorite(self, pdf_path: str) -> None:
        """Remove the favorite matching pdf_path (an absolute path)."""
        want = os.path.normcase(os.path.abspath(pdf_path))
        kept = [f for f in self._favorites if self._fav_key(f) != want]
        if len(kept) == len(self._favorites):
            return                              # wasn't a favorite
        self._favorites = kept
        update_config({"favorites": self._favorites})
        self._refresh_favorites_list()

    def _clear_favorites(self) -> None:
        """Empty the favorites list (after a confirmation)."""
        n = len(self._favorites)
        if n == 0:
            messagebox.showinfo("Clear favorites", "You have no favorites.")
            return
        if not messagebox.askyesno(
                "Clear favorites",
                f"Remove all {n} favorite{'s' if n != 1 else ''}?"):
            return
        self._favorites = []
        update_config({"favorites": self._favorites})
        self._refresh_favorites_list()

    def _export_favorites(self) -> None:
        """Write the favorites (root-relative where possible) to a JSON file."""
        if not self._favorites:
            messagebox.showinfo("Export favorites",
                                 "You have no favorites to export.")
            return
        path = filedialog.asksaveasfilename(
            title="Export favorites", defaultextension=".json",
            initialfile="pdf-sherpa-favorites.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not path:
            return                              # user cancelled
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"favorites": self._favorites}, fh, indent=2)
        except OSError as exc:
            messagebox.showerror("Export favorites",
                                 f"Could not write file:\n{exc}")
            return
        messagebox.showinfo(
            "Export favorites",
            f"Exported {len(self._favorites)} favorite(s) to:\n{path}")

    def _import_favorites(self) -> None:
        """Load favorites from a JSON file, merging with or replacing the
        current list (deduped, capped at MAX_FAVORITES)."""
        path = filedialog.askopenfilename(
            title="Import favorites",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not path:
            return                              # user cancelled
        imported = self._read_favorites_file(path)
        if imported is None:
            return                              # already reported to the user
        merge = True
        if self._favorites:
            ans = messagebox.askyesnocancel(
                "Import favorites",
                "Merge with your current favorites?\n\n"
                "Yes  -  add the imported PDFs to your current list\n"
                "No   -  replace your current favorites")
            if ans is None:
                return                          # Cancel
            merge = ans
        combined = (self._favorites + imported) if merge else imported
        self._favorites = self._dedup_favorites(combined)[:MAX_FAVORITES]
        update_config({"favorites": self._favorites})
        self._refresh_favorites_list()
        messagebox.showinfo(
            "Import favorites",
            f"Your favorites list now has {len(self._favorites)} item(s).")

    def _read_favorites_file(self, path: str) -> list[str] | None:
        """Parse a favorites export file into a list of path strings, or show
        an error and return None.  Accepts either {"favorites": [...]} or a
        bare list of paths."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Import favorites",
                                 f"Could not read file:\n{exc}")
            return None
        raw = data.get("favorites") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            messagebox.showerror("Import favorites",
                                 "That file doesn't contain a favorites list.")
            return None
        paths = [p for p in raw if isinstance(p, str) and p.strip()]
        if not paths:
            messagebox.showinfo("Import favorites",
                                "No favorites were found in that file.")
            return None
        return paths

    def _dedup_favorites(self, paths: list[str]) -> list[str]:
        """Canonicalise each entry to the current root's stored form and drop
        later duplicates (case/path-insensitive), preserving first-seen order.
        Imported absolute paths under the root thus become root-relative."""
        seen: set[str] = set()
        out: list[str] = []
        for p in paths:
            canon = self._fav_store_form(self._fav_abs_path(p))
            key = self._fav_key(canon)
            if key in seen:
                continue
            seen.add(key)
            out.append(canon)
        return out

    def _refresh_favorites_list(self) -> None:
        """Redraw the favorites listbox, sizing it to its contents.  Each row
        shows its path relative to the root (absolute when it lives elsewhere).
        Missing files show red; PDFs with saved bookmarks show blue (matching
        the tree).  An empty list shows a single greyed hint row."""
        lb = self.fav_list
        lb.delete(0, "end")
        self._fav_placeholder = not self._favorites
        if self._fav_placeholder:
            lb.insert("end", " (right-click a PDF below to add)")
            lb.itemconfig(0, foreground="#999")
            lb.config(height=1)
            return
        for i, stored in enumerate(self._favorites):
            abs_path = self._fav_abs_path(stored)
            lb.insert("end", " " + stored)
            if not os.path.isfile(abs_path):
                lb.itemconfig(i, foreground="#c01c28")   # missing file
            elif os.path.isfile(bookmarks_path(abs_path)):
                lb.itemconfig(i, foreground="#1a5fb4")   # has bookmarks
        lb.config(height=min(MAX_FAVORITES, len(self._favorites)))

    def _open_favorite(self, pdf_path: str) -> None:
        """Load a favorite; select it in the tree too when it's listed."""
        if not os.path.isfile(pdf_path):
            messagebox.showerror("Favorites", f"File not found:\n{pdf_path}")
            return
        if not self._select_pdf(pdf_path):      # not under the current folder
            self.load_pdf(pdf_path)
            self.load_topics(pdf_path)

    def _on_favorite_selected(self, _event=None) -> None:
        if self._fav_placeholder:
            return
        sel = self.fav_list.curselection()
        if sel and 0 <= sel[0] < len(self._favorites):
            self._open_favorite(self._fav_abs_path(self._favorites[sel[0]]))

    def _ctx_toggle_favorite(self) -> None:
        """PDF-list menu: add the target PDF to favorites, or remove it."""
        path = self._ctx_path
        if not path or os.path.isdir(path):
            return
        if self._is_favorite(path):
            self._remove_favorite(path)
        else:
            self._add_favorite(path)

    def _on_favorite_right_click(self, event) -> None:
        if self._fav_placeholder:
            return
        idx = self.fav_list.nearest(event.y)
        if idx < 0 or idx >= len(self._favorites):
            return
        self.fav_list.selection_clear(0, "end")
        self.fav_list.selection_set(idx)
        # Store the resolved absolute path so open / remove / reveal all work
        # uniformly regardless of the stored (relative vs absolute) form.
        self._fav_ctx_path = self._fav_abs_path(self._favorites[idx])
        self.fav_menu.tk_popup(event.x_root, event.y_root)

    def _fav_ctx_open(self) -> None:
        if self._fav_ctx_path:
            self._open_favorite(self._fav_ctx_path)

    def _fav_ctx_remove(self) -> None:
        if self._fav_ctx_path:
            self._remove_favorite(self._fav_ctx_path)

    def _fav_ctx_reveal(self) -> None:
        self._ctx_path = self._fav_ctx_path
        self._ctx_reveal()

    # -- Session (window size + last PDF) -------------------------------------
    def _restore_session(self) -> None:
        cfg = load_config()
        top = self.winfo_toplevel()

        geom = cfg.get("geometry")
        if isinstance(geom, str) and "x" in geom:
            try:
                top.geometry(geom)
            except tk.TclError:
                pass

        last = cfg.get("last_pdf")
        if last and os.path.isfile(last):
            self._select_pdf(last)

        top.protocol("WM_DELETE_WINDOW", self._on_close)

    def _select_pdf(self, pdf_path: str) -> bool:
        """Select pdf_path in the tree if listed (selection fires the load).
        Returns True when a matching row was found and selected."""
        want = os.path.normcase(os.path.abspath(pdf_path))
        for iid, path in self._pdf_by_iid.items():
            if os.path.normcase(os.path.abspath(path)) == want:
                self.pdf_tree.selection_set(iid)
                self.pdf_tree.see(iid)
                self.pdf_tree.focus(iid)
                return True
        return False

    def _on_close(self) -> None:
        self._maybe_save_annots()
        if str(self._bm_frame) in self._mid_split.panes():
            self._bm_sash = self._mid_split.sashpos(0)
        updates = {"geometry": self.winfo_toplevel().geometry()}
        if self._bm_sash is not None:
            updates["bm_sash"] = self._bm_sash
        update_config(updates)
        self.winfo_toplevel().destroy()

    # -- Topics ---------------------------------------------------------------
    def load_topics(self, pdf_path: str) -> None:
        self._topic_entries = []
        self._topic_placeholder = None
        self._bookmarks = load_bookmarks(pdf_path)
        self._populate_bookmark_tree()
        metadata_path = find_metadata_path(pdf_path)
        if metadata_path is None:
            self._topic_placeholder = "(no metadata file found)"
            self._populate_topic_tree()
            return
        try:
            entries = load_metadata(metadata_path)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            # Unreadable file, bad JSON/toc syntax, or wrong entry shape.
            self._topic_placeholder = (
                f"(error reading {os.path.basename(metadata_path)})")
            self._populate_topic_tree()
            messagebox.showerror("Metadata error",
                                 f"Could not read {metadata_path}:\n\n{exc}")
            return

        if not entries:
            self._topic_placeholder = (
                f"(no topics in {os.path.basename(metadata_path)})")
            self._populate_topic_tree()
            return

        self._topic_entries = entries
        self._populate_topic_tree()

    def _populate_topic_tree(self) -> None:
        """Fill the topic tree from the loaded entries, honouring the topic
        search filter (substring match on the topic text only)."""
        self.topic_tree.delete(*self.topic_tree.get_children())
        if self._topic_placeholder is not None:
            self.topic_tree.insert(
                "", "end", text=self._topic_placeholder, values=("",))
            return

        needle = self.topic_filter_text.lower()
        count = 0
        for topic, page in self._topic_entries:
            if needle and needle not in topic.lower():
                continue
            # page is read back from the row's values on click; let Tk assign
            # the iid (topics/pages may repeat, so we can't use them as ids).
            self.topic_tree.insert("", "end", text=topic, values=(page,))
            count += 1

        if count == 0 and needle:
            self.topic_tree.insert("", "end", text="(no matches)", values=("",))

    def _populate_bookmark_tree(self) -> None:
        """Fill the bookmark list and attach/detach its pane: while the PDF
        has bookmarks it sits above the topics behind a draggable sash;
        without any, Topics takes the full height."""
        self.bm_tree.delete(*self.bm_tree.get_children())
        for i, bm in enumerate(self._bookmarks):
            # The list index rides in the iid so the right-click menu knows
            # which bookmark it targets.
            self.bm_tree.insert("", "end", iid=f"bm-{i}",
                                text=bm["title"], values=(bm["page"],))
        shown = str(self._bm_frame) in self._mid_split.panes()
        if self._bookmarks and not shown:
            self._mid_split.insert(0, self._bm_frame, weight=1)
            self.after_idle(self._place_bm_sash)
        elif not self._bookmarks and shown:
            self._bm_sash = self._mid_split.sashpos(0)  # keep the dragged spot
            self._mid_split.forget(self._bm_frame)

    def _place_bm_sash(self, retries: int = 20) -> None:
        """Put the bookmarks/topics sash where the user last dragged it, or
        at 25% of the split's height the first time the pane appears."""
        if str(self._bm_frame) not in self._mid_split.panes():
            return
        height = self._mid_split.winfo_height()
        if height <= 1:                   # split not laid out yet; retry
            # Bounded retry: give up (leaving Tk's default sash) rather
            # than reschedule forever if the split never gets a size.
            if retries > 0:
                self.after(50, lambda: self._place_bm_sash(retries - 1))
            return
        pos = self._bm_sash if self._bm_sash is not None else height // 4
        self._mid_split.sashpos(0, max(40, min(pos, height - 80)))

    def _on_topic_search_changed(self, *_args) -> None:
        self.topic_filter_text = self.topic_search_var.get().strip()
        self._populate_topic_tree()

    # -- Bookmarks -------------------------------------------------------------
    def add_bookmark(self) -> None:
        """Bookmark the current page under a user-chosen name."""
        if self.doc is None or self.current_pdf_path is None:
            return
        page = self.current_page + 1
        default = f"Page {page}"
        title = simpledialog.askstring("Add bookmark", "Bookmark name:",
                                       initialvalue=default, parent=self)
        if title is None:               # cancelled
            return
        title = title.strip() or default
        bookmark = {"page": page, "title": title}
        self._bookmarks.append(bookmark)
        self._bookmarks.sort(key=lambda b: b["page"])
        save_bookmarks(self.current_pdf_path, self._bookmarks)
        self._populate_bookmark_tree()
        self._recolor_pdf_row(self.current_pdf_path)  # first bookmark -> blue
        self._select_bookmark_row(self._bookmarks.index(bookmark))

    def _select_bookmark_row(self, index: int) -> None:
        """Highlight a bookmark's row so the user sees where it landed."""
        iid = f"bm-{index}"
        if self.bm_tree.exists(iid):
            self.bm_tree.selection_set(iid)
            self.bm_tree.see(iid)

    def on_bookmark_selected(self, _event=None) -> None:
        selection = self.bm_tree.selection()
        if not selection:
            return
        values = self.bm_tree.item(selection[0], "values")
        if not values or values[0] in ("", None):
            return
        try:
            page = int(values[0])
        except (ValueError, TypeError):
            return
        self.goto_page(page - 1)        # bookmark pages are 1-based

    def _on_bm_right_click(self, event) -> None:
        iid = self.bm_tree.identify_row(event.y)
        if not iid.startswith("bm-"):
            return
        self._bm_menu_index = int(iid.split("-", 1)[1])
        self.bm_tree.selection_set(iid)
        self.bookmark_menu.tk_popup(event.x_root, event.y_root)

    def _rename_bookmark(self) -> None:
        i = self._bm_menu_index
        if i is None or not 0 <= i < len(self._bookmarks):
            return
        title = simpledialog.askstring("Rename bookmark", "Bookmark name:",
                                       initialvalue=self._bookmarks[i]["title"],
                                       parent=self)
        if title is None or not title.strip():
            return
        self._bookmarks[i]["title"] = title.strip()
        save_bookmarks(self.current_pdf_path, self._bookmarks)
        self._populate_bookmark_tree()
        self._select_bookmark_row(i)

    def _delete_bookmark(self) -> None:
        i = self._bm_menu_index
        if i is None or not 0 <= i < len(self._bookmarks):
            return
        del self._bookmarks[i]
        save_bookmarks(self.current_pdf_path, self._bookmarks)
        self._populate_bookmark_tree()
        self._recolor_pdf_row(self.current_pdf_path)  # last one gone -> revert

    # -- Content search (text of the open PDF) --------------------------------
    def _on_content_search_changed(self, *_args) -> None:
        # Debounce: searching every page on each keystroke would stutter.
        if self._content_search_after is not None:
            self.after_cancel(self._content_search_after)
        self._content_search_after = self.after(300, self._run_content_search)

    def _on_content_return(self, _event=None) -> str:
        """Enter runs a pending search immediately, otherwise steps to the
        next match."""
        if self._content_search_after is not None:
            self.after_cancel(self._content_search_after)
            self._content_search_after = None
            self._run_content_search()
        else:
            self.next_match()
        return "break"

    def _run_content_search(self, jump: bool = True) -> None:
        self._content_search_after = None
        self._cancel_content_scan()
        self.content_matches = []
        self.content_match_idx = -1
        needle = self.content_search_var.get().strip()
        if self.doc is None or not needle:
            self._update_match_label()
            if self.doc is not None:
                self.render_page()   # drop any old highlights
            return
        self._content_scan_active = True
        self._content_scan_needle = needle
        self._content_scan_page = 0
        self._content_scan_jump = jump
        self._update_match_label()
        self.render_page()           # drop highlights from the old query
        self._content_scan_chunk()

    def _cancel_content_scan(self) -> None:
        if self._content_scan_after is not None:
            self.after_cancel(self._content_scan_after)
            self._content_scan_after = None
        self._content_scan_active = False

    def _content_scan_chunk(self, pages_per_chunk: int = 50) -> None:
        """Scan the next slice of pages for the current needle, then yield to
        the event loop and reschedule -- keeps the UI live on huge PDFs."""
        self._content_scan_after = None
        if self.doc is None or not self._content_scan_active:
            return
        end = min(self._content_scan_page + pages_per_chunk,
                  self.doc.page_count)
        found_on_current = False
        for pno in range(self._content_scan_page, end):
            for rect in self.doc[pno].search_for(self._content_scan_needle):
                self.content_matches.append((pno, rect))
                found_on_current |= pno == self.current_page
        self._content_scan_page = end
        done = end >= self.doc.page_count

        if self._content_scan_jump:
            # Jump once we know the first hit on/after the page we started
            # on; if the whole scan ends without one, wrap to the first hit.
            idx = next((i for i, (p, _) in enumerate(self.content_matches)
                        if p >= self.current_page), None)
            if idx is None and done and self.content_matches:
                idx = 0
            if idx is not None:
                self._content_scan_jump = False
                if done:
                    self._content_scan_active = False
                self._select_match(idx)
        elif found_on_current:
            self.render_page()       # paint new highlights on the shown page

        if done:
            self._content_scan_active = False
            self._update_match_label()
        else:
            self._update_match_label()
            self._content_scan_after = self.after(1, self._content_scan_chunk)

    def next_match(self) -> None:
        self._step_match(1)

    def prev_match(self) -> None:
        self._step_match(-1)

    def _step_match(self, delta: int) -> None:
        if not self.content_matches:
            return
        if self.content_match_idx == -1:
            # First jump goes to the nearest hit on/after the current page.
            idx = next((i for i, (p, _) in enumerate(self.content_matches)
                        if p >= self.current_page), 0)
            if delta < 0:
                idx = (idx - 1) % len(self.content_matches)
        else:
            idx = (self.content_match_idx + delta) % len(self.content_matches)
        self._select_match(idx)

    def _select_match(self, idx: int) -> None:
        self.content_match_idx = idx
        pno, rect = self.content_matches[idx]
        self.goto_page(pno)          # re-renders, drawing the highlights
        self._update_match_label()
        self._scroll_match_into_view(rect)

    def _update_match_label(self) -> None:
        if not self.content_search_var.get().strip():
            self.match_var.set("")
            return
        n = len(self.content_matches)
        if not self._content_scan_active:
            pos = self.content_match_idx + 1 if self.content_match_idx >= 0 \
                else ("–" if n else 0)
            self.match_var.set(f"{pos} / {n}")
        elif n == 0:
            self.match_var.set("…")   # scan under way, nothing found yet
        else:
            pos = "–" if self.content_match_idx < 0 \
                else self.content_match_idx + 1
            self.match_var.set(f"{pos} / {n}…")

    def _scroll_match_into_view(self, rect) -> None:
        """Scroll the canvas so the match sits about a third of the way down
        the view (render_page resets the scroll to the top)."""
        if self.doc is None:
            return
        page = self.doc[self.current_page]
        page_w = (page.rect.width or 1) * self.zoom
        page_h = (page.rect.height or 1) * self.zoom
        view_w = self.canvas.winfo_width() or 1
        view_h = self.canvas.winfo_height() or 1
        if page_h > view_h:
            frac = (rect.y0 * self.zoom - view_h / 3) / page_h
            self.canvas.yview_moveto(max(0.0, min(1.0, frac)))
        if page_w > view_w:
            frac = (rect.x0 * self.zoom - view_w / 3) / page_w
            self.canvas.xview_moveto(max(0.0, min(1.0, frac)))

    def _draw_match_highlights(self) -> None:
        """Overlay match rectangles on the rendered page (canvas coordinates
        are PDF points scaled by the current zoom)."""
        z = self.zoom
        for i, (pno, rect) in enumerate(self.content_matches):
            if pno != self.current_page:
                continue
            current = i == self.content_match_idx
            self.canvas.create_rectangle(
                rect.x0 * z, rect.y0 * z, rect.x1 * z, rect.y1 * z,
                outline="#e07000" if current else "#c8a800",
                width=2 if current else 1,
                fill="#ffd000", stipple="gray25")

    # -- Text selection & highlight annotations --------------------------------
    def _on_select_start(self, event) -> None:
        if self.doc is None:
            return
        self._select_anchor = (self.canvas.canvasx(event.x),
                               self.canvas.canvasy(event.y))
        self._select_drag = None
        if self._selected_rects:          # a plain click clears the selection
            self._selected_rects = []
            self.canvas.delete("selection")
            self._update_annot_buttons()

    def _on_select_drag(self, event) -> None:
        if self.doc is None or self._select_anchor is None:
            return
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        self._select_drag = (x, y)
        ax, ay = self._select_anchor
        self.canvas.delete("selband")     # rubber-band the drag area
        self.canvas.create_rectangle(ax, ay, x, y, outline="#3b78d8",
                                     dash=(3, 2), tags="selband")

    def _on_select_end(self, _event) -> None:
        self.canvas.delete("selband")
        anchor, self._select_anchor = self._select_anchor, None
        drag, self._select_drag = self._select_drag, None
        if self.doc is None or anchor is None or drag is None:
            return
        z = self.zoom
        x0, x1 = sorted((anchor[0], drag[0]))
        y0, y1 = sorted((anchor[1], drag[1]))
        sel = fitz.Rect(x0 / z, y0 / z, x1 / z, y1 / z)
        words = self.doc[self.current_page].get_text("words")
        self._selected_rects = [fitz.Rect(w[:4]) for w in words
                                if fitz.Rect(w[:4]).intersects(sel)]
        self._draw_selection()
        self._update_annot_buttons()

    def _draw_selection(self) -> None:
        self.canvas.delete("selection")
        z = self.zoom
        for r in self._selected_rects:
            self.canvas.create_rectangle(
                r.x0 * z, r.y0 * z, r.x1 * z, r.y1 * z,
                outline="", fill="#3b78d8", stipple="gray50",
                tags="selection")

    def _update_annot_buttons(self) -> None:
        self.highlight_btn.config(
            state="normal" if self._selected_rects else "disabled")
        self.save_btn.config(
            state="normal" if self._annots_dirty else "disabled")

    def highlight_selection(self) -> None:
        if self.doc is None or not self._selected_rects:
            return
        page = self.doc[self.current_page]
        annot = page.add_highlight_annot(self._selected_rects)
        annot.update()
        self._selected_rects = []
        self._annots_dirty = True
        self._update_annot_buttons()
        self.render_page()                # annotation shows on the repaint

    @staticmethod
    def _find_highlight(page, pt: tuple[float, float]):
        """The highlight annotation under a PDF-space point, or None.

        The caller must keep `page` alive while using the returned annot --
        a PyMuPDF Annot dies with its owning Page object."""
        for annot in page.annots(types=(fitz.PDF_ANNOT_HIGHLIGHT,)):
            if annot.rect.contains(fitz.Point(*pt)):
                return annot
        return None

    def _on_canvas_right_click(self, event) -> None:
        if self.doc is None:
            return
        self._canvas_menu_pt = (self.canvas.canvasx(event.x) / self.zoom,
                                self.canvas.canvasy(event.y) / self.zoom)
        page = self.doc[self.current_page]
        self.canvas_menu.entryconfig(
            0, state="normal" if self._selected_rects else "disabled")
        self.canvas_menu.entryconfig(
            1, state="normal"
            if self._find_highlight(page, self._canvas_menu_pt) else "disabled")
        self.canvas_menu.tk_popup(event.x_root, event.y_root)

    def _remove_highlight_here(self) -> None:
        if self.doc is None or self._canvas_menu_pt is None:
            return
        page = self.doc[self.current_page]
        annot = self._find_highlight(page, self._canvas_menu_pt)
        if annot is None:
            return
        page.delete_annot(annot)
        self._annots_dirty = True
        self._update_annot_buttons()
        self.render_page()

    def save_annotations(self) -> None:
        if self.doc is None or not self._annots_dirty:
            return
        src = self.current_pdf_path or ""
        base, ext = os.path.splitext(src)
        ann_path = base + "(ann)" + ext
        save_copy = False
        # An "(ann)" copy is already the annotated file -- save it in place
        # without asking (avoids piling up "(ann)(ann)" names).
        if not base.endswith("(ann)"):
            choice = messagebox.askyesnocancel(
                "Save highlights",
                "Save highlights as a separate copy?\n\n"
                f"Yes  –  save as {os.path.basename(ann_path)}\n"
                f"No  –  save into {os.path.basename(src)}")
            if choice is None:
                return                    # cancelled; keep changes pending
            save_copy = choice

        if save_copy:
            try:
                self.doc.save(ann_path)   # full write to the new file
            except (OSError, RuntimeError, ValueError) as exc:
                messagebox.showerror(
                    "Save failed",
                    f"Could not save\n{ann_path}:\n\n{exc}")
                return
            # Give the copy the same topics file so it lists cleanly.
            meta = find_metadata_path(src)
            if meta is not None:
                ann_meta = base + "(ann)" + os.path.splitext(meta)[1]
                if not os.path.exists(ann_meta):
                    try:
                        shutil.copyfile(meta, ann_meta)
                    except OSError:
                        pass              # copy still saved; just no topics
            # ...and the same bookmarks sidecar, if any.
            bm = bookmarks_path(src)
            if os.path.isfile(bm):
                ann_bm = bookmarks_path(ann_path)
                if not os.path.exists(ann_bm):
                    try:
                        shutil.copyfile(bm, ann_bm)
                    except OSError:
                        pass              # copy still saved; just no bookmarks
            self.refresh_pdf_list()       # show the new (ann) file
        else:
            try:
                signed = (self.doc.get_sigflags() or 0) > 0
            except (RuntimeError, ValueError):
                signed = False
            if signed and not messagebox.askyesno(
                    "Signed PDF",
                    "This PDF is digitally signed; saving highlights may "
                    "invalidate the signature.\n\nSave anyway?"):
                return
            try:
                self.doc.saveIncr()       # append-only update, fast
            except (OSError, RuntimeError, ValueError) as exc:
                messagebox.showerror(
                    "Save failed",
                    f"Could not save highlights to\n{src}:\n\n{exc}")
                return
        self._annots_dirty = False
        self._update_annot_buttons()

    def _maybe_save_annots(self) -> None:
        """Offer to keep unsaved highlights before the document goes away."""
        if self.doc is None or not self._annots_dirty:
            return
        name = os.path.basename(self.current_pdf_path or "this document")
        if messagebox.askyesno(
                "Unsaved highlights",
                f"{name} has unsaved highlights.\n\nSave them?"):
            self.save_annotations()
        self._annots_dirty = False        # saved or deliberately discarded
        self._update_annot_buttons()

    def on_topic_selected(self, _event=None) -> None:
        selection = self.topic_tree.selection()
        if not selection:
            return
        values = self.topic_tree.item(selection[0], "values")
        if not values or values[0] in ("", None):
            return
        try:
            page = int(values[0])
        except (ValueError, TypeError):
            return
        self.goto_page(page - 1)  # metadata pages are 1-based

    # -- Viewer ---------------------------------------------------------------
    def load_pdf(self, pdf_path: str) -> None:
        if fitz is None:
            self._render_message("PyMuPDF not installed.\n"
                                 "Run:  pip install pymupdf pillow")
            return
        if Image is None:
            self._render_message("Pillow not installed.\n"
                                 "Run:  pip install pymupdf pillow")
            return
        if pdf_path == self.current_pdf_path:
            return
        # Stop any in-flight content scan before the document goes away, and
        # offer to keep unsaved highlights.
        self._cancel_content_scan()
        self._maybe_save_annots()
        self._selected_rects = []
        try:
            if self.doc is not None:
                self.doc.close()
            self.doc = fitz.open(pdf_path)
        except (OSError, RuntimeError, ValueError) as exc:
            self.doc = None
            self._render_message(f"Could not open PDF:\n{exc}")
            return
        self.current_pdf_path = pdf_path
        # Restore the page we were last on for this PDF (clamped to range).
        saved_page = load_config().get("last_pages", {}).get(_page_key(pdf_path))
        self.current_page = 0
        if isinstance(saved_page, int):
            self.current_page = max(0, min(saved_page, self.doc.page_count - 1))
        self.fit_mode = self.fit_pref   # open in the last-used fit preference
        self.render_page()
        update_config({"last_pdf": pdf_path})
        # Re-run an active content search against the new document, but stay
        # on the restored page (matches highlight; ▼/Enter jumps to one).
        if self.content_search_var.get().strip():
            self._run_content_search(jump=False)
        elif self.content_matches:
            self.content_matches = []      # drop hits from the previous PDF
            self.content_match_idx = -1
            self._update_match_label()

    def goto_page(self, page_index: int) -> None:
        if self.doc is None:
            return
        page_index = max(0, min(page_index, self.doc.page_count - 1))
        changed = page_index != self.current_page
        self.current_page = page_index
        if changed and self._selected_rects:
            self._selected_rects = []     # selection was on the old page
            self._update_annot_buttons()
        self.render_page()
        # No _maybe_snap() here: paging through a single PDF must not resize
        # the window.  Full-page mode still re-fits each page to the current
        # window (via render_page -> _apply_fit); the window only snaps on the
        # Full page button and on user window resizes.
        if changed and self.current_pdf_path:
            self._save_last_page(self.current_pdf_path, page_index)

    def _save_last_page(self, pdf_path: str, page_index: int) -> None:
        cfg = load_config()
        pages = cfg.get("last_pages", {})
        if not isinstance(pages, dict):
            pages = {}
        # Dicts keep insertion order (and JSON round-trips it), so removing
        # the key before re-adding keeps the map ordered oldest-first --
        # letting the cap below prune the least recently viewed PDFs.
        key = _page_key(pdf_path)
        pages.pop(key, None)
        pages[key] = page_index
        while len(pages) > MAX_REMEMBERED_PAGES:
            pages.pop(next(iter(pages)))
        update_config({"last_pages": pages})

    def prev_page(self) -> None:
        self.goto_page(self.current_page - 1)

    def next_page(self) -> None:
        self.goto_page(self.current_page + 1)

    def change_zoom(self, factor: float) -> None:
        self.fit_mode = None   # manual zoom overrides any active fit
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, self.zoom * factor))
        self.render_page()

    def fit_width(self) -> None:
        self._set_fit_pref("width")

    def fit_page(self) -> None:
        self._set_fit_pref("page")

    def _set_fit_pref(self, mode: str) -> None:
        """Apply a fit mode, remember it, and persist it for future runs."""
        self.fit_mode = self.fit_pref = mode
        update_config({"fit_pref": mode})
        self.render_page()
        self._maybe_snap()

    def _on_canvas_configure(self, event) -> None:
        """Re-apply the active fit once a window/pane resize has settled.

        <Configure> fires continuously while dragging, so debounce: reschedule
        a one-shot render and only run it after the size stops changing.  A
        manual (+/-) zoom has no fit mode and is left untouched."""
        size = (event.width, event.height)
        if size == self._last_canvas_size:
            return
        self._last_canvas_size = size
        if self.doc is None or not self.fit_mode:
            return
        if self._resize_after is not None:
            self.after_cancel(self._resize_after)
        self._resize_after = self.after(150, self._refit_after_resize)

    def _refit_after_resize(self) -> None:
        self._resize_after = None
        if self.doc is None or not self.fit_mode:
            return
        if self.fit_mode == "page" and self._fitted_canvas_size:
            # The dimension the user dragged drives the zoom; the window
            # snap then re-fits the other dimension to the page.
            self.canvas.update_idletasks()
            ow, oh = self._fitted_canvas_size
            dw = self.canvas.winfo_width() - ow
            dh = self.canvas.winfo_height() - oh
            self._fit_master = "w" if abs(dw) >= abs(dh) else "h"
        try:
            self.render_page()   # _apply_fit recomputes zoom for the new size
            self._maybe_snap()
        finally:
            self._fit_master = None

    def _apply_fit(self) -> None:
        """When a fit mode is active, recompute self.zoom from the current
        canvas size and page dimensions (keeps fit across page changes)."""
        if self.doc is None or not self.fit_mode:
            return
        self.canvas.update_idletasks()   # ensure the canvas has been laid out
        page = self.doc[self.current_page]
        pad = 8
        canvas_w = self.canvas.winfo_width() or 600
        page_w = page.rect.width or 1
        zoom = (canvas_w - pad) / page_w
        if self.fit_mode == "page":
            canvas_h = self.canvas.winfo_height() or 800
            page_h = page.rect.height or 1
            zoom_h = (canvas_h - pad) / page_h
            if self._fit_master == "h":
                zoom = zoom_h            # user dragged height: height rules
            elif self._fit_master != "w":
                zoom = min(zoom, zoom_h)  # no drag in play: whole page fits
            self._fitted_canvas_size = (canvas_w, canvas_h)
        self.zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))

    def render_page(self) -> None:
        if self.doc is None:
            return
        self._apply_fit()
        page = self.doc[self.current_page]
        matrix = fitz.Matrix(self.zoom, self.zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        self._photo = ImageTk.PhotoImage(image)

        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self._draw_match_highlights()
        self._draw_selection()
        self.canvas.config(scrollregion=(0, 0, pix.width, pix.height))
        self.canvas.yview_moveto(0)
        self.page_var.set(
            f"Page {self.current_page + 1} / {self.doc.page_count}")
        self._last_pix = (pix.width, pix.height)

    def _maybe_snap(self) -> None:
        """Snap the window to the page in full-page mode.  Called from the
        actions that should resize the window (Full page button, user window
        resizes) -- deliberately NOT from every render, nor from page turns,
        so scrolling through a single PDF (or selecting a different PDF) never
        moves the window."""
        if self.fit_mode == "page" and self.doc is not None and self._last_pix:
            self._snap_window_to_page(*self._last_pix)

    def _snap_window_to_page(self, page_w: int, page_h: int) -> None:
        """Full-page mode: resize the toplevel so the canvas hugs the rendered
        page instead of leaving gray slack around it.

        Targets page size plus the fit padding so _apply_fit recomputes the
        same zoom afterwards (otherwise each snap would shrink the page by the
        pad).  The geometry change fires the debounced refit, which renders
        and snaps again -- so any part of the resize absorbed by the other
        panes is corrected over a few quick passes."""
        top = self.winfo_toplevel()
        if top.state() != "normal":
            return                      # don't fight a maximized window
        self.canvas.update_idletasks()
        pad = 8                         # keep in sync with _apply_fit
        dw = (page_w + pad) - self.canvas.winfo_width()
        dh = (page_h + pad) - self.canvas.winfo_height()
        if abs(dw) <= 2 and abs(dh) <= 2:
            return                      # close enough -- avoid resize loops
        new_w = max(480, min(top.winfo_width() + dw, top.winfo_screenwidth()))
        new_h = max(360, min(top.winfo_height() + dh,
                             top.winfo_screenheight()))
        if (new_w, new_h) != (top.winfo_width(), top.winfo_height()):
            # The WM resize redistributes width across all panes (by weight),
            # so the canvas only receives part of the change.  Remember the
            # sash positions; the follow-up pass restores them, putting the
            # whole change on the viewer so it lands in one correction.
            n_sashes = len(self.panes.panes()) - 1
            self._saved_sashes = [self.panes.sashpos(i)
                                  for i in range(n_sashes)]
            top.geometry(f"{new_w}x{new_h}")
            if self._snap_after is not None:
                self.after_cancel(self._snap_after)
            self._snap_after = self.after(180, self._snap_again)

    def _snap_again(self) -> None:
        self._snap_after = None
        if self.doc is None or self.fit_mode != "page":
            self._saved_sashes = None
            return
        for i, pos in enumerate(self._saved_sashes or []):
            try:
                self.panes.sashpos(i, pos)
            except tk.TclError:
                pass
        self._saved_sashes = None
        self.render_page()       # re-fits to the new size...
        self._maybe_snap()       # ...and snaps again if still off

    def _render_message(self, message: str) -> None:
        self.canvas.delete("all")
        self.canvas.create_text(
            20, 20, anchor="nw", fill="white", text=message,
            font=("Segoe UI", 11))
        self.page_var.set("—")

    def _on_mousewheel(self, event) -> None:
        # Only handle the wheel when the pointer is over the viewer canvas;
        # otherwise let the PDF/topic trees keep their own wheel scrolling.
        if self.winfo_containing(event.x_root, event.y_root) is not self.canvas:
            return None
        if self.doc is None:
            return "break"

        down = event.delta < 0                       # wheel down = go forward
        top, bottom = self.canvas.yview()
        if down and bottom >= 0.999:                 # at bottom -> next page
            if self.current_page < self.doc.page_count - 1:
                self.goto_page(self.current_page + 1)   # lands at page top
            return "break"
        if not down and top <= 0.001:                # at top -> previous page
            if self.current_page > 0:
                self.goto_page(self.current_page - 1)
                self.canvas.update_idletasks()
                self.canvas.yview_moveto(1.0)           # show its bottom
            return "break"

        self.canvas.yview_scroll(-3 if not down else 3, "units")
        return "break"


# ----------------------------------------------------------------------------
# Minimal Markdown -> Tk Text renderer (for the Help window)
# ----------------------------------------------------------------------------
# Kept dependency-free on purpose: handles headings, bold/italic/inline-code,
# links, bullet/numbered lists, fenced code blocks, horizontal rules, and
# simple pipe tables. Anything fancier is rendered as plain text.

_MD_INLINE = re.compile(
    r"(?P<code>`[^`]+`)"
    r"|(?P<bold>\*\*[^*]+\*\*)"
    r"|(?P<italic>\*[^*\s][^*]*\*)"
    r"|(?P<link>\[[^\]]+\]\([^)]+\))")
_MD_TABLE_SEP = re.compile(r"^\s*:?-{2,}:?\s*$")


def _configure_help_tags(text: "tk.Text") -> None:
    base = "Segoe UI"
    text.tag_configure("h1", font=(base, 18, "bold"), spacing1=10, spacing3=6)
    text.tag_configure("h2", font=(base, 14, "bold"), spacing1=10, spacing3=4)
    text.tag_configure("h3", font=(base, 12, "bold"), spacing1=8, spacing3=3)
    text.tag_configure("bold", font=(base, 11, "bold"))
    text.tag_configure("italic", font=(base, 11, "italic"))
    text.tag_configure("code", font=("Consolas", 10), background="#f0f0f0")
    text.tag_configure("codeblock", font=("Consolas", 10),
                       background="#f5f5f5", lmargin1=24, lmargin2=24,
                       spacing1=1, spacing3=1)
    text.tag_configure("bullet", lmargin1=20, lmargin2=36)
    text.tag_configure("rule", foreground="#bbbbbb", spacing1=4, spacing3=6)
    text.tag_configure("link", foreground="#0a58ca", underline=True)


def _md_inline(text: "tk.Text", line: str, base_tags: tuple) -> None:
    """Insert one line, applying inline bold/italic/code/link formatting."""
    pos = 0
    for m in _MD_INLINE.finditer(line):
        if m.start() > pos:
            text.insert("end", line[pos:m.start()], base_tags)
        if m.group("code"):
            text.insert("end", m.group()[1:-1], base_tags + ("code",))
        elif m.group("bold"):
            text.insert("end", m.group()[2:-2], base_tags + ("bold",))
        elif m.group("italic"):
            text.insert("end", m.group()[1:-1], base_tags + ("italic",))
        elif m.group("link"):
            label, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", m.group()).groups()
            tag = f"link-{id(m)}-{m.start()}"
            text.insert("end", label, base_tags + ("link", tag))
            text.tag_bind(tag, "<Button-1>",
                          lambda e, u=url: webbrowser.open(u))
            text.tag_bind(tag, "<Enter>",
                          lambda e: text.config(cursor="hand2"))
            text.tag_bind(tag, "<Leave>",
                          lambda e: text.config(cursor="arrow"))
        pos = m.end()
    if pos < len(line):
        text.insert("end", line[pos:], base_tags)
    text.insert("end", "\n")


def _md_table(text: "tk.Text", rows: list[str]) -> None:
    """Render a block of pipe-table lines as aligned monospace text."""
    parsed = []
    for row in rows:
        cells = [c.strip().replace("`", "")
                 for c in row.strip().strip("|").split("|")]
        if all(_MD_TABLE_SEP.match(c) or not c for c in cells):
            continue  # the |---|---| separator row
        parsed.append(cells)
    if not parsed:
        return
    ncol = max(len(r) for r in parsed)
    widths = [0] * ncol
    for r in parsed:
        for j, c in enumerate(r):
            widths[j] = max(widths[j], len(c))
    for r in parsed:
        padded = "  ".join(c.ljust(widths[j]) for j, c in enumerate(r))
        text.insert("end", padded + "\n", ("codeblock",))
    text.insert("end", "\n")


def _md_is_block_start(line: str) -> bool:
    """True if `line` begins a new block (so it can't be lazy continuation)."""
    s = line.strip()
    return (not s or s.startswith("```") or s.startswith("#")
            or s in ("---", "***", "___") or "|" in line
            or re.match(r"^\s*[-*]\s+", line) is not None
            or re.match(r"^\s*\d+\.\s+", line) is not None)


def _render_markdown(text: "tk.Text", md: str) -> None:
    _configure_help_tags(text)
    lines = md.split("\n")
    i, n = 0, len(md.split("\n"))

    def gather_continuation(start: int) -> tuple:
        """Join following non-blank, non-block-start lines (lazy wrapping)."""
        parts, j = [], start
        while j < n and lines[j].strip() and not _md_is_block_start(lines[j]):
            parts.append(lines[j].strip())
            j += 1
        return " ".join(parts), j

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):                 # fenced code block
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                text.insert("end", lines[i] + "\n", ("codeblock",))
                i += 1
            i += 1                                      # skip closing fence
            continue
        if not stripped:
            text.insert("end", "\n")
            i += 1
            continue
        if stripped in ("---", "***", "___"):
            text.insert("end", "─" * 50 + "\n", ("rule",))
            i += 1
            continue
        if stripped.startswith("### "):
            _md_inline(text, stripped[4:], ("h3",))
            i += 1
            continue
        if stripped.startswith("## "):
            _md_inline(text, stripped[3:], ("h2",))
            i += 1
            continue
        if stripped.startswith("# "):
            _md_inline(text, stripped[2:], ("h1",))
            i += 1
            continue
        if "|" in line:                                 # table block
            block = []
            while i < len(lines) and "|" in lines[i]:
                block.append(lines[i])
                i += 1
            _md_table(text, block)
            continue
        bullet = re.match(r"^\s*[-*]\s+(.*)", line)
        if bullet:
            rest, i = gather_continuation(i + 1)
            content = bullet.group(1) + ((" " + rest) if rest else "")
            text.insert("end", "•  ", ("bullet",))
            _md_inline(text, content, ("bullet",))
            continue
        numbered = re.match(r"^\s*(\d+\.)\s+(.*)", line)
        if numbered:
            rest, i = gather_continuation(i + 1)
            content = numbered.group(2) + ((" " + rest) if rest else "")
            text.insert("end", numbered.group(1) + "  ", ("bullet",))
            _md_inline(text, content, ("bullet",))
            continue
        rest, i = gather_continuation(i + 1)             # paragraph
        para = stripped + ((" " + rest) if rest else "")
        _md_inline(text, para, ())


def _page_key(pdf_path: str) -> str:
    """Stable per-PDF key for the last-page map (case/relpath insensitive)."""
    return os.path.normcase(os.path.abspath(pdf_path))


def _open_path(path: str) -> None:
    """Open `path` (a file or folder) with the OS default handler.

    Cross-platform replacement for os.startfile, which exists only on Windows.
    Raises OSError on failure so callers can report it."""
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]  # Windows only
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=True)
    else:
        subprocess.run(["xdg-open", path], check=True)


def _reveal_path(path: str) -> None:
    """Show `path` in the OS file manager, selected if possible.

    Raises OSError on failure so callers can report it."""
    if sys.platform == "win32":
        if os.path.isdir(path):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            # explorer's /select wants the path as one quoted token.
            subprocess.run(f'explorer /select,"{os.path.normpath(path)}"')
        return
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", path], check=True)
        return
    # Linux/BSD: the freedesktop FileManager1 D-Bus interface selects the item
    # in Nautilus/Dolphin/Nemo/etc.  Fall back to opening the folder when no
    # such file manager is running (or dbus-send is missing).
    uri = "file://" + urllib.request.pathname2url(os.path.abspath(path))
    try:
        subprocess.run(
            ["dbus-send", "--session", "--print-reply",
             "--dest=org.freedesktop.FileManager1", "--type=method_call",
             "/org/freedesktop/FileManager1",
             "org.freedesktop.FileManager1.ShowItems",
             f"array:string:{uri}", "string:"],
            check=True, capture_output=True, timeout=5)
        return
    except (OSError, subprocess.SubprocessError):
        pass
    target = path if os.path.isdir(path) else os.path.dirname(path)
    subprocess.run(["xdg-open", target], check=True)


def _config_path() -> str:
    """Per-user settings file (persists across runs, incl. the frozen exe).

    The folder stays "PDFGuide" (the app's former name) so settings saved
    before the rename to PDF Sherpa keep working."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        # Follow the XDG base-dir spec on Linux/macOS (~/.config by default).
        base = (os.environ.get("XDG_CONFIG_HOME")
                or os.path.join(os.path.expanduser("~"), ".config"))
    return os.path.join(base, "PDFGuide", "config.json")


def load_config() -> dict:
    try:
        with open(_config_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(settings: dict) -> None:
    try:
        path = _config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(settings, fh)
    except OSError:
        pass  # settings are a convenience -- never crash on a write failure


def update_config(updates: dict) -> None:
    """Merge `updates` into the saved settings (preserves other keys)."""
    cfg = load_config()
    cfg.update(updates)
    save_config(cfg)


def _resource_path(name: str) -> str:
    """Locate a bundled resource, whether running from source or frozen."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def _apply_window_icon(win) -> None:
    """Give a window a title-bar/taskbar icon on any platform.

    Windows uses the .ico via iconbitmap; everywhere else Tk ignores .ico, so
    fall back to the PNG via iconphoto (needs Pillow).  Never fatal."""
    if sys.platform == "win32":
        ico = _resource_path("pdfboss.ico")
        if os.path.isfile(ico):
            try:
                win.iconbitmap(ico)
                return
            except tk.TclError:
                pass
    png = _resource_path("pdfboss.png")
    if Image is not None and ImageTk is not None and os.path.isfile(png):
        try:
            img = Image.open(png)
            img.thumbnail((256, 256))
            photo = ImageTk.PhotoImage(img)
            win.iconphoto(True, photo)
            win._icon_photo = photo  # keep a reference so Tk doesn't GC it
        except Exception:
            pass


def _running_portable() -> bool:
    """True when this frozen exe is a loose portable copy.

    The installed copy always has Inno Setup's uninstaller (unins*.exe)
    sitting beside it; a downloaded portable exe does not.  Source checkouts
    return False (the update prompt already special-cases non-frozen runs).
    When in doubt, prefer portable: an in-place exe swap is less invasive
    than silently running the installer.
    """
    if not getattr(sys, "frozen", False):
        return False
    try:
        names = os.listdir(os.path.dirname(sys.executable))
    except OSError:
        return True
    return not any(n.lower().startswith("unins") and n.lower().endswith(".exe")
                   for n in names)


def _running_appimage() -> str | None:
    """Path to the running AppImage, or None when not launched from one.

    The AppImage runtime exports ``APPIMAGE`` (the outer .AppImage path) to the
    program it launches; its presence is how we know an in-place self-update is
    possible on Linux."""
    path = os.environ.get("APPIMAGE")
    return path if path and os.path.isfile(path) else None


def _parse_version(text: str) -> tuple[int, ...] | None:
    """'v1.3.3' / '1.3.3' -> (1, 3, 3); None if malformed (e.g. a beta tag)."""
    try:
        return tuple(int(part) for part in text.lstrip("vV").split("."))
    except ValueError:
        return None


def _fetch_latest_release() -> dict:
    """Query GitHub for the newest release.  Blocking -- worker thread only.

    Returns {"version": tuple, "version_str": str, "asset_url": str | None,
    "portable_url": str | None, "html_url": str}.  Raises on any failure
    (network, JSON, bad tag); the caller decides whether that is silent
    (auto check) or reported (manual).
    """
    req = urllib.request.Request(
        UPDATE_API_URL,
        headers={"User-Agent": "PDFSherpa",
                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    tag = data.get("tag_name", "")
    version = _parse_version(tag)
    if version is None:
        raise ValueError(f"unrecognized release tag: {tag!r}")
    asset_url = None
    portable_url = None
    appimage_url = None
    fallback_exe = None
    for asset in data.get("assets", []):
        name = str(asset.get("name", ""))
        url = asset.get("browser_download_url")
        if name == UPDATE_ASSET_NAME:
            asset_url = url
        elif name == UPDATE_PORTABLE_ASSET_NAME:
            portable_url = url
        elif name == UPDATE_APPIMAGE_ASSET_NAME:
            appimage_url = url
        elif fallback_exe is None and name.endswith(".exe"):
            fallback_exe = url
    return {"version": version,
            "version_str": tag.lstrip("vV"),
            "asset_url": asset_url or fallback_exe,
            "portable_url": portable_url,
            "appimage_url": appimage_url,
            "html_url": data.get(
                "html_url",
                "https://github.com/Flinterpop/PDF_Sherpa/releases/latest")}


def _default_folder() -> str:
    """Default to a 'pdfs' folder next to the app (works when frozen too)."""
    if getattr(sys, "frozen", False):          # bundled by PyInstaller
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "pdfs")


def main() -> None:
    # Folder precedence: command line, then the last folder chosen via
    # "Choose folder..." (if it still exists -- it may be on an unplugged
    # drive), then the default pdfs folder next to the app.
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    else:
        saved = load_config().get("folder")
        folder = (saved if isinstance(saved, str) and os.path.isdir(saved)
                  else _default_folder())

    root = tk.Tk()
    root.title(f"PDF Sherpa - V{APP_VERSION}")
    root.geometry("1100x720")

    _apply_window_icon(root)

    if fitz is None or Image is None:
        messagebox.showwarning(
            "Missing dependencies",
            "The embedded viewer needs PyMuPDF and Pillow.\n\n"
            "Install them with:\n    pip install pymupdf pillow\n\n"
            "The app will still list PDFs and topics.")

    PDFSherpaApp(root, folder)
    root.mainloop()


if __name__ == "__main__":
    main()
