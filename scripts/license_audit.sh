#!/bin/sh
# License audit (docs/licenses.md): full table + hard fails.
# Fails on GPL (non-LGPL) licenses and on UNKNOWN entries beyond the
# two known-benign ones (basic-pitch = Apache-2.0 upstream,
# piano-transcription-inference = verify upstream).
set -e
cd "$(dirname "$0")/.."
PIP_LICENSES=".venv/bin/pip-licenses"
[ -x "$PIP_LICENSES" ] || .venv/bin/pip -q install pip-licenses
"$PIP_LICENSES" --format=plain --with-urls
echo
# Allowlist: pyinstaller(+hooks) is GPLv2 WITH the bootloader
# exception (build tool, bundles apps of any license); sphn and the
# two transcriber packages are known UNKNOWNs tracked in
# docs/licenses.md.
bad=$("$PIP_LICENSES" --format=csv 2>/dev/null | awk -F'","' '
  $1 ~ /pyinstaller/ {next}
  tolower($0) ~ /gpl/ && tolower($0) !~ /lgpl/ {print $1}
  /UNKNOWN/ && $1 !~ /basic-pitch|piano-transcription-inference|sphn|BeatNet/ {print $1}
' | tr -d '"')
if [ -n "$bad" ]; then
  echo "LICENSE AUDIT FAILED — review these packages:" >&2
  echo "$bad" >&2
  exit 1
fi
echo "license audit: OK (see docs/licenses.md for the yellow zone)"
