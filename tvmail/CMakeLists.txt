cmake_minimum_required(VERSION 3.13)
project(tvmail VERSION 1.0.0 LANGUAGES CXX)

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

add_executable(tvmail src/tvmail.cpp)
target_link_libraries(tvmail PRIVATE tvision)
target_compile_definitions(tvmail PRIVATE TVMAIL_SH="${TVMAIL_SH}")

if(WIN32)
    # self-contained .exe: no libstdc++-6/libgcc_s/libwinpthread next to it
    target_link_options(tvmail PRIVATE -static -static-libgcc -static-libstdc++)
endif()

# --- tests (ctest) -------------------------------------------------------
# backend_unit : stdlib unittest over tvmail-backend (mbox parsing, delete,
#                aliases, lock-sweep, serve framing) - no build needed
# backend_pipe : `tvmail --selftest` - the C++ side forks the serve
#                co-process, handshakes, round-trips a request, spawnLogged
# e2e_tui      : drives the built tvmail in a pty (launch, panes, Tab, body
#                scroll, background F3); exits 77 = skipped where no pty
find_program(TVMAIL_PYTHON
    NAMES python3 python3.13 python3.12 python3.11 python3.10 python3.9 python)

if(TVMAIL_PYTHON)
    add_test(NAME backend_unit
             COMMAND ${TVMAIL_PYTHON} -m unittest discover
                     -s "${CMAKE_CURRENT_SOURCE_DIR}/tests" -p "test_*.py")
endif()

if(NOT WIN32)
    add_test(NAME backend_pipe COMMAND tvmail --selftest)
    set_tests_properties(backend_pipe PROPERTIES
        ENVIRONMENT "PATH=${CMAKE_CURRENT_SOURCE_DIR}/backend:$ENV{PATH}"
        PASS_REGULAR_EXPRESSION "served=1 status=0")
    if(TVMAIL_PYTHON)
        add_test(NAME e2e_tui
                 COMMAND ${TVMAIL_PYTHON}
                         "${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_tui.py"
                         --bin $<TARGET_FILE:tvmail>
                         --backend-dir "${CMAKE_CURRENT_SOURCE_DIR}/backend")
        set_tests_properties(e2e_tui PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 120)
    endif()
endif()

include(GNUInstallDirs)
install(TARGETS tvmail RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(PROGRAMS backend/tvmail-backend DESTINATION ${CMAKE_INSTALL_BINDIR})
install(PROGRAMS backend/pop-pull DESTINATION ${CMAKE_INSTALL_BINDIR})
install(FILES man/tvmail.1 man/tvmail-backend.1
        DESTINATION ${CMAKE_INSTALL_MANDIR}/man1)
install(FILES README.md HISTORY.md LICENSE AUTHORS WARRANTY
        DESTINATION ${CMAKE_INSTALL_DOCDIR})

# --- tarballs (CPack) -------------------------------------------------------
# Binary tarball:  cd build && cpack           -> tvmail-1.0.0-<system>.tar.gz
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
