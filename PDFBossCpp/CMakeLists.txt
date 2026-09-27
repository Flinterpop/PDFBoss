# PDFBoss -- C++ port.
#
# This is the reference implementation.  The Python app at the repo root
# (app.py, tocgen.py) is retained as historical reference only; a difference
# from it is not automatically a bug here.
#
# Dependencies come from vcpkg in CLASSIC mode, not a manifest.  A vcpkg.json
# here would switch the toolchain to manifest mode and rebuild wxWidgets from
# source into build/vcpkg_installed -- a long detour for no gain, since the
# packages are already installed machine-wide.  Install them once with:
#
#   C:\vcpkg\vcpkg.exe install wxwidgets:x64-windows-static ^
#                              md4c:x64-windows-static ^
#                              catch2:x64-windows-static ^
#                              nlohmann-json:x64-windows-static
#
# MuPDF is NOT a vcpkg package: there is no pdfium port, poppler is GPL, and
# podofo cannot rasterize.  It is built from a pinned sibling tree; see
# cmake/MuPdf.cmake.
#
# Configure, build and test:
#
#   cmake --preset windows-static
#   cmake --build build --config Release
#   ctest --test-dir build -C Release --output-on-failure

cmake_minimum_required(VERSION 3.21)

# Kept in step with app/Version.h by release.ps1; a test enforces it.
project(PDFBossCpp VERSION 2.4.1 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Static CRT so the shipped exe needs no VC++ redistributable.  Verify with
# `dumpbin /dependents` -- KERNEL32.dll and friends only, no VCRUNTIME /
# MSVCP / api-ms-win-crt.  MuPDF is built /MT to match; mixing the two is not
# a warning, it is two heaps and a crash at the first cross-module free.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

# First-party code is warning-clean at /W4 /WX.  Third-party headers come in
# through SYSTEM includes so their warnings do not land here.
add_compile_options(/W4 /WX /utf-8 /permissive- /external:W0)

# windows.h defines min/max as macros, which then break std::min/std::max at
# every call site that transitively includes it -- and the error ("illegal
# token on right side of ::") names neither windows.h nor the macro.  Defined
# once here rather than per-file, so a new file that includes windows.h cannot
# reintroduce it.  WIN32_LEAN_AND_MEAN keeps the header cheap besides.
add_compile_definitions(NOMINMAX WIN32_LEAN_AND_MEAN)

# MuPDF's static libraries are built with /GL, so the link needs /LTCG; MSVC
# otherwise restarts the link with a warning that /WX turns into an error.
add_link_options($<$<CONFIG:Release>:/LTCG>)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
include(MuPdf)

find_package(nlohmann_json CONFIG REQUIRED)

enable_testing()

add_subdirectory(app)
add_subdirectory(tests)
