#include "ViewerPane.h"

#include <algorithm>
#include <cassert>
#include <cstring>

#include <wx/dcbuffer.h>
#include <wx/graphics.h>
#include <wx/wrapsizer.h>

#include "Metadata.h"
#include "PathUtf8.h"

namespace pdfboss {
namespace {

namespace fs = std::filesystem;

constexpr int kScrollUnit = 16;

// Overlay colours, matching the Python app: every hit yellow, the current one
// outlined orange, the drag selection blue.
const wxColour kMatchFill(255, 235, 59);
const wxColour kCurrentMatchOutline(255, 138, 0);
const wxColour kSelectionFill(51, 122, 255);

}  // namespace

ViewerPane::ViewerPane(wxWindow* parent, FitMode initial_fit)
    : wxPanel(parent, wxID_ANY),
      fit_mode_(initial_fit),
      alive_(std::make_shared<std::atomic<bool>>(true)),
      generation_(std::make_shared<std::atomic<unsigned>>(0))
{
    build_ui();
    message_ = L"Select a PDF to view it here.";
}

ViewerPane::~ViewerPane()
{
    // Order matters.  Clearing the flag first means any result the worker
    // posts from here on is dropped rather than touching a half-destroyed
    // panel; joining second guarantees the thread is gone before the members
    // it borrows are.
    alive_->store(false);
    cancel_search();
}

void ViewerPane::build_ui()
{
    auto* outer = new wxBoxSizer(wxVERTICAL);

    // -- content search bar --
    auto* search_row = new wxBoxSizer(wxHORIZONTAL);
    search_box_ = new wxTextCtrl(this, wxID_ANY, wxEmptyString, wxDefaultPosition,
                                 wxDefaultSize, wxTE_PROCESS_ENTER);
    search_box_->SetHint(L"Search in this PDF…");
    auto* find_prev = new wxButton(this, wxID_ANY, L"▲", wxDefaultPosition,
                                   wxSize(32, -1));
    auto* find_next = new wxButton(this, wxID_ANY, L"▼", wxDefaultPosition,
                                   wxSize(32, -1));
    auto* clear = new wxButton(this, wxID_ANY, L"✕", wxDefaultPosition,
                               wxSize(32, -1));
    find_prev->SetToolTip(L"Previous match (Shift+F3)");
    find_next->SetToolTip(L"Next match (F3)");
    clear->SetToolTip(L"Clear the search");
    match_label_ = new wxStaticText(this, wxID_ANY, wxEmptyString,
                                    wxDefaultPosition, wxSize(70, -1));

    search_row->Add(search_box_, 1, wxALL | wxALIGN_CENTER_VERTICAL, 2);
    search_row->Add(find_prev, 0, wxALL, 2);
    search_row->Add(find_next, 0, wxALL, 2);
    search_row->Add(clear, 0, wxALL, 2);
    search_row->Add(match_label_, 0, wxALIGN_CENTER_VERTICAL | wxLEFT, 4);

    // -- navigation bar --
    // A wrap sizer, not a box sizer: the viewer pane is user-resizable down to
    // the splitter's minimum, and a horizontal box silently clips whatever
    // does not fit off the right edge rather than reflowing.  "Fit page"
    // disappeared entirely at narrow widths before this.
    // wxREMOVE_LEADING_SPACES only, NOT the default flags.
    //
    // wxWRAPSIZER_DEFAULT_FLAGS includes wxEXTEND_LAST_ON_EACH_LINE, which
    // stretches the last control on every row to fill the remaining width.
    // Once the bar wraps, that made "Page" a full-width button on a row of
    // its own -- it looked like a layout bug rather than a wrapped toolbar.
    auto* nav = new wxWrapSizer(wxHORIZONTAL, wxREMOVE_LEADING_SPACES);
    prev_button_ = new wxButton(this, wxID_ANY, L"◀", wxDefaultPosition,
                                wxSize(36, -1));
    next_button_ = new wxButton(this, wxID_ANY, L"▶", wxDefaultPosition,
                                wxSize(36, -1));
    prev_button_->SetToolTip(L"Previous page");
    next_button_->SetToolTip(L"Next page");
    page_label_ = new wxStaticText(this, wxID_ANY, L"–");

    auto* zoom_out = new wxButton(this, wxID_ANY, L"−", wxDefaultPosition,
                                  wxSize(32, -1));
    auto* zoom_in = new wxButton(this, wxID_ANY, L"+", wxDefaultPosition,
                                 wxSize(32, -1));
    // Short labels: the nav bar has to survive the viewer pane being dragged
    // narrow, and "Fit width"/"Fit page" overflowed and clipped at the right
    // edge before the stretch spacer could give anything back.
    auto* fit_width_button = new wxButton(this, wxID_ANY, L"Width",
                                          wxDefaultPosition, wxSize(56, -1));
    auto* fit_page_button = new wxButton(this, wxID_ANY, L"Page",
                                         wxDefaultPosition, wxSize(56, -1));
    highlight_button_ = new wxButton(this, wxID_ANY, L"Highlight");
    highlight_button_->SetToolTip(
        L"Save the selected words as a highlight annotation");
    highlight_button_->Enable(false);
    zoom_out->SetToolTip(L"Zoom out");
    zoom_in->SetToolTip(L"Zoom in");
    fit_width_button->SetToolTip(L"Fit the page width to the window");
    fit_page_button->SetToolTip(L"Fit the whole page in the window");

    nav->Add(prev_button_, 0, wxALL, 2);
    nav->Add(next_button_, 0, wxALL, 2);
    nav->Add(page_label_, 0, wxALIGN_CENTER_VERTICAL | wxLEFT | wxRIGHT, 8);
    nav->Add(highlight_button_, 0, wxALL, 2);
    nav->Add(zoom_out, 0, wxALL, 2);
    nav->Add(zoom_in, 0, wxALL, 2);
    nav->Add(fit_width_button, 0, wxALL, 2);
    nav->Add(fit_page_button, 0, wxALL, 2);

    // -- page canvas --
    canvas_ = new wxScrolledWindow(this, wxID_ANY, wxDefaultPosition,
                                   wxDefaultSize, wxVSCROLL | wxHSCROLL);
    canvas_->SetScrollRate(kScrollUnit, kScrollUnit);
    canvas_->SetBackgroundColour(wxColour(0x50, 0x50, 0x50));
    // Required for wxAutoBufferedPaintDC to do its job; without it the page
    // flickers badly on every scroll.
    canvas_->SetBackgroundStyle(wxBG_STYLE_PAINT);

    outer->Add(search_row, 0, wxEXPAND);
    outer->Add(nav, 0, wxEXPAND);
    outer->Add(canvas_, 1, wxEXPAND);
    SetSizer(outer);

    canvas_->Bind(wxEVT_PAINT, &ViewerPane::on_paint, this);
    canvas_->Bind(wxEVT_SIZE, &ViewerPane::on_size, this);
    canvas_->Bind(wxEVT_MOUSEWHEEL, &ViewerPane::on_mouse_wheel, this);
    canvas_->Bind(wxEVT_LEFT_DOWN, &ViewerPane::on_left_down, this);
    canvas_->Bind(wxEVT_MOTION, &ViewerPane::on_motion, this);
    canvas_->Bind(wxEVT_LEFT_UP, &ViewerPane::on_left_up, this);
    canvas_->Bind(wxEVT_CONTEXT_MENU, &ViewerPane::on_context_menu, this);

    prev_button_->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { prev_page(); });
    next_button_->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { next_page(); });
    zoom_out->Bind(wxEVT_BUTTON,
                   [this](wxCommandEvent&) { change_zoom(1.0F / kZoomStep); });
    zoom_in->Bind(wxEVT_BUTTON,
                  [this](wxCommandEvent&) { change_zoom(kZoomStep); });
    fit_width_button->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { fit_width(); });
    fit_page_button->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { fit_page(); });
    highlight_button_->Bind(wxEVT_BUTTON,
                            [this](wxCommandEvent&) { highlight_selection(); });

    find_next->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { next_match(); });
    find_prev->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) { prev_match(); });
    clear->Bind(wxEVT_BUTTON, [this](wxCommandEvent&) {
        search_box_->ChangeValue(wxEmptyString);
        cancel_search();
        matches_.clear();
        current_match_ = -1;
        needle_.clear();
        update_match_label();
        canvas_->Refresh();
    });

    search_box_->Bind(wxEVT_TEXT, [this](wxCommandEvent&) {
        start_search(search_box_->GetValue().utf8_string());
    });
    search_box_->Bind(wxEVT_TEXT_ENTER, [this](wxCommandEvent& event) {
        if (wxGetKeyState(WXK_SHIFT)) {
            prev_match();
        } else {
            next_match();
        }
        event.Skip(false);
    });
}

