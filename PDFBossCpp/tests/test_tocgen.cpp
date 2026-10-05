// Golden-corpus parity for topic generation.
//
// The expected values below are the *verbatim output of tocgen.py* over the
// same fixtures, captured with tocgen_oracle.py against pymupdf==1.28.2 (the
// release wrapping the MuPDF this links).  Regenerate with:
//
//     python tests/tocgen_oracle.py tests/fixture*.pdf
//
// Heading detection is the highest-risk part of the port: it is a pile of
// thresholds tuned against a real corpus, and a regression in it produces
// plausible-looking but subtly wrong topic lists rather than an error.  These
// tests pin all three strategies and, more importantly, the awkward cases the
// heuristic exists to handle.

#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include <catch2/catch_test_macros.hpp>

#include "PdfDocument.h"
#include "TocGen.h"

namespace fs = std::filesystem;
using namespace pdfboss;

namespace {

fs::path fixture_dir()
{
    return fs::path(PDFBOSS_TEST_FIXTURE).parent_path();
}

TocResult generate(const std::string& name)
{
    static PdfDocument doc;  // reused; open() closes any previous document
    const fs::path path = fixture_dir() / name;
    REQUIRE(doc.open(path));
    // The fixtures carry an author line, "B Graham", and the golden values
    // come from the Python oracle, which had that name compiled into its
    // noise list.  The C++ side has no compiled-in list any more, so the
    // tests supply the word the way a person's local toc-noise.txt would.
    set_author_noise({"Graham"});
    TocResult result = generate_entries(doc, path);
    set_author_noise({});
    return result;
}

}  // namespace

TEST_CASE("an outline wins over heading detection", "[tocgen][parity]")
{
    const TocResult result = generate("fixture.pdf");
    REQUIRE(result.ok);
    CHECK(to_string(result.method) == "bookmarks");
    REQUIRE(result.entries.size() == 2);
    CHECK(result.entries[0].topic == "Introduction");
    CHECK(result.entries[0].page == 1);
    CHECK(result.entries[1].topic == "Chapter 2");
    CHECK(result.entries[1].page == 2);
}

TEST_CASE("headings are detected when there is no outline", "[tocgen][parity]")
{
    const TocResult result = generate("fixture_headings.pdf");
    REQUIRE(result.ok);
    CHECK(to_string(result.method) == "headings");

    // Exactly the Python oracle's output, in order.
    const std::vector<TopicEntry> expected = {
        {"Overview of the System", 1},
        {"5.2 Message Formats", 1},
        {"6.1 Timing and Latency", 2},
        {"Appendix A", 2},
    };

    REQUIRE(result.entries.size() == expected.size());
    for (std::size_t i = 0; i < expected.size(); ++i) {
        INFO("entry " << i);
        CHECK(result.entries[i].topic == expected[i].topic);
        CHECK(result.entries[i].page == expected[i].page);
    }
}

TEST_CASE("a bare section number is rejoined with the title below it",
          "[tocgen][parity]")
{
    // "5.2" alone on one line and "Message Formats" on the next must become a
    // single topic.  Getting this wrong yields a topic list full of naked
    // numbers, which is the failure this rule was written for.
    const TocResult result = generate("fixture_headings.pdf");
    REQUIRE(result.ok);

    bool found_joined = false;
    for (const TopicEntry& entry : result.entries) {
        CHECK(entry.topic != "5.2");  // never leak the bare number
        if (entry.topic == "5.2 Message Formats") {
            found_joined = true;
        }
    }
    CHECK(found_joined);
}

TEST_CASE("a heading with its own number is not rejoined", "[tocgen][parity]")
{
    // "6.1 Timing and Latency" already carries a number prefix, so a pending
    // bare number must not be glued onto it as well.
    const TocResult result = generate("fixture_headings.pdf");
    REQUIRE(result.ok);

    for (const TopicEntry& entry : result.entries) {
        if (entry.topic.find("Timing and Latency") != std::string::npos) {
            CHECK(entry.topic == "6.1 Timing and Latency");
        }
    }
}

