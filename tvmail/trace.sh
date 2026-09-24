#!/usr/bin/env sh
# Profile a --trace run: run tvmail (or tvmail-backend) with --trace [FILE]
# first, quit, then run this against the files it left behind.
#
#   ./trace.sh                     trace.log + trace.backend.log (the defaults)
#   ./trace.sh FILE                FILE + FILE's backend sibling (same rule
#                                   tvmail uses: name.ext -> name.backend.ext)
#   ./trace.sh FRONTEND BACKEND    an explicit pair
#   ./trace.sh -n N [FILE...]      top N entries instead of the default 20
#
# The frontend trace is Chrome Trace Event JSON, the backend trace is a
# cProfile dump_stats() file - different formats need different profilers, so
# this runs both non-interactively and just prints the hot spots.  Either file
# may be missing (e.g. a tvmail-backend-only trace); whichever exists gets
# profiled.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$here"

need() { command -v "$1" >/dev/null 2>&1 || { echo "trace.sh: '$1' not found" >&2; exit 1; }; }
need python3

top=20
if [ "${1:-}" = "-n" ]; then
  top=$2
  shift 2
fi

front="${1:-trace.log}"
if [ -n "${2:-}" ]; then
  back="$2"
else
  # same rule as tvmail.cpp's backendPath(): insert .backend before the
  # extension (name.ext -> name.backend.ext), or append it if there's none.
  dir="${front%/*}"
  base="$front"
  case "$front" in
    */*) base="${front##*/}" ;;
    *)   dir="" ;;
  esac
  case "$base" in
    *.*) backbase="${base%.*}.backend.${base##*.}" ;;
    *)   backbase="$base.backend" ;;
  esac
  if [ -n "$dir" ]; then back="$dir/$backbase"; else back="$backbase"; fi
fi

found=0

if [ -e "$front" ]; then
  found=1
  echo ">> frontend trace: $front (Chrome Trace Event JSON)"
  python3 - "$front" "$top" <<'PY'
import json, sys

path, top = sys.argv[1], int(sys.argv[2])
with open(path) as f:
    data = json.load(f)
events = [e for e in data.get("traceEvents", []) if e.get("ph") == "X"]
events.sort(key=lambda e: e.get("dur", 0), reverse=True)
total = sum(e.get("dur", 0) for e in events)
print(f"   {len(events)} span(s), {total / 1000:.3f} ms total")
print(f"   {'dur (ms)':>10}  {'start (ms)':>10}  name")
for e in events[:top]:
    print(f"   {e.get('dur', 0) / 1000:>10.3f}  {e.get('ts', 0) / 1000:>10.3f}  {e.get('name', '')}")
PY
  echo "   view interactively: chrome://tracing (Load) or https://ui.perfetto.dev (drag & drop)"
  echo
fi

if [ -e "$back" ]; then
  found=1
  echo ">> backend trace: $back (cProfile)"
  python3 - "$back" "$top" <<'PY'
import pstats, sys

path, top = sys.argv[1], int(sys.argv[2])
print("   -- by cumulative time --")
pstats.Stats(path).strip_dirs().sort_stats("cumulative").print_stats(top)
print("   -- by internal (tottime) time --")
pstats.Stats(path).strip_dirs().sort_stats("tottime").print_stats(top)
PY
  echo "   explore interactively: python3 -m pstats $back   (or: pip install snakeviz && snakeviz $back)"
  echo
fi

if [ "$found" = 0 ]; then
  echo "trace.sh: neither $front nor $back exists - run tvmail --trace [FILE] (or tvmail-backend --trace) first" >&2
  exit 1
fi
