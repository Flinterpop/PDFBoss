# PDFBoss — Help

*Last updated: 16 Aug 2026*

Vibe Coded with Claude Code using Fable 5 

B. Graham - July 2026

PDFBoss lets you browse a folder of PDFs **by topic** and read them in a built-in viewer.

## The three panes

- **PDFs** (left) — every PDF found under your top-level folders, each shown as its own heading with subfolders nested beneath it. Click one to open it. The search box filters the list as you type. The list is built in the background, so the window is usable straight away and the status bar reads *Scanning folders…* until it is ready; on a folder held on a network share that can take a moment. The document you were last reading opens immediately rather than waiting for the scan, and its row is selected once the list appears.
  Right-click a PDF for **Open PDF** (in your default viewer), **Reveal in Explorer**, or **Add to favorites**; right-click a folder for **Show as flat list** or to open it in Explorer.
  **Show as flat list** collapses a folder's structure: it then lists every PDF beneath it, at any depth, with no subfolder rows — handy when you know the filename but not which subfolder it is in. It works on any folder, top-level or not, and each folder remembers its own setting, so you can flatten one and leave its neighbour as a tree. A flattened folder is marked `(flat)`; untick to get the tree back.
  A **Favorites** list sits above the search box: up to 10 pinned PDFs (newest first) that you can open in one click. Each row shows the PDF's path
  relative to the top-level folder it sits under, and favorites are stored that way — so they keep working if you move or rename that folder
  (a PDF outside every top-level folder is kept as an absolute path). Right-click a
  favorite to open it, reveal it, or remove it. Favorites are remembered across runs, and a favorite that also has bookmarks shows in blue. The **⋯**
  button beside the *Favorites* heading lets you **clear** the list, or **export** it to / **import** it from a JSON file (handy for moving your
  favorites to another machine); importing can either merge with or replace your current list.
- **Topics** (middle) — the selected PDF's topic list. Click a topic to jump straight to that page. The search box filters the topics as you type. If the
  PDF has bookmarks, they appear in their own list above the topics (see **Bookmarks** below).
- **Viewer** (right) — the embedded page view, with a search box that finds text inside the open PDF (see **Searching** below).

The **PDFs** and **Topics** buttons in the toolbar collapse and restore the left and middle panes — turn both off for a full-width reading view. Your
choice is remembered across runs, and hidden panes keep their state (selection, search text) while collapsed.

## Adding PDFs

**Drag and drop** one or more PDF files anywhere onto the window. Each is copied into an `inbox` subfolder of the top-level folder holding the currently selected PDF (or the first one, when nothing is selected), a topics file is generated automatically, and the last one is selected and opened for you.

If a file of that name is already in the inbox you are asked whether to replace it, and all of those questions are asked up front — the copying and indexing then run together behind a progress dialog naming the file being worked on, with a **Cancel** button. Cancelling stops before the next file; whatever was already added stays added.

## Topic (.toc) files

Every PDF can have a companion topics file with the same base name — for example `manual.pdf` pairs with `manual.toc`. If a PDF has none, it shows as `(no metadata)`. Press **Refresh** (or `F5`) and choose to build topic lists: PDFBoss reads each PDF's built-in outline bookmarks (not your own `Ctrl+B` bookmarks), or falls back to detecting headings from the text when there are none.

When topics come from detected headings, lines set in heading type that are really an author's name, an organisation or a recurring footer can be kept out. List them in a plain text file named `toc-noise.txt` in the settings folder, beside `config.json`. Put one word or phrase per line; a line containing any of them, in any case, is never made a topic. Lines starting with `#` are ignored. The file is yours and stays on your machine: PDFBoss has no such list of its own.

Building a topic list means reading text from **every page**, so a batch of large documents takes a while. A progress dialog shows which file is being read and how far through the batch it is, and **Cancel** stops after the file in progress — the lists already built are kept, and pressing **Refresh** again picks up where it left off, since only PDFs still without a topics file are offered.

A `.toc` is a plain text file you can edit by hand — the page is the trailing number, so topics may contain colons and dashes:

```
# comments start with '#'
Introduction: 1
Chapter 1 - Setup: 5
Advanced Topics: 42
```

Page numbers are **1-based** (page 1 is the first page).

## Bookmarks

Mark your own places in a PDF: press `Ctrl+B` (or the **🔖** button, or right-click the page and choose **Bookmark this page**) to bookmark the page
you're reading. Name it whatever you like — the suggested name is just `Page N`.

Your bookmarks appear in their own **Bookmarks** list above the Topics. Click a bookmark to jump to its page; right-click one to **Rename** or
**Delete** it. The list only appears when the PDF has bookmarks — otherwise the Topics take the full height — and you can drag the divider between
the two lists to resize them (the divider position is remembered across runs). PDFs that have bookmarks are shown in **blue** in the PDF list, so you
can spot them at a glance. These are separate from a PDF's built-in bookmarks, which feed the topic list.