bool ViewerPane::load(const fs::path& pdf_path, int page_index)
{
    cancel_search();
    matches_.clear();
    current_match_ = -1;
    selected_rects_.clear();
    select_anchor_.reset();
    select_drag_.reset();

    if (!doc_.open(pdf_path)) {
        unload();
        message_ = wxString::FromUTF8("Could not open " +
                                      path_to_utf8(pdf_path.filename()) + ":\n" +
                                      doc_.last_error());
        canvas_->Refresh();
        return false;
    }

    path_ = pdf_path;
    page_count_ = doc_.page_count();
    current_page_ = std::clamp(page_index, 0, (page_count_ > 0) ? page_count_ - 1 : 0);
    message_.clear();
    apply_fit();
    render_current_page();

    // The first fit runs against whatever size the canvas has *right now*,
    // which during startup is not yet its laid-out size -- the page then sits
    // at roughly half width until something else triggers a resize.  Re-fit
    // once the pending layout has been applied.
    CallAfter([this]() {
        if (doc_.is_open() && fit_mode_ != FitMode::kNone) {
            const float before = zoom_;
            apply_fit();
            if (zoom_ != before) {
                render_current_page();
            }
        }
    });

    // A filter stays applied across PDFs, as in the Python app: the search
    // re-runs against the new document but the view stays on the current page
    // until the user steps to a match.
    if (!needle_.empty()) {
        start_search(needle_);
    }
    return true;
}

