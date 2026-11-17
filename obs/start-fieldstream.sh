#!/usr/bin/env bash
# Run from Terminal in the extracted kit. Supports the system Bash on macOS.
set -eu
kit_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
if [ -n "${FIELD_PYTHON:-}" ]; then
    python_path=$FIELD_PYTHON
elif [ -x "$kit_dir/.venv/bin/python" ]; then
    python_path="$kit_dir/.venv/bin/python"
else
    python_path=python3
fi
exec "$python_path" "$kit_dir/launch_posix.py" "$@"
