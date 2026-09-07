cmake_minimum_required(VERSION 3.13)
project(tvmail CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# Cygwin's newlib hides XSI symbols (wcwidth, SA_NOCLDWAIT, ...) that tvision's
# Unix backend expects; _GNU_SOURCE exposes them. Must precede add_subdirectory.
if(CYGWIN)
    add_compile_definitions(_GNU_SOURCE)
    # Cygwin has no SA_NOCLDWAIT (no auto child-reaping); make it a no-op flag.
    add_compile_definitions(SA_NOCLDWAIT=0)
endif()

# Path to a Cygwin `bash` the built .exe should call for all mail operations.
# build.sh fills this in; harmless/ignored on a native Linux build.
set(TVMAIL_BASH "bash" CACHE STRING "bash used to run tvmail-backend")

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
target_compile_definitions(tvmail PRIVATE TVMAIL_BASH="${TVMAIL_BASH}")

if(WIN32)
    # self-contained .exe: no libstdc++-6/libgcc_s/libwinpthread next to it
    target_link_options(tvmail PRIVATE -static -static-libgcc -static-libstdc++)
endif()

include(GNUInstallDirs)
install(TARGETS tvmail RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(PROGRAMS backend/tvmail-backend DESTINATION ${CMAKE_INSTALL_BINDIR})
install(PROGRAMS backend/pop-pull DESTINATION ${CMAKE_INSTALL_BINDIR})
install(FILES man/tvmail.1 man/tvmail-backend.1
        DESTINATION ${CMAKE_INSTALL_MANDIR}/man1)