void ViewerPane::unload()
{
    cancel_search();
    doc_.close();
    path_.clear();
    page_count_ = 0;
    current_page_ = 0;
    page_bitmap_ = wxBitmap();
    matches_.clear();
    current_match_ = -1;
    selected_rects_.clear();
    message_ = L"Select a PDF to view it here.";
    canvas_->SetVirtualSize(0, 0);
    update_nav_label();
    update_match_label();
    canvas_->Refresh();
}

void ViewerPane::goto_page(int page_index)
{
    if (!doc_.is_open() || page_count_ <= 0) {
        return;
    }
    const int clamped = std::clamp(page_index, 0, page_count_ - 1);
    if (clamped == current_page_ && page_bitmap_.IsOk()) {
        return;
    }
    current_page_ = clamped;
    selected_rects_.clear();
    apply_fit();
    render_current_page();
    canvas_->Scroll(0, 0);
    if (on_page_changed_) {
        on_page_changed_(current_page_);
    }
}

void ViewerPane::change_zoom(float factor)
{
    assert(factor > 0.0F);
    if (!doc_.is_open()) {
        return;
    }
    const float wanted = std::clamp(zoom_ * factor, kMinZoom, kMaxZoom);
    if (wanted == zoom_) {
        return;
    }
    zoom_ = wanted;
    // A manual zoom cancels the fit; it is re-applied only when the user asks
    // for a fit again, matching how the Python app treats fit_mode.
    fit_mode_ = FitMode::kNone;
    render_current_page();
}

void ViewerPane::fit_width()
{
    fit_mode_ = FitMode::kWidth;
    apply_fit();
    render_current_page();
    if (on_fit_changed_) {
        on_fit_changed_(fit_mode_);
    }
}

void ViewerPane::fit_page()
{
    fit_mode_ = FitMode::kPage;
    apply_fit();
    render_current_page();
    if (on_fit_changed_) {
        on_fit_changed_(fit_mode_);
    }
}

void ViewerPane::apply_fit()
{
    if (!doc_.is_open() || fit_mode_ == FitMode::kNone) {
        return;
    }
    const Rect bounds = doc_.page_bounds(current_page_);
    if (bounds.width() <= 0.0F || bounds.height() <= 0.0F) {
        return;
    }

    const wxSize client = canvas_->GetClientSize();
    if (client.x <= 0 || client.y <= 0) {
        return;  // not laid out yet; the first wxEVT_SIZE re-runs this
    }

    // Leave room for the scrollbar the fit itself may introduce, so fit-width
    // does not oscillate between "fits" and "needs a horizontal scrollbar".
    const int margin = wxSystemSettings::GetMetric(wxSYS_VSCROLL_X) + 4;
    const float by_width =
        static_cast<float>(client.x - margin) / bounds.width();

    float wanted = by_width;
    if (fit_mode_ == FitMode::kPage) {
        const float by_height =
            static_cast<float>(client.y - 4) / bounds.height();
        wanted = std::min(by_width, by_height);
    }
    zoom_ = std::clamp(wanted, kMinZoom, kMaxZoom);
}

