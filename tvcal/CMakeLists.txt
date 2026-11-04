cmake_minimum_required(VERSION 3.13)
project(tvcal VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# tvcal-backend is a co-process: tvcal needs fork() and pipes.  There is no native
# Windows build - use Cygwin (or WSL).
if(WIN32)
    message(FATAL_ERROR "tvcal needs a POSIX system (fork()): build it on "
                        "Linux, macOS, the BSDs, Cygwin or WSL")
endif()

enable_testing()

# The C++ tvcal shares with tvmail and tvchat (../tvcommon; a source tarball carries it
# as ./tvcommon).  It also finds Turbo Vision and sets TV_SH.
if(NOT DEFINED TVCOMMON_DIR)
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tvcommon/tvcommon.cmake")
        set(TVCOMMON_DIR "${CMAKE_CURRENT_SOURCE_DIR}/tvcommon")
    else()
        set(TVCOMMON_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../tvcommon")
    endif()
endif()
include("${TVCOMMON_DIR}/tvcommon.cmake")

add_executable(tvcal
    src/main.cpp src/app.cpp src/cal.cpp src/calwin.cpp src/floydwin.cpp src/help.cpp
    ${TVCOMMON_SOURCES})
tvcommon_setup(tvcal)
target_compile_definitions(tvcal PRIVATE TVCAL_VERSION="${PROJECT_VERSION}")

# --- tests (ctest) -------------------------------------------------------
# cal_unit     : stdlib unittest over tvcal-backend (iCalendar, repeats, to-dos, reminders,
#                requests, serve framing, a fake CalDAV server) - no network, no build needed
# backend_pipe : `tvcal --selftest` - the C++ side forks the serve co-process
find_program(TVCAL_PYTHON
    NAMES python3 python3.13 python3.12 python3.11 python3.10 python3.9 python)

if(TVCAL_PYTHON)
    add_test(NAME cal_unit
             COMMAND ${TVCAL_PYTHON} -W ignore -m unittest discover
                     -s "${CMAKE_CURRENT_SOURCE_DIR}/tests" -p "test_cal_backend.py")
    set_tests_properties(cal_unit PROPERTIES TIMEOUT 600)
endif()

add_test(NAME backend_pipe COMMAND tvcal --selftest)
set_tests_properties(backend_pipe PROPERTIES
    ENVIRONMENT "PATH=${CMAKE_CURRENT_SOURCE_DIR}/backend:$ENV{PATH};TVCAL_DATA=${CMAKE_CURRENT_BINARY_DIR}/selftest.ics;TVCAL_STATE=${CMAKE_CURRENT_BINARY_DIR}/selftest-state"
    PASS_REGULAR_EXPRESSION "selftest: cal served=1")

include(GNUInstallDirs)
install(TARGETS tvcal RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(PROGRAMS backend/tvcal-backend DESTINATION ${CMAKE_INSTALL_BINDIR})
# Floyd's words, shared with tvmail and tvchat (the backend looks in ../share/tvcal)
install(FILES ${TVCOMMON_DIR}/py/tvfloyd.py DESTINATION ${CMAKE_INSTALL_DATADIR}/tvcal)
install(FILES README.md INSTALL.md HISTORY.md LICENSE AUTHORS WARRANTY
              configure/tvcal.conf.example configure/configure-radicale.sh
        DESTINATION ${CMAKE_INSTALL_DOCDIR})

set(CPACK_GENERATOR "TGZ")
set(CPACK_PACKAGE_NAME "tvcal")
set(CPACK_PACKAGE_VENDOR "Chris Pollitt")
set(CPACK_PACKAGE_CONTACT "Chris Pollitt <chris.pollitt@gmail.com>")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Turbo Vision calendar and to-do manager")
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_RESOURCE_FILE_LICENSE "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE")
set(CPACK_PACKAGE_FILE_NAME
    "tvcal-${PROJECT_VERSION}-${CMAKE_SYSTEM_NAME}-${CMAKE_SYSTEM_PROCESSOR}")
set(CPACK_STRIP_FILES ON)
set(CPACK_SOURCE_GENERATOR "TGZ")
set(CPACK_SOURCE_PACKAGE_FILE_NAME "tvcal-${PROJECT_VERSION}")
set(CPACK_SOURCE_IGNORE_FILES
    "/build/" "/_inst/" "/third_party/" "/\\.git/"
    "\\.o$" "\\.bak$" "/__pycache__/" "\\.pyc$")
include(CPack)
