#!/usr/bin/env sh
# Build (if needed) and run every tvmail test layer through ctest.
#
#   ./test.sh                    # all layers
#   ./test.sh -R backend_unit    # just one
#   ./test.sh -V                 # verbose
#   ./test.sh --output-on-failure   (the default)
set -e
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$here"

cmake -S . -B build >/dev/null
cmake --build build

cd build
set -- --output-on-failure "$@"
exec ctest "$@"