void ViewerPane::render_current_page()
{
    if (!doc_.is_open()) {
        return;
    }

    RenderedPage rendered;
    if (!doc_.render_page(current_page_, zoom_, &rendered)) {
        page_bitmap_ = wxBitmap();
        message_ = wxString::FromUTF8("Could not render page: " +
                                      doc_.last_error());
        canvas_->Refresh();
        return;
    }

    // wxImage frees the buffer it is given, so it gets its own copy;
    // `rendered` owns a vector that unwinds normally.
    auto* buffer = static_cast<unsigned char*>(std::malloc(rendered.rgb.size()));
    if (buffer == nullptr) {
        message_ = L"Out of memory rendering the page.";
        canvas_->Refresh();
        return;
    }
    std::memcpy(buffer, rendered.rgb.data(), rendered.rgb.size());

    wxImage image(rendered.width, rendered.height, buffer);
    page_bitmap_ = wxBitmap(image);
    message_.clear();

    canvas_->SetVirtualSize(rendered.width, rendered.height);
    update_nav_label();
    canvas_->Refresh();
}

void ViewerPane::update_nav_label()
{
    if (page_label_ == nullptr) {
        return;
    }
    if (!doc_.is_open() || page_count_ <= 0) {
        page_label_->SetLabel(L"–");
    } else {
        page_label_->SetLabel(
            wxString::Format("%d / %d", current_page_ + 1, page_count_));
    }
    if (prev_button_ != nullptr) {
        prev_button_->Enable(doc_.is_open() && current_page_ > 0);
    }
    if (next_button_ != nullptr) {
        next_button_->Enable(doc_.is_open() && current_page_ + 1 < page_count_);
    }
    if (highlight_button_ != nullptr) {
        highlight_button_->Enable(!selected_rects_.empty());
    }
    Layout();
}

void ViewerPane::update_match_label()
{
    if (match_label_ == nullptr) {
        return;
    }
    if (needle_.empty()) {
        match_label_->SetLabel(wxEmptyString);
    } else if (matches_.empty()) {
        match_label_->SetLabel(scan_running_ ? L"…" : L"0");
    } else {
        match_label_->SetLabel(wxString::Format(
            "%d / %zu", current_match_ + 1, matches_.size()));
    }
    Layout();
}

// ---------------------------------------------------------------------------
// Content search
// ---------------------------------------------------------------------------

void ViewerPane::cancel_search()
{
    // Bumping the generation is what actually stops results being applied; the
    // worker itself checks it each page and returns early.
    generation_->fetch_add(1);
    if (worker_.joinable()) {
        worker_.join();
    }
    scan_running_ = false;
}

void ViewerPane::start_search(const std::string& needle)
{
    cancel_search();
    matches_.clear();
    current_match_ = -1;
    needle_ = needle;
    wanted_page_ = -1;   // show_search() sets it again after this returns

    if (needle.empty() || !doc_.is_open()) {
        update_match_label();
        canvas_->Refresh();
        return;
    }

    scan_running_ = true;
    update_match_label();

    const unsigned generation = generation_->load();
    auto alive = alive_;
    auto generation_cell = generation_;
    const fs::path path = path_;
    const int pages = page_count_;

    // The worker opens its OWN document rather than borrowing doc_: one
    // PdfDocument owns one fz_context and is not safe to share across threads,
    // and reopening costs microseconds against a scan that reads every page.
    worker_ = std::thread([this, alive, generation_cell, generation, path, pages]() {
        // An uncaught exception in a worker is a silent std::terminate with
        // exit code 0xC0000409 and no dialog, so the whole body is guarded.
        try {
            PdfDocument scanner;
            if (!scanner.open(path)) {
                return;
            }
            for (int page = 0; page < pages; ++page) {
                if (generation_cell->load() != generation) {
                    return;  // superseded by newer input
                }
                std::vector<Rect> hits;
                if (!scanner.search_page(page, needle_, &hits) || hits.empty()) {
                    continue;
                }
                std::vector<std::pair<int, Rect>> batch;
                batch.reserve(hits.size());
                for (const Rect& hit : hits) {
                    batch.emplace_back(page, hit);
                }
                // Post each page's hits as they are found, so the counter
                // fills in progressively on a long document instead of the UI
                // sitting blank until the whole scan finishes.
                wxTheApp->CallAfter([this, alive, generation_cell, generation,
                                     batch = std::move(batch)]() {
                    if (!alive->load() || generation_cell->load() != generation) {
                        return;
                    }
                    const bool first = matches_.empty();
                    const std::size_t start = matches_.size();
                    matches_.insert(matches_.end(), batch.begin(), batch.end());
                    // Batches arrive in page order, one page each, so the
                    // first batch at or past the wanted page holds its match.
                    if (wanted_page_ >= 0) {
                        if (batch.front().first >= wanted_page_) {
                            wanted_page_ = -1;
                            select_match(start);
                        } else {
                            update_match_label();
                        }
                    } else if (first) {
                        select_match(0);
                    } else {
                        update_match_label();
                        canvas_->Refresh();
                    }
                });
            }
            wxTheApp->CallAfter([this, alive, generation_cell, generation]() {
                if (!alive->load() || generation_cell->load() != generation) {
                    return;
                }
                scan_running_ = false;
                wanted_page_ = -1;   // no match from that page on: stay put
                update_match_label();
            });
        } catch (...) {
            // Nothing useful to do here, but dying quietly beats terminating
            // the process.
        }
    });
}

