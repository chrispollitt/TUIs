# tvcommon bugs

Defects in the shared code. Bugs found here are numbered in the app whose
test found them - [tvmail](../tvmail/BUGS.md) first, then
[tvchat](../tvchat/BUGS.md) - and listed there; the fix and its regression
case go in that app's tree. A bug that only shows in one app is that app's.

Things that were found here: tvmail #43 (a signal during a `CoProcess` round
trip was taken for a dead helper), #44 (idle ticks closed menus), #45 (the
mingw build did not compile: `setenv()`).

## To be squashed

| # | Bug | Notes |
|---|-----|-------|

## Squashed

See the apps' lists.
