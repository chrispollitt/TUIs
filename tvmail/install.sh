#!/usr/bin/env bash
# install.sh - deprecated alias for setup.sh.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$here/setup.sh" "$@"
