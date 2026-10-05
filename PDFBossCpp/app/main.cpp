#include <filesystem>

#include <wx/cmdline.h>
#include <wx/filename.h>
#include <wx/stdpaths.h>
#include <wx/wx.h>

#include "Config.h"
#include "MainFrame.h"
#include "PathUtf8.h"
#include "TocGen.h"
#include "Version.h"

namespace fs = std::filesystem;

namespace {

// A "pdfs" folder beside the executable, matching _default_folder().
fs::path default_folder()
{
    wxFileName exe(wxStandardPaths::Get().GetExecutablePath());
    return pdfboss::path_from_utf8(exe.GetPath().utf8_string()) / "pdfs";
}

}  // namespace

class PdfBossApp : public wxApp {
public:
    bool OnInit() override;
    void OnInitCmdLine(wxCmdLineParser& parser) override;
    bool OnCmdLineParsed(wxCmdLineParser& parser) override;

private:
    wxString folder_argument_;
};

void PdfBossApp::OnInitCmdLine(wxCmdLineParser& parser)
{
    wxApp::OnInitCmdLine(parser);
    // Without declaring this, wx's default parser REJECTS any positional
    // argument, OnInit() returns false, and the app exits with no window and
    // no message.  `PDFBoss.exe C:\docs` is exactly the documented usage, so
    // the failure would be silent and total.
    parser.AddParam("folder", wxCMD_LINE_VAL_STRING, wxCMD_LINE_PARAM_OPTIONAL);
}

bool PdfBossApp::OnCmdLineParsed(wxCmdLineParser& parser)
{
    if (parser.GetParamCount() > 0) {
        folder_argument_ = parser.GetParam(0);
    }
    return wxApp::OnCmdLineParsed(parser);
}

bool PdfBossApp::OnInit()
{
    if (!wxApp::OnInit()) {
        return false;
    }

    wxInitAllImageHandlers();

    // Folder precedence, as main() in app.py resolves it: the command line,
    // then the last folder chosen (only if it still exists, since it may be on
    // a drive that is no longer plugged in), then ./pdfs beside the exe.
    pdfboss::Config config;
    config.load();
    // Author and organisation lines to keep out of generated topics: the
    // person's own list, beside config.json, never compiled in.
    pdfboss::set_author_noise(pdfboss::read_noise_words(
        pdfboss::Config::path().parent_path() / pdfboss::kNoiseWordsFile));

    fs::path override_root;
    if (!folder_argument_.empty()) {
        override_root = pdfboss::path_from_utf8(folder_argument_.utf8_string());
    } else if (config.roots().empty()) {
        // Nothing configured yet: seed the ./pdfs folder beside the exe, so a
        // fresh install opens on something rather than an empty tree.
        pdfboss::Root root;
        root.path = pdfboss::path_to_utf8(default_folder());
        root.name = pdfboss::path_to_utf8(default_folder().filename());
        config.save_roots({root});
    }

    auto* frame = new pdfboss::MainFrame(override_root, std::move(config));
    frame->Show(true);
    return true;
}

wxIMPLEMENT_APP(PdfBossApp);
