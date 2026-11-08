// The right-hand pane: the content-search bar, the page navigation bar, and
// the rendered page itself.
//
// Coordinate spaces, because mixing them is the bug this pane invites:
//   PDF points   what PdfDocument speaks -- word boxes, search hits, annots.
//   canvas px    PDF points * zoom.  What is drawn.
//   device px    canvas px minus the scroll offset.  What the mouse reports.
// Convert at the boundary with to_canvas()/to_pdf() and nowhere else.

#ifndef PDFBOSS_APP_VIEWER_PANE_H
#define PDFBOSS_APP_VIEWER_PANE_H

#include <atomic>
#include <filesystem>
#include <functional>
#include <memory>
#include <optional>
#include <thread>
#include <utility>
#include <vector>

#include <wx/wx.h>

#include "PdfDocument.h"

namespace pdfboss {

// Zoom limits, matching app.py's MIN_ZOOM / MAX_ZOOM / ZOOM_STEP.
inline constexpr float kMinZoom = 0.25F;
inline constexpr float kMaxZoom = 4.0F;
inline constexpr float kZoomStep = 1.25F;

enum class FitMode {
    kNone,   // a manual zoom is in effect
    kWidth,
    kPage,
};

class ViewerPane : public wxPanel {
public:
    ViewerPane(wxWindow* parent, FitMode initial_fit);
    ~ViewerPane() override;

    bool load(const std::filesystem::path& pdf_path, int page_index);
    void unload();

    bool has_document() const { return doc_.is_open(); }
    const std::filesystem::path& path() const { return path_; }
    int page_count() const { return page_count_; }
    int current_page() const { return current_page_; }

    void goto_page(int page_index);
    void next_page() { goto_page(current_page_ + 1); }
    void prev_page() { goto_page(current_page_ - 1); }

    void change_zoom(float factor);
    void fit_width();
    void fit_page();

    // -- content search --
    void focus_search();
    void next_match() { step_match(1); }
    void prev_match() { step_match(-1); }
    // Search for `needle` as if typed into the search box, but show the first
    // match on or after `page_index` rather than the first in the document;
    // with no match from there on, the view stays on `page_index`.  For a host
    // that already knows which page a hit is on (DocBoss's search index).
    void show_search(const std::string& needle, int page_index);

    // -- highlights --
    void highlight_selection();
    bool dirty() const { return doc_.dirty(); }
    // Offer to save pending highlights.  Returns false if the user cancelled
    // and the caller should abandon whatever it was about to do.
    bool maybe_save_annotations();

    void set_page_changed_handler(std::function<void(int)> handler)
    {
        on_page_changed_ = std::move(handler);
    }
    // Raised when the user picks a fit, so the choice can be persisted.  The
    // Python app writes fit_pref on every _set_fit_pref call; without this the
    // preference is read at startup and never written, so it silently never
    // survives a restart.
    void set_fit_changed_handler(std::function<void(FitMode)> handler)
    {
        on_fit_changed_ = std::move(handler);
    }
    // Asked to bookmark the current page from the canvas context menu.
    void set_bookmark_requested_handler(std::function<void(int)> handler)
    {
        on_bookmark_requested_ = std::move(handler);
    }
    // Raised after an "(ann)" copy is written, so the PDF list can pick it up.
    void set_documents_changed_handler(std::function<void()> handler)
    {
        on_documents_changed_ = std::move(handler);
    }

    PdfDocument& document() { return doc_; }

private:
    void build_ui();
    void on_paint(wxPaintEvent& event);
    void on_size(wxSizeEvent& event);
    void on_mouse_wheel(wxMouseEvent& event);
    void on_left_down(wxMouseEvent& event);
    void on_motion(wxMouseEvent& event);
    void on_left_up(wxMouseEvent& event);
    void on_context_menu(wxContextMenuEvent& event);

    void render_current_page();
    void apply_fit();
    void update_nav_label();
    void update_match_label();

    void start_search(const std::string& needle);
    void cancel_search();
    void step_match(int delta);
    void select_match(std::size_t index);
    void scroll_rect_into_view(const Rect& rect);

    wxPoint to_canvas(float x, float y) const;
    wxRect to_canvas(const Rect& rect) const;
    // Device (mouse) coordinates to PDF points, undoing the scroll offset.
    void to_pdf(const wxPoint& device, float* x, float* y) const;

    PdfDocument doc_;
    std::filesystem::path path_;
    int page_count_ = 0;
    int current_page_ = 0;
    float zoom_ = 1.0F;
    FitMode fit_mode_ = FitMode::kWidth;

    wxScrolledWindow* canvas_ = nullptr;
    wxBitmap page_bitmap_;
    wxString message_;

    wxTextCtrl* search_box_ = nullptr;
    wxStaticText* match_label_ = nullptr;
    wxButton* prev_button_ = nullptr;
    wxButton* next_button_ = nullptr;
    wxStaticText* page_label_ = nullptr;
    wxButton* highlight_button_ = nullptr;

    // Search results as (page index, rect in PDF points), in document order.
    std::vector<std::pair<int, Rect>> matches_;
    int current_match_ = -1;
    std::string needle_;
    bool scan_running_ = false;
    // Set by show_search(): the first match to select is the first on or
    // after this page.  -1 selects the first match in the document.
    int wanted_page_ = -1;

    // The worker scans on its own thread with its own PdfDocument.  `alive_`
    // is cleared in the destructor and checked on the UI thread before any
    // posted result is applied; `generation_` invalidates the results of a
    // scan the user has already superseded by typing again.
    std::thread worker_;
    std::shared_ptr<std::atomic<bool>> alive_;
    std::shared_ptr<std::atomic<unsigned>> generation_;

    // Drag selection, in PDF points.
    std::optional<std::pair<float, float>> select_anchor_;
    std::optional<std::pair<float, float>> select_drag_;
    std::vector<Rect> selected_rects_;
    // Where the canvas context menu was opened, in PDF points.
    std::optional<std::pair<float, float>> menu_point_;

    std::function<void(int)> on_page_changed_;
    std::function<void(FitMode)> on_fit_changed_;
    std::function<void(int)> on_bookmark_requested_;
    std::function<void()> on_documents_changed_;
};

}  // namespace pdfboss

#endif  // PDFBOSS_APP_VIEWER_PANE_H
