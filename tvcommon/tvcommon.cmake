# tvcommon.cmake - the C++ tvmail and tvchat share, for their CMakeLists.txt:
#
#     include(${TVCOMMON_DIR}/tvcommon.cmake)   # after project() and enable_testing()
#     add_executable(myapp src/main.cpp ... ${TVCOMMON_SOURCES})
#     tvcommon_setup(myapp)
#
# It locates Turbo Vision (a tvision package, else ${TVISION_DIR}, by default
# tvcommon/third_party/tvision - third_party.sh fetches and patches it there, one
# checkout for both apps), and sets the Cygwin defines and the shell the
# helper scripts run through (TV_SH).

get_filename_component(TVCOMMON_DIR "${CMAKE_CURRENT_LIST_DIR}" ABSOLUTE)

# Cygwin's newlib hides XSI symbols (wcwidth, SA_NOCLDWAIT, ...) that tvision's
# Unix backend expects; _GNU_SOURCE exposes them. Must precede add_subdirectory.
if(CYGWIN)
    add_compile_definitions(_GNU_SOURCE)
    # Cygwin has no SA_NOCLDWAIT (no auto child-reaping); make it a no-op flag.
    add_compile_definitions(SA_NOCLDWAIT=0)
endif()

# POSIX shell the app runs its little helper scripts through.  /bin/sh is right
# everywhere native; tvmail's mingw cross-build (build.sh --mingw) overrides it
# with a Cygwin shell, since a native Windows .exe cannot exec a Unix /bin/sh.
set(TV_SH "/bin/sh" CACHE STRING "shell used to run the helper scripts")

# --- locate Turbo Vision (magiblot/tvision) -----------------------------
find_package(tvision QUIET)
if(NOT TARGET tvision)
    if(NOT DEFINED TVISION_DIR)
        set(TVISION_DIR "${TVCOMMON_DIR}/third_party/tvision")
    endif()
    if(EXISTS "${TVISION_DIR}/CMakeLists.txt")
        message(STATUS "${PROJECT_NAME}: building bundled Turbo Vision from ${TVISION_DIR}")
        set(TV_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
        add_subdirectory("${TVISION_DIR}" tvision-build EXCLUDE_FROM_ALL)
    else()
        message(FATAL_ERROR
            "Turbo Vision not found.\n"
            "  ${TVCOMMON_DIR}/third_party.sh tvision\n"
            "or pass -DTVISION_DIR=/path/to/tvision , or install the tvision package.")
    endif()
endif()

set(TVCOMMON_SOURCES
    ${TVCOMMON_DIR}/src/util.cpp
    ${TVCOMMON_DIR}/src/shell.cpp
    ${TVCOMMON_DIR}/src/coprocess.cpp
    ${TVCOMMON_DIR}/src/dialogs.cpp
    ${TVCOMMON_DIR}/src/cli.cpp
    ${TVCOMMON_DIR}/src/tvapp.cpp
    ${TVCOMMON_DIR}/src/tty.cpp
    ${TVCOMMON_DIR}/src/textwin.cpp)

function(tvcommon_setup target)
    target_link_libraries(${target} PRIVATE tvision)
    # Treat tvision's own headers as system headers so ITS warnings (e.g. the
    # __cdecl/__stdcall redefinition tv.h does on non-_WIN32 builds - Cygwin's
    # GCC already has built-in definitions for both) don't show up in the
    # app's build output. Warnings from our own sources are unaffected.
    get_target_property(TVISION_INCLUDE_DIRS tvision INTERFACE_INCLUDE_DIRECTORIES)
    if(TVISION_INCLUDE_DIRS)
        target_include_directories(${target} SYSTEM PRIVATE ${TVISION_INCLUDE_DIRS})
    endif()
    target_include_directories(${target} PRIVATE src "${TVCOMMON_DIR}/src")
    target_compile_definitions(${target} PRIVATE TV_SH="${TV_SH}")
    if(WIN32)
        # self-contained .exe: no libstdc++-6/libgcc_s/libwinpthread next to it
        target_link_options(${target} PRIVATE -static -static-libgcc -static-libstdc++)
    endif()
endfunction()
