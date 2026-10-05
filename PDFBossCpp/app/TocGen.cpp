#include "TocGen.h"

#include <algorithm>
#include <cassert>
#include <cmath>
#include <fstream>
#include <map>
#include <set>
#include <string>
#include <vector>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>  // GetStringTypeW, for Unicode-correct isalpha

#include "PathUtf8.h"
#include "PdfDocument.h"

namespace pdfboss {
namespace {

namespace fs = std::filesystem;

// ---------------------------------------------------------------------------
// UTF-8 helpers.
//
// Python measures len() and iterates in *code points*, not bytes, and its
// str.isalpha() is Unicode-aware.  Doing either bytewise here would change
// which headings survive _is_noise on any document with accented text, so the
// few places that care decode first.
// ---------------------------------------------------------------------------

std::u32string decode_utf8(std::string_view text)
{
    std::u32string out;
    out.reserve(text.size());
    std::size_t i = 0;
    while (i < text.size()) {
        const unsigned char lead = static_cast<unsigned char>(text[i]);
        char32_t code = 0;
        std::size_t extra = 0;
        if (lead < 0x80) {
            code = lead;
        } else if ((lead & 0xE0) == 0xC0) {
            code = lead & 0x1FU;
            extra = 1;
        } else if ((lead & 0xF0) == 0xE0) {
            code = lead & 0x0FU;
            extra = 2;
        } else if ((lead & 0xF8) == 0xF0) {
            code = lead & 0x07U;
            extra = 3;
        } else {
            // Invalid lead byte; mirror Python's decoder by substituting the
            // replacement character rather than dropping the text.
            out.push_back(0xFFFD);
            ++i;
            continue;
        }
        if (i + extra >= text.size()) {
            out.push_back(0xFFFD);
            break;
        }
        for (std::size_t k = 1; k <= extra; ++k) {
            code = (code << 6) | (static_cast<unsigned char>(text[i + k]) & 0x3FU);
        }
        out.push_back(code);
        i += extra + 1;
    }
    return out;
}

std::string encode_utf8(const std::u32string& text)
{
    std::string out;
    out.reserve(text.size());
    for (const char32_t code : text) {
        if (code < 0x80) {
            out.push_back(static_cast<char>(code));
        } else if (code < 0x800) {
            out.push_back(static_cast<char>(0xC0 | (code >> 6)));
            out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
        } else if (code < 0x10000) {
            out.push_back(static_cast<char>(0xE0 | (code >> 12)));
            out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
        } else {
            out.push_back(static_cast<char>(0xF0 | (code >> 18)));
            out.push_back(static_cast<char>(0x80 | ((code >> 12) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | ((code >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (code & 0x3F)));
        }
    }
    return out;
}

bool is_space(char32_t code)
{
    return code == U' ' || code == U'\t' || code == U'\n' || code == U'\r' ||
           code == U'\f' || code == U'\v' || code == 0xA0;
}

// Unicode-aware alphabetic test, matching Python's str.isalpha() closely.
// std::isalpha under the default "C" locale sees only ASCII, which would
// wrongly reject an accented heading as "fewer than two letters" noise.
bool is_alpha(char32_t code)
{
    if (code < 0x80) {
        return (code >= U'A' && code <= U'Z') || (code >= U'a' && code <= U'z');
    }
    if (code > 0xFFFF) {
        return false;  // outside the BMP; not worth surrogate handling here
    }
    const wchar_t wide = static_cast<wchar_t>(code);
    WORD type = 0;
    if (::GetStringTypeW(CT_CTYPE1, &wide, 1, &type) == 0) {
        return false;
    }
    return (type & C1_ALPHA) != 0;
}

std::size_t count_alpha(const std::u32string& text)
{
    std::size_t count = 0;
    for (const char32_t code : text) {
        if (is_alpha(code)) {
            ++count;
        }
    }
    return count;
}

std::string to_lower_ascii(std::string text)
{
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return text;
}

bool contains_ci(const std::string& haystack, const std::string& needle)
{
    return to_lower_ascii(haystack).find(to_lower_ascii(needle)) !=
           std::string::npos;
}

// tocgen.py's _clean: collapse all whitespace runs to single spaces and trim.
//
// The Python version also does .replace("�", "-") twice.  Both literals
// are byte-identical (EF BF BD), so the second call is a no-op -- almost
// certainly an en-dash and an em-dash that were themselves corrupted into
// replacement characters when the file was re-saved at some point.  The
// behaviour is reproduced exactly as it stands today so the golden-corpus
// comparison holds; see the note in the port's README before "fixing" it,
// because doing so changes generated .toc output.
std::string clean(std::string_view text)
{
    const std::u32string decoded = decode_utf8(text);
    std::u32string collapsed;
    collapsed.reserve(decoded.size());

    bool pending_space = false;
    for (const char32_t raw : decoded) {
        const char32_t code = (raw == 0xFFFD) ? U'-' : raw;
        if (is_space(code)) {
            pending_space = !collapsed.empty();
            continue;
        }
        if (pending_space) {
            collapsed.push_back(U' ');
            pending_space = false;
        }
        collapsed.push_back(code);
    }
    return encode_utf8(collapsed);
}

// ---------------------------------------------------------------------------
// The patterns from tocgen.py, hand-rolled.
//
// std::regex would be a closer transcription but runs over every line of every
// page of every document; these are simple enough that a scanner is both
// clearer about intent and dramatically faster.
// ---------------------------------------------------------------------------

// NUM_ONLY: ^\d+(\.\d+)*\.?$   e.g. "5", "5.2", "5.2.1."
bool is_number_only(const std::string& text)
{
    if (text.empty()) {
        return false;
    }
    std::size_t i = 0;
    const auto digits = [&]() {
        const std::size_t start = i;
        while (i < text.size() && text[i] >= '0' && text[i] <= '9') {
            ++i;
        }
        return i > start;
    };
    if (!digits()) {
        return false;
    }
    while (i < text.size() && text[i] == '.') {
        const std::size_t dot = i;
        ++i;
        if (!digits()) {
            i = dot;  // a trailing '.' is allowed, but only at the very end
            break;
        }
    }
    if (i < text.size() && text[i] == '.') {
        ++i;
    }
    return i == text.size();
}

// NUM_PREFIX: ^\d+(\.\d+)*\.?\s+\S   e.g. "5.2 Overview"
bool has_number_prefix(const std::string& text)
{
    std::size_t i = 0;
    const auto digits = [&]() {
        const std::size_t start = i;
        while (i < text.size() && text[i] >= '0' && text[i] <= '9') {
            ++i;
        }
        return i > start;
    };
    if (!digits()) {
        return false;
    }
    while (i < text.size() && text[i] == '.') {
        const std::size_t dot = i;
        ++i;
        if (!digits()) {
            i = dot;
            break;
        }
    }
    if (i < text.size() && text[i] == '.') {
        ++i;
    }
    const std::size_t space_start = i;
    while (i < text.size() &&
           is_space(static_cast<char32_t>(static_cast<unsigned char>(text[i])))) {
        ++i;
    }
    if (i == space_start) {
        return false;  // \s+ needs at least one
    }
    return i < text.size();  // \S
}

bool is_ascii_digit(char c)
{
    return c >= '0' && c <= '9';
}

// \w in Python: letters, digits and underscore (Unicode-aware).
bool is_word_char(char32_t code)
{
    return code == U'_' || (code >= U'0' && code <= U'9') || is_alpha(code);
}

// DATE_LIKE: ^\d{1,2}\s+\w+\s+\d{4}$   e.g. "3 January 2024"
bool is_date_like(const std::string& text)
{
    const std::u32string chars = decode_utf8(text);
    std::size_t i = 0;
    std::size_t start = i;
    while (i < chars.size() && chars[i] >= U'0' && chars[i] <= U'9') {
        ++i;
    }
    const std::size_t day_digits = i - start;
    if (day_digits < 1 || day_digits > 2) {
        return false;
    }
    start = i;
    while (i < chars.size() && is_space(chars[i])) {
        ++i;
    }
    if (i == start) {
        return false;
    }
    start = i;
    while (i < chars.size() && is_word_char(chars[i])) {
        ++i;
    }
    if (i == start) {
        return false;
    }
    start = i;
    while (i < chars.size() && is_space(chars[i])) {
        ++i;
    }
    if (i == start) {
        return false;
    }
    start = i;
    while (i < chars.size() && chars[i] >= U'0' && chars[i] <= U'9') {
        ++i;
    }
    return (i - start) == 4 && i == chars.size();
}

// MONTH_DATE: (January|...|December)\s+\d{1,2},\s+\d{4}   anywhere in the text.
// Case-sensitive, as the Python pattern is (only AUTHOR_ORG uses IGNORECASE).
bool has_month_date(const std::string& text)
{
    static const char* const kMonths[] = {
        "January", "February", "March",     "April",   "May",      "June",
        "July",    "August",   "September", "October", "November", "December"};

    for (const char* month : kMonths) {
        std::size_t from = 0;
        while (true) {
            const std::size_t at = text.find(month, from);
            if (at == std::string::npos) {
                break;
            }
            std::size_t i = at + std::char_traits<char>::length(month);
            const std::size_t space_start = i;
            while (i < text.size() && (text[i] == ' ' || text[i] == '\t')) {
                ++i;
            }
            if (i == space_start) {
                from = at + 1;
                continue;
            }
            std::size_t digit_start = i;
            while (i < text.size() && is_ascii_digit(text[i])) {
                ++i;
            }
            const std::size_t day = i - digit_start;
            if (day < 1 || day > 2 || i >= text.size() || text[i] != ',') {
                from = at + 1;
                continue;
            }
            ++i;
            const std::size_t after_comma = i;
            while (i < text.size() && (text[i] == ' ' || text[i] == '\t')) {
                ++i;
            }
            if (i == after_comma) {
                from = at + 1;
                continue;
            }
            digit_start = i;
            while (i < text.size() && is_ascii_digit(text[i])) {
                ++i;
            }
            if ((i - digit_start) == 4) {
                return true;
            }
            from = at + 1;
        }
    }
    return false;
}

// The words set_author_noise() was given.  Empty unless the app loaded some.
std::vector<std::string>& author_noise()
{
    static std::vector<std::string> words;
    return words;
}

// AUTHOR_ORG: recurring author / organisation / footer lines to drop.  The
// words used to be compiled in, and were the author's own name and
// organisation -- in a public repo.  They are now whatever the person
// running the app lists locally (set_author_noise), and none by default.
bool is_author_org(const std::string& text)
{
    for (const std::string& needle : author_noise()) {   // bounded by the list
        if (!needle.empty() && contains_ci(text, needle)) {
            return true;
        }
    }
    return false;
}

bool is_noise(const std::string& text)
{
    if (text.find('@') != std::string::npos) {
        return true;
    }
    if (to_lower_ascii(text).find("http") != std::string::npos) {
        return true;
    }
    if (is_date_like(text) || has_month_date(text)) {
        return true;
    }
    if (is_author_org(text)) {
        return true;
    }
    const std::u32string chars = decode_utf8(text);
    if (chars.size() < 3 || chars.size() > 90) {
        return true;
    }
    if (count_alpha(chars) < 2) {
        return true;
    }
    return false;
}

// ---------------------------------------------------------------------------

// The most common rounded font size, weighted by how much text is set in it --
// i.e. the body size everything else is measured against.
float body_size(PdfDocument& doc)
{
    // Insertion-ordered so ties break the way collections.Counter breaks them
    // (first seen wins), rather than by numeric value.
    std::vector<std::pair<float, long long>> tally;

    const int pages = doc.page_count();
    for (int pno = 0; pno < pages; ++pno) {
        std::vector<TextBlock> blocks;
        if (!doc.page_text_blocks(pno, &blocks)) {
            continue;
        }
        for (const TextBlock& block : blocks) {
            for (const TextLine& line : block.lines) {
                for (const Span& span : line.spans) {
                    const std::string trimmed = clean(span.text);
                    if (trimmed.empty()) {
                        continue;
                    }
                    const float rounded = std::round(span.size * 10.0F) / 10.0F;
                    const auto it = std::find_if(
                        tally.begin(), tally.end(),
                        [rounded](const std::pair<float, long long>& e) {
                            return e.first == rounded;
                        });
                    const long long weight =
                        static_cast<long long>(decode_utf8(trimmed).size());
                    if (it == tally.end()) {
                        tally.emplace_back(rounded, weight);
                    } else {
                        it->second += weight;
                    }
                }
            }
        }
    }

    if (tally.empty()) {
        return 0.0F;
    }
    const auto best = std::max_element(
        tally.begin(), tally.end(),
        [](const std::pair<float, long long>& a,
           const std::pair<float, long long>& b) { return a.second < b.second; });
    return best->first;
}

std::vector<TopicEntry> extract_headings(PdfDocument& doc, float body)
{
    std::vector<TopicEntry> out;
    std::set<std::string> seen;
    std::string pending;
    bool has_pending = false;

    const int pages = doc.page_count();
    for (int index = 0; index < pages; ++index) {
        const int pno = index + 1;  // tocgen enumerates from 1
        std::vector<TextBlock> blocks;
        if (!doc.page_text_blocks(index, &blocks)) {
            continue;
        }
        for (const TextBlock& block : blocks) {
            for (const TextLine& line : block.lines) {
                if (line.spans.empty()) {
                    continue;
                }
                std::string joined;
                for (std::size_t i = 0; i < line.spans.size(); ++i) {
                    if (i != 0) {
                        joined += " ";
                    }
                    joined += line.spans[i].text;
                }
                std::string text = clean(joined);
                if (text.empty()) {
                    continue;
                }

                float size = 0.0F;
                bool bold = false;
                for (const Span& span : line.spans) {
                    size = (span.size > size) ? span.size : size;
                    // PyMuPDF's test is `"bold" in font.lower() or flags & 16`;
                    // MuPDF answers the second half directly via
                    // fz_font_is_bold, which PdfDocument surfaces as Span::bold.
                    if (span.bold || contains_ci(span.font, "bold")) {
                        bold = true;
                    }
                }

                if (!(size >= body + 1.0F || (bold && size >= body + 0.3F))) {
                    continue;
                }

                // A bare section number on its own line belongs to the heading
                // on the next one; hold it and prepend.
                if (is_number_only(text)) {
                    pending = text;
                    while (!pending.empty() && pending.back() == '.') {
                        pending.pop_back();
                    }
                    has_pending = true;
                    continue;
                }
                if (has_pending && !has_number_prefix(text)) {
                    text = pending + " " + text;
                }
                has_pending = false;
                pending.clear();

                if (is_noise(text)) {
                    continue;
                }
                const std::string key = to_lower_ascii(text);
                if (seen.count(key) != 0) {
                    continue;
                }
                seen.insert(key);
                out.push_back(TopicEntry{text, pno});
            }
        }
    }
    return out;
}

std::string stem_title(const fs::path& pdf_path)
{
    const std::string stem = path_to_utf8(pdf_path.stem());

    // ^\d+[-_]?(TN\d+[A-Za-z]?)?[-_]?  -- strips a leading document number and
    // an optional "TN123a" tech-note tag, the naming convention in the corpus.
    std::size_t i = 0;
    while (i < stem.size() && is_ascii_digit(stem[i])) {
        ++i;
    }
    if (i == 0) {
        // The pattern needs \d+, so with no leading digits nothing is stripped.
        std::string out = stem;
        std::replace(out.begin(), out.end(), '_', ' ');
        const std::string trimmed = clean(out);
        return trimmed.empty() ? stem : trimmed;
    }
    if (i < stem.size() && (stem[i] == '-' || stem[i] == '_')) {
        ++i;
    }
    if (i + 1 < stem.size() && (stem[i] == 'T' || stem[i] == 't') &&
        (stem[i + 1] == 'N' || stem[i + 1] == 'n')) {
        std::size_t j = i + 2;
        const std::size_t digits_start = j;
        while (j < stem.size() && is_ascii_digit(stem[j])) {
            ++j;
        }
        if (j > digits_start) {
            if (j < stem.size() && is_alpha(static_cast<char32_t>(
                                       static_cast<unsigned char>(stem[j]))) &&
                static_cast<unsigned char>(stem[j]) < 0x80) {
                ++j;
            }
            i = j;
        }
    }
    if (i < stem.size() && (stem[i] == '-' || stem[i] == '_')) {
        ++i;
    }

    std::string out = stem.substr(i);
    std::replace(out.begin(), out.end(), '_', ' ');
    const std::string trimmed = clean(out);
    return trimmed.empty() ? stem : trimmed;
}

std::string best_title(PdfDocument& doc, const std::string& fallback)
{
    float best_size = 0.0F;
    std::string best;

    std::vector<TextBlock> blocks;
    if (doc.page_text_blocks(0, &blocks)) {
        for (const TextBlock& block : blocks) {
            for (const TextLine& line : block.lines) {
                for (const Span& span : line.spans) {
                    const std::string text = clean(span.text);
                    const std::u32string chars = decode_utf8(text);
                    if (chars.size() >= 4 && chars.size() <= 80 &&
                        count_alpha(chars) >= 3 && span.size > best_size) {
                        best_size = span.size;
                        best = text;
                    }
                }
            }
        }
    }

    const std::string title = best.empty() ? fallback : best;
    // Too short to be a real title; the filename is the better guess.
    return (decode_utf8(title).size() >= 6) ? title : fallback;
}

}  // namespace

std::string to_string(TocMethod method)
{
    switch (method) {
        case TocMethod::kBookmarks:
            return "bookmarks";
        case TocMethod::kHeadings:
            return "headings";
        case TocMethod::kTitle:
            break;
    }
    return "title";
}

TocResult generate_entries(PdfDocument& doc, const fs::path& pdf_path)
{
    TocResult result;
    if (!doc.is_open()) {
        result.error = "no document open";
        return result;
    }

    // 1. the document's own outline
    const std::vector<OutlineItem> outline = doc.outline();
    if (!outline.empty()) {
        std::vector<TopicEntry> entries;
        for (const OutlineItem& item : outline) {
            const std::string title = clean(item.title);
            if (title.empty()) {
                continue;
            }
            entries.push_back(TopicEntry{title, (item.page > 1) ? item.page : 1});
        }
        if (!entries.empty()) {
            result.ok = true;
            result.method = TocMethod::kBookmarks;
            result.entries = std::move(entries);
            return result;
        }
    }

    // 2. headings detected from the text
    const float body = body_size(doc);
    std::vector<TopicEntry> headings = extract_headings(doc, body);
    if (doc.page_count() > 1 && headings.size() >= 2) {
        result.ok = true;
        result.method = TocMethod::kHeadings;
        result.entries = std::move(headings);
        return result;
    }

    // 3. the title
    result.ok = true;
    result.method = TocMethod::kTitle;
    result.entries.push_back(
        TopicEntry{best_title(doc, stem_title(pdf_path)), 1});
    return result;
}

TocResult write_toc(const fs::path& pdf_path)
{
    PdfDocument doc;
    TocResult result;
    if (!doc.open(pdf_path)) {
        result.error = doc.last_error();
        return result;
    }

    result = generate_entries(doc, pdf_path);
    if (!result.ok) {
        return result;
    }

    fs::path out_path = pdf_path;
    out_path.replace_extension(".toc");

    std::ofstream stream(out_path, std::ios::binary | std::ios::trunc);
    if (!stream) {
        result.ok = false;
        result.error = "could not write " + path_to_utf8(out_path.filename());
        return result;
    }

    const std::string name = path_to_utf8(pdf_path.filename());
    stream << "# Topics for " << name << "\n";
    stream << "# Auto-generated (" << to_string(result.method)
           << "). Format: 'Topic: page' (1-based). Edit freely.\n";
    stream << "\n";
    for (const TopicEntry& entry : result.entries) {
        stream << entry.topic << ": " << entry.page << "\n";
    }
    stream.flush();

    if (!stream.good()) {
        result.ok = false;
        result.error = "could not write " + path_to_utf8(out_path.filename());
    }
    return result;
}

void set_author_noise(std::vector<std::string> words)
{
    author_noise() = std::move(words);
}

std::vector<std::string> read_noise_words(const fs::path& file)
{
    constexpr std::size_t kMaxWords = 200;
    std::vector<std::string> words;
    std::ifstream in(file);
    std::string line;
    while (words.size() < kMaxWords && std::getline(in, line)) {   // bounded
        // Notepad starts a UTF-8 file with a byte-order mark.
        if (line.compare(0, 3, "\xEF\xBB\xBF") == 0) {
            line.erase(0, 3);
        }
        const std::size_t first = line.find_first_not_of(" \t\r");
        if (first == std::string::npos || line[first] == '#') {
            continue;
        }
        const std::size_t last = line.find_last_not_of(" \t\r");
        words.push_back(line.substr(first, last - first + 1));
    }
    return words;
}

}  // namespace pdfboss
