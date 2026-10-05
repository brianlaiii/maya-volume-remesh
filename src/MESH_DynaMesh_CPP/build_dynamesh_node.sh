#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/../maya_volume_remesh/_native/build_maya_plugin.sh" \
  --tool-root "${SCRIPT_DIR}" --source "dynamesh_node.cpp" \
  --output-name "btDynaMesh_0012E55A" --library "OpenMaya" --library "dl" -- "$@"
