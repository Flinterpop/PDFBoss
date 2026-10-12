# pdfboss_core -- everything that does not touch the GUI.
#
# The split is deliberate: document handling, sidecar files, settings and topic
# generation are all testable headlessly, and keeping them out of the wxWidgets
# target means the test suite neither links nor initialises a GUI toolkit.

add_library(pdfboss_core STATIC
    Config.cpp
    Config.h
    FavoritesFile.cpp
    FavoritesFile.h
    Metadata.cpp
    Metadata.h
    PathUtf8.cpp
    PathUtf8.h
    PdfDocument.cpp
    PdfDocument.h
    TocGen.cpp
    TocGen.h
    # The updater's logic lives here, not in the GUI target, so the version
    # comparison, the installer's arguments and the handoff batch text can be
    # unit-tested without starting wxWidgets.  Nothing in it includes wx.
    Updater.cpp
    Updater.h
    Version.h
)

target_include_directories(pdfboss_core PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")

target_link_libraries(pdfboss_core
    PUBLIC
        nlohmann_json::nlohmann_json
    PRIVATE
        # PRIVATE: only PdfDocument.cpp may include a MuPDF header, and making
        # the dependency private is what enforces that at build time rather
        # than by convention.  See the comment at the top of PdfDocument.h.
        mupdf
        # ShellExecuteExW, which the updater uses to start the installer
        # elevated.
        shell32
)

# -- The application ---------------------------------------------------------

find_package(wxWidgets CONFIG REQUIRED COMPONENTS core base html net)
find_package(md4c CONFIG REQUIRED)

add_executable(PDFBoss WIN32
    main.cpp
    DropTarget.cpp
    DropTarget.h
    FoldersDialog.cpp
    FoldersDialog.h
    HelpDialog.cpp
    HelpDialog.h
    MainFrame.cpp
    MainFrame.h
    PdfListPane.cpp
    PdfListPane.h
    ProgressJob.cpp
    ProgressJob.h
    TopicsPane.cpp
    TopicsPane.h
    UpdateCheck.cpp
    UpdateCheck.h
    ViewerPane.cpp
    ViewerPane.h
    # Listed as a source so MSBuild embeds it.  Without the manifest the
    # process is DPI-virtualised: a blurry page, and every coordinate the
    # viewer computes disagreeing with what the OS reports.
    PDFBoss.manifest
    # The application icon and the version info Explorer shows.  Without this
    # the exe, its Start-Menu shortcut and its uninstall entry all get the
    # generic Windows icon.
    PDFBoss.rc
)

target_link_libraries(PDFBoss PRIVATE
    pdfboss_core
    wx::core
    wx::base
    wx::html
    wx::net
    md4c::md4c-html)

set_target_properties(PDFBoss PROPERTIES OUTPUT_NAME "PDFBoss")
