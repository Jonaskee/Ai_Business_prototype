#!/usr/bin/env sh
# GramIQ starten op macOS of Linux:  ./start.sh   (of: sh start.sh)
cd "$(dirname "$0")" || exit 1
for py in python3 python; do
  if command -v "$py" >/dev/null 2>&1; then
    exec "$py" start.py "$@"
  fi
done
echo "Python 3.10 of nieuwer is niet gevonden: https://www.python.org/downloads/"
exit 1