void ViewerPane::step_match(int delta)
{
    if (matches_.empty()) {
        return;
    }
    const int count = static_cast<int>(matches_.size());
    // Wraps at both ends, as F3 does in the Python app.
    const int next = ((current_match_ + delta) % count + count) % count;
    select_match(static_cast<std::size_t>(next));
}

void ViewerPane::select_match(std::size_t index)
{
    if (index >= matches_.size()) {
        return;
    }
    current_match_ = static_cast<int>(index);
    const auto& match = matches_[index];
    if (match.first != current_page_) {
        goto_page(match.first);
    }
    update_match_label();
    scroll_rect_into_view(match.second);
    canvas_->Refresh();
}

void ViewerPane::scroll_rect_into_view(const Rect& rect)
{
    if (canvas_ == nullptr) {
        return;
    }
    const wxRect target = to_canvas(rect);
    const wxSize client = canvas_->GetClientSize();

    // Put the hit a third of the way down rather than hard against the top
    // edge, so its surrounding context is visible too.
    const int wanted_y = std::max(0, target.GetTop() - client.y / 3);
    const int wanted_x = std::max(0, target.GetLeft() - client.x / 3);
    canvas_->Scroll(wanted_x / kScrollUnit, wanted_y / kScrollUnit);
}

void ViewerPane::show_search(const std::string& needle, int page_index)
{
    if (!doc_.is_open() || search_box_ == nullptr || page_count_ <= 0) {
        return;
    }
    goto_page(page_index);
    // ChangeValue, not SetValue: SetValue raises wxEVT_TEXT, whose handler
    // would start a second search that selects the document's first match.
    search_box_->ChangeValue(wxString::FromUTF8(needle));
    start_search(needle);
    // After start_search(), which clears it.  The worker's results reach the
    // UI thread through CallAfter, so none can have been applied yet.
    if (!needle.empty()) {
        wanted_page_ = std::clamp(page_index, 0, page_count_ - 1);
    }
    assert(wanted_page_ < page_count_);
}

void ViewerPane::focus_search()
{
    if (search_box_ != nullptr) {
        search_box_->SetFocus();
        search_box_->SelectAll();
    }
}

// ---------------------------------------------------------------------------
// Coordinates
// ---------------------------------------------------------------------------

wxPoint ViewerPane::to_canvas(float x, float y) const
{
    return wxPoint(static_cast<int>(x * zoom_), static_cast<int>(y * zoom_));
}

wxRect ViewerPane::to_canvas(const Rect& rect) const
{
    const wxPoint top_left = to_canvas(rect.x0, rect.y0);
    const wxPoint bottom_right = to_canvas(rect.x1, rect.y1);
    return wxRect(top_left, bottom_right);
}

void ViewerPane::to_pdf(const wxPoint& device, float* x, float* y) const
{
    assert(x != nullptr && y != nullptr);
    int canvas_x = 0;
    int canvas_y = 0;
    canvas_->CalcUnscrolledPosition(device.x, device.y, &canvas_x, &canvas_y);
    *x = static_cast<float>(canvas_x) / zoom_;
    *y = static_cast<float>(canvas_y) / zoom_;
}

// ---------------------------------------------------------------------------
// Painting
// ---------------------------------------------------------------------------

