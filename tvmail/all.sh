#!/bin/bash
rm -f *.log
set -eu
./build.sh      > build.log
./test.sh       > test.log
./install.sh    > install.log
tvmail --trace  > run.log
./trace.sh      > trace.result.log
cat trace.result.log
