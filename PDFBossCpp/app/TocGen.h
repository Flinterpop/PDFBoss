// Build a topic list for a PDF that has no .toc beside it.
//
// Three strategies, best first, exactly as tocgen.py orders them:
//   1. the PDF's own outline bookmarks, if it has any;
//   2. otherwise headings detected from the text -- larger or bold type, with
//      split section numbers rejoined and title-block noise filtered out;
//   3. otherwise a single "title" entry pointing at page 1.
//
// Strategy 2 is the delicate one.  Its thresholds were tuned against a real
// corpus of technical documents, so they are ported as-is rather than
// re-derived, and tests/test_tocgen_parity compares this against the Python
// implementation's output over a fixture set.

#ifndef PDFBOSS_APP_TOC_GEN_H
#define PDFBOSS_APP_TOC_GEN_H

#include <filesystem>
#include <string>
#include <vector>

#include "Metadata.h"

namespace pdfboss {

class PdfDocument;

enum class TocMethod {
    kBookmarks,  // taken from the PDF's own outline
    kHeadings,   // detected from text size and weight
    kTitle,      // last resort: one entry for the document title
};

std::string to_string(TocMethod method);

struct TocResult {
    bool ok = false;
    std::vector<TopicEntry> entries;
    TocMethod method = TocMethod::kTitle;
    std::string error;
};

// Generate topics for an already-open document.  `pdf_path` is used only for
// the filename-derived fallback title.
TocResult generate_entries(PdfDocument& doc,
                           const std::filesystem::path& pdf_path);

// Generate and write <base>.toc.  Returns the result, with `entries` holding
// what was written.
TocResult write_toc(const std::filesystem::path& pdf_path);

// Words or phrases that mark a line in heading type as an author,
// organisation or footer line rather than a topic (case-insensitive,
// matched anywhere in the line).  None by default: they are particular to
// whoever runs the app, so they live in a local file, never in this repo.
// Call once at startup, before any topics are built on any thread.
void set_author_noise(std::vector<std::string> words);

// The noise words in `file`: one per line, trimmed, blank lines and lines
// starting with '#' skipped.  Empty when the file is missing.  At most 200.
std::vector<std::string> read_noise_words(const std::filesystem::path& file);

// Where the apps keep that file: "toc-noise.txt" in their profile folder.
inline constexpr char kNoiseWordsFile[] = "toc-noise.txt";

}  // namespace pdfboss

#endif  // PDFBOSS_APP_TOC_GEN_H