void ViewerPane::on_paint(wxPaintEvent&)
{
    wxAutoBufferedPaintDC dc(canvas_);
    dc.SetBackground(wxBrush(canvas_->GetBackgroundColour()));
    dc.Clear();
    canvas_->DoPrepareDC(dc);

    if (!page_bitmap_.IsOk()) {
        if (!message_.empty()) {
            dc.SetTextForeground(*wxWHITE);
            const wxSize client = canvas_->GetClientSize();
            const wxSize extent = dc.GetMultiLineTextExtent(message_);
            dc.DrawText(message_, (client.x - extent.x) / 2,
                        (client.y - extent.y) / 2);
        }
        return;
    }

    dc.DrawBitmap(page_bitmap_, 0, 0, false);

    // Overlays go through a graphics context so they can be translucent; a
    // solid brush would hide the very text the user is looking at.
    std::unique_ptr<wxGraphicsContext> gc(wxGraphicsContext::Create(dc));
    if (gc == nullptr) {
        return;
    }
    double origin_x = 0.0;
    double origin_y = 0.0;
    int scroll_x = 0;
    int scroll_y = 0;
    canvas_->CalcScrolledPosition(0, 0, &scroll_x, &scroll_y);
    origin_x = scroll_x;
    origin_y = scroll_y;
    gc->Translate(origin_x, origin_y);

    for (std::size_t i = 0; i < matches_.size(); ++i) {
        if (matches_[i].first != current_page_) {
            continue;
        }
        const wxRect box = to_canvas(matches_[i].second);
        gc->SetBrush(wxBrush(wxColour(kMatchFill.Red(), kMatchFill.Green(),
                                      kMatchFill.Blue(), 110)));
        gc->SetPen(*wxTRANSPARENT_PEN);
        gc->DrawRectangle(box.x, box.y, box.width, box.height);

        if (static_cast<int>(i) == current_match_) {
            gc->SetBrush(*wxTRANSPARENT_BRUSH);
            gc->SetPen(wxPen(kCurrentMatchOutline, 2));
            gc->DrawRectangle(box.x, box.y, box.width, box.height);
        }
    }

    for (const Rect& rect : selected_rects_) {
        const wxRect box = to_canvas(rect);
        gc->SetBrush(wxBrush(wxColour(kSelectionFill.Red(), kSelectionFill.Green(),
                                      kSelectionFill.Blue(), 90)));
        gc->SetPen(*wxTRANSPARENT_PEN);
        gc->DrawRectangle(box.x, box.y, box.width, box.height);
    }

    // The rubber band itself, while the drag is in progress.
    if (select_anchor_.has_value() && select_drag_.has_value()) {
        const wxPoint a = to_canvas(select_anchor_->first, select_anchor_->second);
        const wxPoint b = to_canvas(select_drag_->first, select_drag_->second);
        const wxRect band(a, b);
        gc->SetBrush(*wxTRANSPARENT_BRUSH);
        gc->SetPen(wxPen(kSelectionFill, 1, wxPENSTYLE_SHORT_DASH));
        gc->DrawRectangle(band.x, band.y, band.width, band.height);
    }
}

void ViewerPane::on_size(wxSizeEvent& event)
{
    if (doc_.is_open() && fit_mode_ != FitMode::kNone) {
        const float before = zoom_;
        apply_fit();
        if (zoom_ != before) {
            render_current_page();
        }
    }
    event.Skip();
}