Bookmarks are saved next to the PDF — `manual.pdf` gets `manual.bookmarks.json` — so they travel with the folder and are easy to edit
by hand. Deleting the last bookmark removes the file, and saving highlights as an `(ann)` copy carries the bookmarks along.

## Choosing folders

Use **Folders…** to manage the top-level folders PDFBoss shows — you can have up to five, and each appears as its own heading at the top of the PDF list with its subfolders nested beneath it. In the dialog you can **Add** a folder, **Rename** it (the name is just a label, so a long path can read as *ICDs* or *Manuals*), reorder it with **Move up** / **Move down**, or **Remove** it. Removing a folder only stops PDFBoss listing it; nothing on disk is touched.

The order matters in one place: a PDF dropped on the window is filed into the `inbox` of whichever folder the currently selected PDF belongs to, falling back to the first folder in the list when nothing is selected.

Your folders are remembered — the next launch opens the same set — along with which subfolders you left open, your window size, and the last PDF you were reading, returning you to the page you left off on. Reading positions are kept for the 200 PDFs you opened most recently. (Starting the app with a folder on the command line uses just that folder for the session, and does not disturb your saved list.)

## Searching

Each pane has its own search box:

- **PDFs** — filters the file list by name as you type; folders with no matches are hidden.
- **Topics** — filters the current PDF's topic list as you type.
- **Viewer** — searches the **text of the open PDF**. Matches are highlighted in yellow on the page, with the current match outlined in orange, and the
  counter shows where you are (for example `2 / 17`). Use the **▲ / ▼** buttons — or `Enter` / `Shift+Enter` in the box, or `F3` / `Shift+F3`
  anywhere — to step between matches; stepping wraps around at the end. `Ctrl+F` focuses the box. Searches are case-insensitive.

Press the **✕** button to clear a search. Filters stay applied when you switch PDFs — a content search re-runs against the new document but stays on your
page until you step to a match.

## Highlighting text

Drag across text in the viewer to select it (selected words shade blue), then click **Highlight** in the toolbar — or right-click and choose **Highlight
selection**. Highlights are standard PDF annotations, so they show up in any PDF viewer.

Changes are kept in memory until you save. The **Save** button (or `Ctrl+S`) asks where to put them: **Yes** saves an annotated copy alongside the
original — `manual.pdf` becomes `manual(ann).pdf`, with the topics and bookmarks files copied too — while **No** writes into the original file. Saving while
viewing an `(ann)` copy just updates it, no questions asked. If you switch PDFs or close the app with unsaved highlights, PDFBoss asks whether to save
them. To remove a highlight, right-click it and choose **Remove highlight**, then save.

Notes: scanned PDFs without a text layer have nothing to select, and saving highlights into a digitally signed PDF may invalidate its signature (PDFBoss
warns first).

## Viewer controls

- **Prev / Next** buttons, or the arrow keys, to page through the document.
- **+ / −** to zoom; **Fit width** and **Full page** to fit the page. Your fit choice is remembered and re-applied when you resize the window.
- The mouse wheel scrolls the page and flips to the next or previous page when you scroll past the bottom or top edge.

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| Left / Page Up | Previous page |
| Right / Page Down / Space | Next page |
| Home / End | First / last page |
| + / = / - | Zoom in / out |
| W | Fit width |
| P | Full page |
| Ctrl+B | Bookmark the current page |
| Ctrl+F | Focus the content search box |
| Ctrl+S | Save highlights to the PDF |
| F3 / Shift+F3 | Next / previous search match |
| Enter / Shift+Enter (in search box) | Next / previous search match |
| F5 | Refresh PDF list |

Arrow, space, and page keys defer to the PDF and topic lists while one of them has keyboard focus, so you can still navigate the lists from the keyboard.

## Updates

A couple of seconds after launch PDFBoss quietly checks GitHub for a newer release (it never interrupts you when you're up to date or
offline). When one is found you get three choices: **Yes** downloads the update and applies it in place — the app restarts on the new version by itself;
**No** skips that version for good (you'll be asked again for the next one); **Cancel** just reminds you on the next launch. On **Windows** this works for both the
installed copy (it re-runs the installer silently) and the portable copy (it swaps the exe where it sits, even on a USB stick).

You can also check on demand with the **Check for updates** button at the bottom of this Help window — it always answers, including "You're up to
date", and still offers a version you previously skipped.

To turn the launch check off entirely, add `"check_updates": false` to your config file, `%APPDATA%\PDFGuide\config.json`.

## More

Project page: [github.com/Flinterpop/PDFBoss](https://github.com/Flinterpop/PDFBoss)
