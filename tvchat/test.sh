#!/usr/bin/env sh
# Build (if needed) and run every tvchat test layer through ctest.
#
#   ./test.sh                    # all layers
#   ./test.sh -R chat_unit       # just one
#   ./test.sh -V                 # verbose
#   ./test.sh --output-on-failure   (the default)
set -e
# Cygwin: cmake finds its modules from the path it was run as; a PATH with /bin but
# not /usr/bin (a bare ssh login) makes it look in /share and fail with "Could not
# find CMAKE_ROOT"
case "$(uname -s 2>/dev/null)" in CYGWIN*) PATH=/usr/bin:$PATH; export PATH ;; esac
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$here"

cmake -S . -B build >/dev/null
cmake --build build

cd build
set -- --output-on-failure "$@"
exec ctest "$@"
