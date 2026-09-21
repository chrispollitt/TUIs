#!/usr/bin/env bash
# install.sh - deprecated alias for setup.sh.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sudo "cmake" --install "$here/build" --prefix "/usr/local/"
