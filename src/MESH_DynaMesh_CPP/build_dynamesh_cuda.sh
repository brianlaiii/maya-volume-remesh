#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo "DynaMesh CUDA requires Linux x86_64." >&2; exit 1; }
[[ $# == 1 ]] || { echo "Usage: build_dynamesh_cuda.sh OUTPUT.so" >&2; exit 1; }
NVCC_BIN="${CUDACXX:-}"
if [[ -z "${NVCC_BIN}" ]]; then NVCC_BIN="$(command -v nvcc || true)"; fi
if [[ -z "${NVCC_BIN}" ]]; then
  for root in "${CUDA_ROOT:-}" "${CUDA_HOME:-}" "${CUDA_PATH:-}" /opt/cuda /usr/local/cuda; do
    if [[ -n "${root}" && -x "${root}/bin/nvcc" ]]; then NVCC_BIN="${root}/bin/nvcc"; break; fi
  done
fi
[[ -n "${NVCC_BIN}" ]] || { echo "Could not find nvcc." >&2; exit 1; }
CXX_BIN="${CUDAHOSTCXX:-${CXX:-}}"
if [[ -z "${CXX_BIN}" ]]; then CXX_BIN="$(command -v g++ || true)"; fi
[[ -n "${CXX_BIN}" ]] || { echo "Could not find the CUDA host compiler." >&2; exit 1; }
OUT="$1"
mkdir -p "$(dirname "${OUT}")"
TEMP_OUT="$(mktemp "${OUT}.tmp.XXXXXX")"
trap 'rm -f "${TEMP_OUT}"' EXIT
echo "[dynamesh-cuda-build] Compiling optional sm_86 + PTX distance batches."
"${NVCC_BIN}" -std=c++17 -O3 --fmad=false --prec-div=true --prec-sqrt=true \
  -ccbin "${CXX_BIN}" -Xcompiler=-fPIC,-fvisibility=hidden,-ffp-contract=off \
  -gencode=arch=compute_86,code=sm_86 -gencode=arch=compute_86,code=compute_86 \
  --shared --cudart=static "${SCRIPT_DIR}/dynamesh_cuda.cu" -o "${TEMP_OUT}"
mv "${TEMP_OUT}" "${OUT}"
echo "[dynamesh-cuda-build] Completed."
echo "${OUT}"
