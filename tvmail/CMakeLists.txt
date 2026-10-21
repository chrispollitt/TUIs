cmake_minimum_required(VERSION 3.13)
project(tvmail VERSION 1.2.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# Before add_subdirectory(tvision), so the bundled build also gets a
# CTestTestfile.cmake and `ctest` doesn't warn about a missing include.
enable_testing()

# Cygwin's newlib hides XSI symbols (wcwidth, SA_NOCLDWAIT, ...) that tvision's
# Unix backend expects; _GNU_SOURCE exposes them. Must precede add_subdirectory.
if(CYGWIN)
    add_compile_definitions(_GNU_SOURCE)
    # Cygwin has no SA_NOCLDWAIT (no auto child-reaping); make it a no-op flag.
    add_compile_definitions(SA_NOCLDWAIT=0)
endif()

# POSIX shell the app runs its little helper scripts through.  /bin/sh is right
# everywhere native; the mingw cross-build (build.sh --mingw) overrides it with
# a Cygwin shell, since a native Windows .exe cannot exec a Unix /bin/sh.
set(TVMAIL_SH "/bin/sh" CACHE STRING "shell used to run tvmail-backend")

# --- locate Turbo Vision (magiblot/tvision) -----------------------------
find_package(tvision QUIET)
if(NOT TARGET tvision)
    if(NOT DEFINED TVISION_DIR)
        set(TVISION_DIR "${CMAKE_CURRENT_SOURCE_DIR}/third_party/tvision")
    endif()
    if(EXISTS "${TVISION_DIR}/CMakeLists.txt")
        message(STATUS "tvmail: building bundled Turbo Vision from ${TVISION_DIR}")
        set(TV_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
        add_subdirectory("${TVISION_DIR}" tvision-build EXCLUDE_FROM_ALL)
    else()
        message(FATAL_ERROR
            "Turbo Vision not found.\n"
            "  git clone https://github.com/magiblot/tvision \"${TVISION_DIR}\"\n"
            "or pass -DTVISION_DIR=/path/to/tvision , or install the tvision package.")
    endif()
endif()

add_executable(tvmail
    src/main.cpp src/app.cpp src/mailwin.cpp src/compose.cpp
    src/addrbook.cpp src/parts.cpp src/signature.cpp src/help.cpp
    src/dialogs.cpp src/util.cpp src/shell.cpp src/coprocess.cpp src/chat.cpp)
target_link_libraries(tvmail PRIVATE tvision)
# Treat tvision's own headers as system headers so ITS warnings (e.g. the
# __cdecl/__stdcall redefinition tv.h does on non-_WIN32 builds - Cygwin's
# GCC already has built-in definitions for both) don't show up in tvmail's
# build output. Warnings from tvmail's own sources are unaffected.
get_target_property(TVISION_INCLUDE_DIRS tvision INTERFACE_INCLUDE_DIRECTORIES)
if(TVISION_INCLUDE_DIRS)
    target_include_directories(tvmail SYSTEM PRIVATE ${TVISION_INCLUDE_DIRS})
endif()
target_include_directories(tvmail PRIVATE src)
target_compile_definitions(tvmail PRIVATE TVMAIL_SH="${TVMAIL_SH}"
    TVMAIL_VERSION="${PROJECT_VERSION}")

if(WIN32)
    # self-contained .exe: no libstdc++-6/libgcc_s/libwinpthread next to it
    target_link_options(tvmail PRIVATE -static -static-libgcc -static-libstdc++)
endif()

# --- tests (ctest) -------------------------------------------------------
# backend_unit : stdlib unittest over tvmail-backend (mbox parsing, delete,
#                aliases, lock-sweep, serve framing) - no build needed
# chat_unit    : stdlib unittest over tvchat-backend (config, event queue,
#                wire escaping, selectors loop, serve framing) - no network
# backend_pipe : `tvmail --selftest` - the C++ side forks the serve
#                co-process, handshakes, round-trips a request, spawnLogged;
#                then the same for tvchat-backend (start, accounts, a poll)
# e2e_tui      : drives the built tvmail in a pty (launch, panes, Tab, body
#                scroll, background F3); exits 77 = skipped where no pty
# e2e_regress  : one pty case per squashed BUGS.md entry, read through a
#                pyte screen model (columns, colours); 77 = no pty or no pyte
# e2e_chat     : tvmail in a pty with a tvchat.conf aimed at a fake IRC server:
#                the chat co-process starts, is polled while idle, and QUITs
#                on Alt-X; 77 = no pty
find_program(TVMAIL_PYTHON
    NAMES python3 python3.13 python3.12 python3.11 python3.10 python3.9 python)

if(TVMAIL_PYTHON)
    add_test(NAME backend_unit
             COMMAND ${TVMAIL_PYTHON} -m unittest discover
                     -s "${CMAKE_CURRENT_SOURCE_DIR}/tests" -p "test_backend.py")
    add_test(NAME chat_unit
             COMMAND ${TVMAIL_PYTHON} -m unittest discover
                     -s "${CMAKE_CURRENT_SOURCE_DIR}/tests" -p "test_chat_backend.py")
endif()

if(NOT WIN32)
    # TVMAIL_MODE=local pins it to the on-disk mbox path regardless of any
    # ~/.config/tvmail/tvmail.conf on the host - so a client machine's
    # --selftest doesn't wander off to a live IMAP server.
    add_test(NAME backend_pipe COMMAND tvmail --selftest)
    set_tests_properties(backend_pipe PROPERTIES
        ENVIRONMENT "PATH=${CMAKE_CURRENT_SOURCE_DIR}/backend:$ENV{PATH};TVMAIL_MODE=local;TVCHAT_CONF=${CMAKE_CURRENT_SOURCE_DIR}/tests/tvchat-selftest.conf"
        PASS_REGULAR_EXPRESSION "selftest: served=1 status=0.*selftest: chat served=1 status=0 accounts=1")
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
        add_test(NAME e2e_chat
                 COMMAND ${TVMAIL_PYTHON}
                         "${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_chat.py"
                         --bin $<TARGET_FILE:tvmail>
                         --backend-dir "${CMAKE_CURRENT_SOURCE_DIR}/backend")
        set_tests_properties(e2e_chat PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
    endif()
    # install_roundtrip : cmake --install into a temp prefix, ./uninstall.sh it
    add_test(NAME install_roundtrip
             COMMAND bash "${CMAKE_CURRENT_SOURCE_DIR}/tests/install_roundtrip.sh"
                     "${CMAKE_CURRENT_BINARY_DIR}")
    # mail_setup : third_party/POSIX/mail-setup's own sandboxed tests (F3 runs
    #              its mail-pull); skipped if not fetched.  -LE mail_setup omits it
    add_test(NAME mail_setup
             COMMAND bash "${CMAKE_CURRENT_SOURCE_DIR}/tests/mail_setup.sh")
    set_tests_properties(mail_setup PROPERTIES
        SKIP_RETURN_CODE 77 TIMEOUT 300 LABELS mail_setup)
    # mingw_compile : every src/*.cpp syntax-checked by the mingw-w64 cross g++
    #                 (warnings as errors) - the native Windows build, chat off;
    #                 77 = no cross compiler, or tvision found as a package
    if(DEFINED TVISION_DIR AND EXISTS "${TVISION_DIR}/include/tvision/tv.h")
        add_test(NAME mingw_compile
                 COMMAND bash "${CMAKE_CURRENT_SOURCE_DIR}/tests/mingw_compile.sh"
                         "${TVISION_DIR}/include")
        set_tests_properties(mingw_compile PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 300)
    endif()
endif()

include(GNUInstallDirs)
install(TARGETS tvmail RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(PROGRAMS backend/tvmail-backend backend/tvchat-backend DESTINATION ${CMAKE_INSTALL_BINDIR})
install(FILES man/tvmail.1 man/tvmail-backend.1
        DESTINATION ${CMAKE_INSTALL_MANDIR}/man1)
install(FILES README.md INSTALL.md HISTORY.md LICENSE AUTHORS WARRANTY
              configure/tvmail.conf.example configure/tvchat.conf.example
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
