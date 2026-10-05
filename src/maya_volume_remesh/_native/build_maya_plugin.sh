#!/usr/bin/env bash
set -euo pipefail

fail() {
  echo "[native-build] $*" >&2
  exit 1
}

TOOL_ROOT=""
OUTPUT_NAME=""
SOURCE_ARGS=()
MAYA_LIBRARIES=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tool-root)
      [[ $# -ge 2 ]] || fail "Missing value for --tool-root."
      TOOL_ROOT="$2"
      shift 2
      ;;
    --source)
      [[ $# -ge 2 ]] || fail "Missing value for --source."
      SOURCE_ARGS+=("$2")
      shift 2
      ;;
    --output-name)
      [[ $# -ge 2 ]] || fail "Missing value for --output-name."
      OUTPUT_NAME="$2"
      shift 2
      ;;
    --library)
      [[ $# -ge 2 ]] || fail "Missing value for --library."
      MAYA_LIBRARIES+=("$2")
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      fail "Unknown build option: $1"
      ;;
  esac
done

[[ -n "${TOOL_ROOT}" ]] || fail "Missing --tool-root."
[[ -n "${OUTPUT_NAME}" ]] || fail "Missing --output-name."
[[ ${#SOURCE_ARGS[@]} -gt 0 ]] || fail "At least one --source is required."
[[ ${#MAYA_LIBRARIES[@]} -gt 0 ]] || fail "At least one --library is required."

MAYA_ROOT_VALUE="${1:-${MAYA_LOCATION:-}}"
MAYA_VERSION_VALUE="${2:-${MAYA_VERSION:-}}"
SYSTEM_NAME="$(uname -s)"

if [[ -n "${MAYA_ROOT_VALUE}" ]]; then
  case "${MAYA_ROOT_VALUE}" in
    */Maya.app/Contents/MacOS)
      MAYA_ROOT_VALUE="$(cd "${MAYA_ROOT_VALUE}/../../.." && pwd)"
      ;;
    */Maya.app/Contents)
      MAYA_ROOT_VALUE="$(cd "${MAYA_ROOT_VALUE}/../.." && pwd)"
      ;;
    */Maya.app)
      MAYA_ROOT_VALUE="$(cd "${MAYA_ROOT_VALUE}/.." && pwd)"
      ;;
  esac
fi

if [[ -z "${MAYA_ROOT_VALUE}" || ! -d "${MAYA_ROOT_VALUE}/include/maya" ]]; then
  for candidate in \
    /Applications/Autodesk/maya2027 \
    /Applications/Autodesk/maya2026 \
    /Applications/Autodesk/maya2025 \
    /Applications/Autodesk/maya2024 \
    /usr/autodesk/maya2027 \
    /usr/autodesk/maya2026 \
    /usr/autodesk/maya2025 \
    /usr/autodesk/maya2024 \
    /opt/Autodesk/maya2027 \
    /opt/Autodesk/maya2026 \
    /opt/Autodesk/maya2025 \
    /opt/Autodesk/maya2024 \
    /opt/maya; do
    if [[ -d "${candidate}/include/maya" ]]; then
      MAYA_ROOT_VALUE="${candidate}"
      break
    fi
  done
fi

if [[ -z "${MAYA_ROOT_VALUE}" || ! -d "${MAYA_ROOT_VALUE}/include/maya" ]]; then
  fail "Could not find Maya include folder."
fi

if [[ -z "${MAYA_VERSION_VALUE}" ]]; then
  maya_root_base="$(basename "${MAYA_ROOT_VALUE}")"
  MAYA_VERSION_VALUE="${maya_root_base#maya}"
fi
if [[ -z "${MAYA_VERSION_VALUE}" || "${MAYA_VERSION_VALUE}" == "$(basename "${MAYA_ROOT_VALUE}")" ]]; then
  fail "Could not infer Maya version. Pass it as the second argument."
fi

SOURCES=()
for source_arg in "${SOURCE_ARGS[@]}"; do
  if [[ "${source_arg}" == /* ]]; then
    source_path="${source_arg}"
  else
    source_path="${TOOL_ROOT}/${source_arg}"
  fi
  [[ -f "${source_path}" ]] || fail "Could not find source file: ${source_path}"
  SOURCES+=("${source_path}")
done

MAYA_LIBRARY_FLAGS=()
for library in "${MAYA_LIBRARIES[@]}"; do
  if [[ "${library}" == -l* ]]; then
    MAYA_LIBRARY_FLAGS+=("${library}")
  else
    MAYA_LIBRARY_FLAGS+=("-l${library}")
  fi
done

OUT_DIR="${TOOL_ROOT}/plug-ins/maya${MAYA_VERSION_VALUE}"
mkdir -p "${OUT_DIR}"

echo "[native-build] Starting ${OUTPUT_NAME} for Maya ${MAYA_VERSION_VALUE} on ${SYSTEM_NAME}."

if [[ "${SYSTEM_NAME}" == "Darwin" ]]; then
  MAYA_LIB_DIR="${MAYA_ROOT_VALUE}/Maya.app/Contents/MacOS"
  OUT="${OUT_DIR}/${OUTPUT_NAME}.bundle"
  [[ -d "${MAYA_LIB_DIR}" ]] || fail "Could not find Maya app library folder: ${MAYA_LIB_DIR}"
  CXX_BIN="${CXX:-/usr/bin/clang++}"
  "${CXX_BIN}" \
    -std=c++17 \
    -O3 \
    -DNDEBUG \
    -fvisibility=hidden \
    -DREQUIRE_IOSTREAM \
    -arch arm64 \
    -arch x86_64 \
    -dynamiclib \
    -I"${MAYA_ROOT_VALUE}/include" \
    -L"${MAYA_LIB_DIR}" \
    -Wl,-rpath,"${MAYA_LIB_DIR}" \
    -lFoundation \
    "${MAYA_LIBRARY_FLAGS[@]}" \
    "${SOURCES[@]}" \
    -o "${OUT}"
elif [[ "${SYSTEM_NAME}" == "Linux" ]]; then
  MAYA_LIB_DIR="${MAYA_ROOT_VALUE}/lib"
  OUT="${OUT_DIR}/${OUTPUT_NAME}.so"
  [[ -d "${MAYA_LIB_DIR}" ]] || fail "Could not find Maya library folder: ${MAYA_LIB_DIR}"
  CXX_BIN="${CXX:-}"
  if [[ -z "${CXX_BIN}" ]]; then
    CXX_BIN="$(command -v g++ || command -v clang++ || command -v c++ || true)"
  fi
  [[ -n "${CXX_BIN}" ]] || fail "Could not find a C++ compiler."
  "${CXX_BIN}" \
    -std=c++17 \
    -O3 \
    -DNDEBUG \
    -fPIC \
    -fvisibility=hidden \
    -D_BOOL \
    -DLINUX \
    -DREQUIRE_IOSTREAM \
    -shared \
    -I"${MAYA_ROOT_VALUE}/include" \
    "${SOURCES[@]}" \
    -L"${MAYA_LIB_DIR}" \
    -Wl,-rpath,"${MAYA_LIB_DIR}" \
    "${MAYA_LIBRARY_FLAGS[@]}" \
    -o "${OUT}"
else
  fail "Unsupported host OS: ${SYSTEM_NAME}"
fi

[[ -f "${OUT}" ]] || fail "Build did not create output: ${OUT}"

echo "[native-build] Completed ${OUTPUT_NAME}."
echo "${OUT}"