TEST_CASE("noise lines are filtered out of headings", "[tocgen][parity]")
{
    // The author line is dropped by the word generate() supplies, as a
    // person's local list would; nothing of the kind is compiled in.
    const TocResult result = generate("fixture_headings.pdf");
    REQUIRE(result.ok);

    for (const TopicEntry& entry : result.entries) {
        // An email address, a bare date and an author line are all set in
        // heading type in the fixture, and all three must be dropped.
        CHECK(entry.topic.find('@') == std::string::npos);
        CHECK(entry.topic != "3 January 2024");
        CHECK(entry.topic.find("Graham") == std::string::npos);
    }
}

TEST_CASE("a repeated running header appears once", "[tocgen][parity]")
{
    const TocResult result = generate("fixture_headings.pdf");
    REQUIRE(result.ok);

    int seen = 0;
    for (const TopicEntry& entry : result.entries) {
        if (entry.topic == "Overview of the System") {
            ++seen;
        }
    }
    CHECK(seen == 1);
}

TEST_CASE("a document with nothing to go on falls back to its title",
          "[tocgen][parity]")
{
    const TocResult result = generate("fixture_title.pdf");
    REQUIRE(result.ok);
    CHECK(to_string(result.method) == "title");
    REQUIRE(result.entries.size() == 1);
    CHECK(result.entries[0].topic == "A Solitary Document Title");
    CHECK(result.entries[0].page == 1);
}

TEST_CASE("write_toc produces the same file the Python app writes", "[tocgen]")
{
    const fs::path source = fixture_dir() / "fixture_headings.pdf";
    const fs::path work =
        fs::temp_directory_path() / "pdfboss_write_toc_fixture.pdf";
    const fs::path expected_toc =
        fs::temp_directory_path() / "pdfboss_write_toc_fixture.toc";

    std::error_code ec;
    fs::remove(expected_toc, ec);
    fs::copy_file(source, work, fs::copy_options::overwrite_existing, ec);
    REQUIRE_FALSE(ec);

    const TocResult result = write_toc(work);
    REQUIRE(result.ok);
    REQUIRE(fs::exists(expected_toc));

    std::ifstream stream(expected_toc, std::ios::binary);
    std::string content((std::istreambuf_iterator<char>(stream)),
                        std::istreambuf_iterator<char>());

    // The header comments are part of the format: they tell a user editing the
    // file by hand what the format is, and which strategy produced it.
    CHECK(content.find("# Topics for pdfboss_write_toc_fixture.pdf") == 0);
    CHECK(content.find("# Auto-generated (headings).") != std::string::npos);
    CHECK(content.find("'Topic: page' (1-based)") != std::string::npos);
    CHECK(content.find("\nOverview of the System: 1\n") != std::string::npos);
    CHECK(content.find("\n5.2 Message Formats: 1\n") != std::string::npos);

    // And the round trip: what write_toc wrote, load_metadata must read back.
    const TopicLoadResult reloaded = load_metadata(expected_toc);
    REQUIRE(reloaded.ok);
    REQUIRE(reloaded.entries.size() == result.entries.size());
    for (std::size_t i = 0; i < reloaded.entries.size(); ++i) {
        INFO("entry " << i);
        CHECK(reloaded.entries[i].topic == result.entries[i].topic);
        CHECK(reloaded.entries[i].page == result.entries[i].page);
    }

    fs::remove(work, ec);
    fs::remove(expected_toc, ec);
}

TEST_CASE("no author or organisation is compiled in", "[tocgen]")
{
    // With no local list, the fixture's author line is just another line in
    // heading type: the filter knows no names of its own.
    PdfDocument doc;
    const fs::path path = fixture_dir() / "fixture_headings.pdf";
    REQUIRE(doc.open(path));
    set_author_noise({});
    const TocResult result = generate_entries(doc, path);
    bool author_kept = false;
    for (const TopicEntry& entry : result.entries) {
        author_kept = author_kept || entry.topic == "B Graham";
    }
    CHECK(author_kept);
}

TEST_CASE("noise words are read from a local file", "[tocgen]")
{
    const fs::path file =
        fs::temp_directory_path() / "pdfboss_noise_words.txt";
    {
        std::ofstream out(file, std::ios::binary);
        out << "\xEF\xBB\xBF# lines to keep out of topics\r\n"
               "  First Name  \r\n"
               "\r\n"
               "Some Organisation\n";
    }
    CHECK(read_noise_words(file) ==
          std::vector<std::string>{"First Name", "Some Organisation"});
    std::error_code ec;
    fs::remove(file, ec);
    CHECK(read_noise_words(file).empty());
}
