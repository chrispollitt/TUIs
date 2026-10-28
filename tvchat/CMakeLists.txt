cmake_minimum_required(VERSION 3.13)
project(tvchat VERSION 1.3.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

# tvchat-backend is a co-process: tvchat needs fork() and pipes.  There is no
# native Windows build - use Cygwin (or WSL).
if(WIN32)
    message(FATAL_ERROR "tvchat needs a POSIX system (fork()): build it on "
                        "Linux, macOS, the BSDs, Cygwin or WSL")
endif()

# Before add_subdirectory(tvision), so the bundled build also gets a
# CTestTestfile.cmake and `ctest` doesn't warn about a missing include.
enable_testing()

# The C++ tvchat shares with tvmail (../tvcommon; a source tarball carries
# it as ./tvcommon).  It also finds Turbo Vision and sets TV_SH.
if(NOT DEFINED TVCOMMON_DIR)
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tvcommon/tvcommon.cmake")
        set(TVCOMMON_DIR "${CMAKE_CURRENT_SOURCE_DIR}/tvcommon")
    else()
        set(TVCOMMON_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../tvcommon")
    endif()
endif()
include("${TVCOMMON_DIR}/tvcommon.cmake")

add_executable(tvchat
    src/main.cpp src/app.cpp src/chat.cpp src/chatwin.cpp src/help.cpp
    ${TVCOMMON_SOURCES})
tvcommon_setup(tvchat)
target_compile_definitions(tvchat PRIVATE TVCHAT_VERSION="${PROJECT_VERSION}")

# --- tests (ctest) -------------------------------------------------------
# chat_unit    : stdlib unittest over tvchat-backend (config, event queue,
#                wire escaping, selectors loop, serve framing, a fake IRC
#                server) - no network, no build needed
# backend_pipe : `tvchat --selftest` - the C++ side forks the serve
#                co-process, reads the accounts, polls, pings
# e2e_chat     : tvchat in a pty with a tvchat.conf aimed at a fake IRC
#                server: polled while idle, a line sent and answered, a
#                killed backend restarted, QUIT on Alt-X; 77 = no pty
# e2e_regress  : the chat window read through a pyte screen model (panes,
#                unread, Tab, drafts, colours, menus while polling);
#                77 = no pty or no pyte
# install_roundtrip : cmake --install into a temp prefix, ./uninstall.sh it
find_program(TVCHAT_PYTHON
    NAMES python3 python3.13 python3.12 python3.11 python3.10 python3.9 python)

if(TVCHAT_PYTHON)
    add_test(NAME chat_unit
             COMMAND ${TVCHAT_PYTHON} -m unittest discover
                     -s "${CMAKE_CURRENT_SOURCE_DIR}/tests" -p "test_chat_backend.py")
endif()

add_test(NAME backend_pipe COMMAND tvchat --selftest)
set_tests_properties(backend_pipe PROPERTIES
    ENVIRONMENT "PATH=${CMAKE_CURRENT_SOURCE_DIR}/backend:$ENV{PATH};TVCHAT_CONF=${CMAKE_CURRENT_SOURCE_DIR}/tests/tvchat-selftest.conf"
    PASS_REGULAR_EXPRESSION "selftest: chat served=1 status=0 accounts=1")
if(TVCHAT_PYTHON)
    add_test(NAME e2e_chat
             COMMAND ${TVCHAT_PYTHON}
                     "${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_chat.py"
                     --bin $<TARGET_FILE:tvchat>
                     --backend-dir "${CMAKE_CURRENT_SOURCE_DIR}/backend")
    set_tests_properties(e2e_chat PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 60)
    add_test(NAME e2e_regress
             COMMAND ${TVCHAT_PYTHON}
                     "${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_regress.py"
                     --bin $<TARGET_FILE:tvchat>
                     --backend-dir "${CMAKE_CURRENT_SOURCE_DIR}/backend")
    set_tests_properties(e2e_regress PROPERTIES SKIP_RETURN_CODE 77 TIMEOUT 300)
endif()
add_test(NAME install_roundtrip
         COMMAND bash "${CMAKE_CURRENT_SOURCE_DIR}/tests/install_roundtrip.sh"
                 "${CMAKE_CURRENT_BINARY_DIR}")

include(GNUInstallDirs)
install(TARGETS tvchat RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(PROGRAMS backend/tvchat-backend DESTINATION ${CMAKE_INSTALL_BINDIR})
# Floyd's levels and tools, shared with tvmail (the backend looks in ../share/tvchat)
install(FILES ${TVCOMMON_DIR}/py/tvfloyd.py DESTINATION ${CMAKE_INSTALL_DATADIR}/tvchat)
install(FILES man/tvchat.1 man/tvchat-backend.1
        DESTINATION ${CMAKE_INSTALL_MANDIR}/man1)
install(FILES README.md INSTALL.md HISTORY.md LICENSE AUTHORS WARRANTY
              configure/tvchat.conf.example configure/floyd.conf.example
              configure/tvchat-floyd.service
        DESTINATION ${CMAKE_INSTALL_DOCDIR})

# --- tarballs (CPack) -------------------------------------------------------
# Binary tarball:  cd build && cpack           -> tvchat-0.1.0-<system>.tar.gz
set(CPACK_GENERATOR "TGZ")
set(CPACK_PACKAGE_NAME "tvchat")
set(CPACK_PACKAGE_VENDOR "Chris Pollitt")
set(CPACK_PACKAGE_CONTACT "Chris Pollitt <chris.pollitt@gmail.com>")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Turbo Vision chat client (IRC, talk, Delta Chat)")
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_RESOURCE_FILE_LICENSE "${CMAKE_CURRENT_SOURCE_DIR}/LICENSE")
set(CPACK_PACKAGE_FILE_NAME
    "tvchat-${PROJECT_VERSION}-${CMAKE_SYSTEM_NAME}-${CMAKE_SYSTEM_PROCESSOR}")
set(CPACK_STRIP_FILES ON)
set(CPACK_SOURCE_GENERATOR "TGZ")
set(CPACK_SOURCE_PACKAGE_FILE_NAME "tvchat-${PROJECT_VERSION}")
set(CPACK_SOURCE_IGNORE_FILES
    "/build/" "/_inst/" "/third_party/" "/\\\\.git/"
    "\\\\.o$" "\\\\.bak$" "/__pycache__/" "\\\\.pyc$")
include(CPack)
