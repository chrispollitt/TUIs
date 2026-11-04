# tvmail is a CMake project driven by the shell scripts next to this file.
# This Makefile only forwards, so a habitual `make` / `make install` does the
# right thing instead of GNU make's built-in "foo.sh -> foo" rule quietly
# copying install.sh to a file called `install`.
#
#   make            ./build.sh      fetch ../tvcommon/third_party/, build
#   make test       ./test.sh       build + all test layers
#   make install    ./install.sh    build, then cmake --install into /usr/local
#   make uninstall  ./uninstall.sh
#   make clean      ./clean.sh      build/, dist/, logs
#   make distclean  ./clean.sh --all   ...and ../tvcommon/third_party/
#
# For the first-time wizard and the per-user config use ./configure.sh and
# ./setup.sh directly (see INSTALL.md).

.PHONY: all build test install uninstall clean distclean

all build:
	./build.sh

test:
	./test.sh

install: all
	./install.sh

uninstall:
	./uninstall.sh

clean:
	./clean.sh

distclean:
	./clean.sh --all
