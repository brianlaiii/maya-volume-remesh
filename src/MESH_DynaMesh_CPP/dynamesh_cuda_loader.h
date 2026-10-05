#pragma once
#include "dynamesh_cuda_api.h"
#include <cmath>
#include <cstdlib>
#include <string>
#include <vector>
#ifndef _WIN32
#include <dlfcn.h>
#endif

namespace bt_dynamesh {
// Set from the plugin's adjacent deployment manifest, or an explicit environment
// path. Libraries remain pinned while Maya nodes can use them.
inline std::string cudaLibrary;
inline BtDmCudaDistance cudaFunction = nullptr;
inline std::string cudaError;
inline bool cudaAttempted = false;

inline BtDmCudaDistance cudaDistance() {
    if (cudaAttempted) return cudaFunction;
    cudaAttempted = true;
    const char* mode = std::getenv("MAYA_VOLUME_REMESH_CUDA");
    if (mode && (std::string(mode)=="off" || std::string(mode)=="0")) {
        cudaError="CUDA disabled"; return nullptr;
    }
    const char* overridePath = std::getenv("MAYA_VOLUME_REMESH_CUDA_LIBRARY");
    const std::string path = overridePath && *overridePath ? overridePath : cudaLibrary;
    if (path.empty()) { cudaError="CUDA runtime unavailable"; return nullptr; }
#if defined(__linux__)
    void* library=dlopen(path.c_str(),RTLD_NOW|RTLD_LOCAL);
    if (!library) { const char* error=dlerror(); cudaError=error ? error : "CUDA load failed"; return nullptr; }
    auto version=reinterpret_cast<int(*)()>(dlsym(library,"bt_dynamesh_cuda_version"));
    auto function=reinterpret_cast<BtDmCudaDistance>(dlsym(library,"bt_dynamesh_cuda_distances_v1"));
    if (!version || version()!=1 || !function) { cudaError="CUDA ABI mismatch"; return nullptr; }
    cudaFunction=function;
#else
    cudaError="CUDA requires Linux x86_64";
#endif
    return cudaFunction;
}
} // namespace bt_dynamesh
