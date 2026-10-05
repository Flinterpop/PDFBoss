// Single source of truth for the version string.
//
// The version is bumped in lockstep across this file, PDFBoss.rc, the
// installer .iss, the CMake project() line and the deprecated Python app's
// APP_VERSION -- see the workspace CLAUDE.md.  release.ps1 bumps all of them,
// and test_version.cpp asserts this file agrees with CMake and with the .rc,
// so a half-applied bump fails the build rather than shipping.

#ifndef PDFBOSS_APP_VERSION_H
#define PDFBOSS_APP_VERSION_H

// A bare macro, above the RC_INVOKED guard, so the resource compiler can use
// it too: rc.exe preprocesses this header but cannot parse the C++ below.
#define PDFBOSS_VERSION_STRING "2.5.0"

#ifndef RC_INVOKED

namespace pdfboss {

inline constexpr const char* kAppVersion = PDFBOSS_VERSION_STRING;
inline constexpr const char* kAppName = "PDFBoss";

}  // namespace pdfboss

#endif  // RC_INVOKED

#endif  // PDFBOSS_APP_VERSION_H