void ViewerPane::on_mouse_wheel(wxMouseEvent& event)
{
    // Ctrl+wheel zooms.
    if (event.ControlDown()) {
        change_zoom((event.GetWheelRotation() > 0) ? kZoomStep : 1.0F / kZoomStep);
        return;
    }

    if (!doc_.is_open() || event.GetWheelRotation() == 0) {
        event.Skip();
        return;
    }

    // Plain wheel reads continuously: it scrolls within the page, and rolling
    // past an edge turns to the next or previous one.  Paging unconditionally
    // would make a zoomed-in page impossible to read, and scrolling only would
    // strand the reader at the bottom of every page -- so the wheel does
    // whichever is left to do.
    const bool down = event.GetWheelRotation() < 0;

    int unit_x = 0;
    int unit_y = 0;
    canvas_->GetScrollPixelsPerUnit(&unit_x, &unit_y);
    int view_x = 0;
    int view_y = 0;
    canvas_->GetViewStart(&view_x, &view_y);
    int virtual_w = 0;
    int virtual_h = 0;
    canvas_->GetVirtualSize(&virtual_w, &virtual_h);
    const wxSize client = canvas_->GetClientSize();

    const int top = view_y * ((unit_y > 0) ? unit_y : 1);
    // A page shorter than the viewport is "at both ends" at once, which is
    // exactly right: in fit-page every scroll should turn the page.
    const bool at_top = top <= 0;
    const bool at_bottom = (top + client.y) >= (virtual_h - 1);

    if (down && at_bottom) {
        if (current_page_ + 1 < page_count_) {
            goto_page(current_page_ + 1);   // lands at the top of the new page
        }
        return;
    }
    if (!down && at_top) {
        if (current_page_ > 0) {
            goto_page(current_page_ - 1);
            // Coming backwards, land at the BOTTOM of the previous page, so
            // reading up is the mirror of reading down rather than a jump to
            // a place the reader has already been.
            int new_w = 0;
            int new_h = 0;
            canvas_->GetVirtualSize(&new_w, &new_h);
            const int last = std::max(0, new_h - canvas_->GetClientSize().y);
            canvas_->Scroll(0, (unit_y > 0) ? (last / unit_y) : 0);
        }
        return;
    }

    event.Skip();  // ordinary scrolling within the page
}

// ---------------------------------------------------------------------------
// Selection and highlights
// ---------------------------------------------------------------------------

void ViewerPane::on_left_down(wxMouseEvent& event)
{
    if (!doc_.is_open()) {
        event.Skip();
        return;
    }
    float x = 0.0F;
    float y = 0.0F;
    to_pdf(event.GetPosition(), &x, &y);
    select_anchor_ = std::make_pair(x, y);
    select_drag_.reset();
    selected_rects_.clear();
    update_nav_label();
    canvas_->CaptureMouse();
    canvas_->Refresh();
}

void ViewerPane::on_motion(wxMouseEvent& event)
{
    if (!select_anchor_.has_value() || !event.Dragging()) {
        event.Skip();
        return;
    }
    float x = 0.0F;
    float y = 0.0F;
    to_pdf(event.GetPosition(), &x, &y);
    select_drag_ = std::make_pair(x, y);
    canvas_->Refresh();
}

void ViewerPane::on_left_up(wxMouseEvent& event)
{
    if (canvas_->HasCapture()) {
        canvas_->ReleaseMouse();
    }
    if (!select_anchor_.has_value() || !select_drag_.has_value()) {
        select_anchor_.reset();
        select_drag_.reset();
        event.Skip();
        return;
    }

    Rect band;
    band.x0 = std::min(select_anchor_->first, select_drag_->first);
    band.y0 = std::min(select_anchor_->second, select_drag_->second);
    band.x1 = std::max(select_anchor_->first, select_drag_->first);
    band.y1 = std::max(select_anchor_->second, select_drag_->second);

    select_anchor_.reset();
    select_drag_.reset();

    // Selection snaps to whole words: a highlight covering half a glyph is
    // never what was meant, and the annotation quad points want word boxes.
    std::vector<Word> words;
    if (doc_.page_words(current_page_, &words)) {
        for (const Word& word : words) {
            if (word.rect.intersects(band)) {
                selected_rects_.push_back(word.rect);
            }
        }
    }

    update_nav_label();
    canvas_->Refresh();
}

void ViewerPane::highlight_selection()
{
    if (selected_rects_.empty() || !doc_.is_open()) {
        return;
    }
    if (!doc_.add_highlight(current_page_, selected_rects_)) {
        wxMessageBox(wxString::FromUTF8("Could not add the highlight:\n\n" +
                                        doc_.last_error()),
                     L"Highlight", wxOK | wxICON_ERROR, this);
        return;
    }
    selected_rects_.clear();
    render_current_page();  // re-render so the annotation itself is drawn
    update_nav_label();
}

