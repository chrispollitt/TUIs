cmake_minimum_required(VERSION 3.13)
project(tvmail VERSION 1.3.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# Before add_subdirectory(tvision), so the bundled build also gets a
# CTestTestfile.cmake and `ctest` doesn't warn about a missing include.
enable_testing()

# The C++ tvmail shares with tvchat (../tvcommon; a source tarball carries
# it as ./tvcommon).  It also finds Turbo Vision and sets TV_SH.
if(NOT DEFINED TVCOMMON_DIR)
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tvcommon/tvcommon.cmake")
        set(TVCOMMON_DIR "${CMAKE_CURRENT_SOURCE_DIR}/tvcommon")
    else()
        set(TVCOMMON_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../tvcommon")
    endif()
endif()
include("${TVCOMMON_DIR}/tvcommon.cmake")

add_executable(tvmail
    src/main.cpp src/app.cpp src/mailwin.cpp src/compose.cpp
    src/addrbook.cpp src/parts.cpp src/signature.cpp src/help.cpp
    src/dialogs.cpp src/backend.cpp src/floydwin.cpp
    ${TVCOMMON_SOURCES})
tvcommon_setup(tvmail)
target_compile_definitions(tvmail PRIVATE TVMAIL_VERSION="${PROJECT_VERSION}")

# --- tests (ctest) -------------------------------------------------------
# backend_unit : stdlib unittest over tvmail-backend (mbox parsing, delete,
#                aliases, lock-sweep, serve framing) - no build needed
# backend_pipe : `tvmail --selftest` - the C++ side forks the serve
#                co-process, handshakes, round-trips a request, spawnLogged
# e2e_tui      : drives the built tvmail in a pty (launch, panes, Tab, body
#                scroll, background F3); exits 77 = skipped where no pty
# e2e_regress  : one pty case per squashed BUGS.md entry, read through a
#                pyte screen model (columns, colours); 77 = no pty or no pyte
find_program(TVMAIL_PYTHON
    NAMES python3 python3.13 python3.12 python3.11 python3.10 python3.9 python)

if(TVMAIL_PYTHON)
    add_test(NAME backend_unit
             COMMAND ${TVMAIL_PYTHON} -m unittest discover
                     -s "${CMAKE_CURRENT_SOURCE_DIR}/tests" -p "test_backend.py")
endif()

if(NOT WIN32)
    # TVMAIL_MODE=local pins it to the on-disk mbox path regardless of any
    # ~/.config/tvmail/tvmail.conf on the host - so a client machine's
    # --selftest doesn't wander off to a live IMAP server.
    add_test(NAME backend_pipe COMMAND tvmail --selftest)
    set_tests_properties(backend_pipe PROPERTIES
        ENVIRONMENT "PATH=${CMAKE_CURRENT_SOURCE_DIR}/backend:$ENV{PATH};TVMAIL_MODE=local"
        PASS_REGULAR_EXPRESSION "selftest: served=1 status=0")
    if(TVMAIL_PYTHON)
        add_test(NAME e2e_tui
                 COMMAND ${TVMAIL_PYTHON}
                         "${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_tui.py"
                         --bin $<TARGET_FILE:tvmail>
                         --backend-dir "${CMAKE_CURRENT_SOURCE_DIR}/backend")
        set_tests_properties(e2e_tui PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 120)
        add_test(NAME e2e_regress
                 COMMAND ${TVMAIL_PYTHON}
                         "${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_regress.py"
                         --bin $<TARGET_FILE:tvmail>
                         --backend-dir "${CMAKE_CURRENT_SOURCE_DIR}/backend")
        set_tests_properties(e2e_regress PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 400)
    endif()
    # install_roundtrip : cmake --install into a temp prefix, ./uninstall.sh it
    add_test(NAME install_roundtrip
             COMMAND bash "${CMAKE_CURRENT_SOURCE_DIR}/tests/install_roundtrip.sh"
                     "${CMAKE_CURRENT_BINARY_DIR}")
    # mail_setup : ../tvcommon/third_party/POSIX/mail-setup's own sandboxed tests (F3 runs
    #              its mail-pull); skipped if not fetched.  -LE mail_setup omits it
    add_test(NAME mail_setup
             COMMAND bash "${CMAKE_CURRENT_SOURCE_DIR}/tests/mail_setup.sh")
    set_tests_properties(mail_setup PROPERTIES
        SKIP_RETURN_CODE 77 TIMEOUT 300 LABELS mail_setup)
    # mingw_compile : every src/*.cpp syntax-checked by the mingw-w64 cross g++
    #                 and tvcommon's (warnings as errors) - the native Windows build;
    #                 77 = no cross compiler, or tvision found as a package
    if(DEFINED TVISION_DIR AND EXISTS "${TVISION_DIR}/include/tvision/tv.h")
        add_test(NAME mingw_compile
                 COMMAND bash "${CMAKE_CURRENT_SOURCE_DIR}/tests/mingw_compile.sh"
                         "${TVISION_DIR}/include" "${TVCOMMON_DIR}")
        set_tests_properties(mingw_compile PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 300)
    endif()
endif()

include(GNUInstallDirs)
install(TARGETS tvmail RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(PROGRAMS backend/tvmail-backend DESTINATION ${CMAKE_INSTALL_BINDIR})
# Floyd's levels and tools, shared with tvchat (the backend looks in ../share/tvmail)
install(FILES ${TVCOMMON_DIR}/py/tvfloyd.py DESTINATION ${CMAKE_INSTALL_DATADIR}/tvmail)
install(FILES man/tvmail.1 man/tvmail-backend.1
        DESTINATION ${CMAKE_INSTALL_MANDIR}/man1)
install(FILES README.md INSTALL.md HISTORY.md WHY.md LICENSE AUTHORS WARRANTY
              configure/tvmail.conf.example
        DESTINATION ${CMAKE_INSTALL_DOCDIR})

# --- tarballs (CPack) -------------------------------------------------------
# Binary tarball:  cd build && cpack           -> tvmail-1.2.0-<system>.tar.gz
# (the binary is per-platform; the source tree builds on Linux/macOS/Cygwin)
set(CPACK_GENERATOR "TGZ")
set(CPACK_PACKAGE_NAME "tvmail")
set(CPACK_PACKAGE_VENDOR "Chris Pollitt")
set(CPACK_PACKAGE_CONTACT "Chris Pollitt <chris.pollitt@gmail.com>")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Turbo Vision mail client for a local mailbox")
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_RESOURCE_FILE_LICENSE "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE")
set(CPACK_PACKAGE_FILE_NAME
    "tvmail-${PROJECT_VERSION}-${CMAKE_SYSTEM_NAME}-${CMAKE_SYSTEM_PROCESSOR}")
set(CPACK_STRIP_FILES ON)

# Source tarball:  cd build && cpack --config CPackSourceConfig.cmake
set(CPACK_SOURCE_GENERATOR "TGZ")
set(CPACK_SOURCE_PACKAGE_FILE_NAME "tvmail-${PROJECT_VERSION}")
set(CPACK_SOURCE_IGNORE_FILES
    "/build/" "/_inst/" "/third_party/" "/\\\\.git/"
    "\\\\.o$" "\\\\.bak$" "/__pycache__/" "\\\\.pyc$")
include(CPack)
