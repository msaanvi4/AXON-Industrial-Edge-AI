#!/usr/bin/env sh
# Requires Python with Axon's edge requirements installed.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python "$ROOT/scripts/edge_runner.py" "$@"