void ViewerPane::on_context_menu(wxContextMenuEvent& event)
{
    if (!doc_.is_open()) {
        return;
    }
    const wxPoint screen = event.GetPosition();
    const wxPoint local = (screen == wxDefaultPosition)
                              ? wxPoint(0, 0)
                              : canvas_->ScreenToClient(screen);
    float x = 0.0F;
    float y = 0.0F;
    to_pdf(local, &x, &y);
    menu_point_ = std::make_pair(x, y);

    wxMenu menu;
    const int kBookmark = wxWindow::NewControlId();
    const int kHighlight = wxWindow::NewControlId();
    const int kRemove = wxWindow::NewControlId();
    const int kSave = wxWindow::NewControlId();

    menu.Append(kBookmark, L"Bookmark this page");
    if (!selected_rects_.empty()) {
        menu.Append(kHighlight, L"Highlight selection");
    }
    menu.Append(kRemove, L"Remove highlight here");
    menu.AppendSeparator();
    menu.Append(kSave, L"Save highlights…");
    menu.Enable(kSave, doc_.dirty());

    menu.Bind(wxEVT_MENU, [this](wxCommandEvent&) {
        if (on_bookmark_requested_) {
            on_bookmark_requested_(current_page_ + 1);
        }
    }, kBookmark);

    menu.Bind(wxEVT_MENU,
              [this](wxCommandEvent&) { highlight_selection(); }, kHighlight);

    menu.Bind(wxEVT_MENU, [this](wxCommandEvent&) {
        if (!menu_point_.has_value()) {
            return;
        }
        if (doc_.remove_highlight_at(current_page_, menu_point_->first,
                                     menu_point_->second)) {
            render_current_page();
        }
    }, kRemove);

    menu.Bind(wxEVT_MENU,
              [this](wxCommandEvent&) { (void)maybe_save_annotations(); }, kSave);

    PopupMenu(&menu);
}

bool ViewerPane::maybe_save_annotations()
{
    if (!doc_.is_open() || !doc_.dirty()) {
        return true;
    }

    // manual.pdf -> manual(ann).pdf, beside the original.
    const std::string stem = path_to_utf8(path_.stem());
    const fs::path ann_path =
        path_.parent_path() /
        path_from_utf8(stem + "(ann)" + path_to_utf8(path_.extension()));

    bool save_copy = false;
    // An "(ann)" copy is already the annotated file: save it in place without
    // asking, so the user never accumulates "(ann)(ann)" names.
    if (stem.size() < 5 || stem.compare(stem.size() - 5, 5, "(ann)") != 0) {
        wxMessageDialog dialog(
            this,
            wxString::FromUTF8(
                "Save highlights as a separate copy?\n\n"
                "Yes  -  save as " + path_to_utf8(ann_path.filename()) + "\n"
                "No   -  save into " + path_to_utf8(path_.filename())),
            L"Save highlights", wxYES_NO | wxCANCEL | wxICON_QUESTION);
        const int answer = dialog.ShowModal();
        if (answer == wxID_CANCEL) {
            return false;  // keep the changes pending
        }
        save_copy = (answer == wxID_YES);
    }

    if (save_copy) {
        if (!doc_.save_as(ann_path)) {
            wxMessageBox(wxString::FromUTF8("Could not save\n" +
                                            path_to_utf8(ann_path) + ":\n\n" +
                                            doc_.last_error()),
                         L"Save failed", wxOK | wxICON_ERROR, this);
            return true;
        }
        // Give the copy the same topics and bookmarks, so it lists cleanly
        // beside the original rather than as an untagged stray.
        std::error_code ec;
        if (const auto meta = find_metadata_path(path_); meta.has_value()) {
            fs::path ann_meta = ann_path;
            ann_meta.replace_extension(meta->extension());
            if (!fs::exists(ann_meta, ec)) {
                fs::copy_file(*meta, ann_meta, ec);
            }
        }
        const fs::path bm = bookmarks_path(path_);
        if (fs::is_regular_file(bm, ec)) {
            const fs::path ann_bm = bookmarks_path(ann_path);
            if (!fs::exists(ann_bm, ec)) {
                fs::copy_file(bm, ann_bm, ec);
            }
        }
        if (on_documents_changed_) {
            on_documents_changed_();
        }
    } else {
        if (doc_.is_signed() &&
            wxMessageBox(L"This PDF is digitally signed; saving highlights may "
                         L"invalidate the signature.\n\nSave anyway?",
                         L"Signed PDF", wxYES_NO | wxICON_WARNING,
                         this) != wxYES) {
            return true;
        }
        if (!doc_.save_incremental()) {
            wxMessageBox(wxString::FromUTF8("Could not save highlights to\n" +
                                            path_to_utf8(path_) + ":\n\n" +
                                            doc_.last_error()),
                         L"Save failed", wxOK | wxICON_ERROR, this);
        }
    }
    return true;
}

}  // namespace pdfboss
