// Optional OpenVDB sidecar. This header and the Maya node link to no OpenVDB library.
#pragma once
#include <algorithm>
#include <array>
#include <cctype>
#include <cstdlib>
#include <memory>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
#ifdef _WIN32
#include <windows.h>
#else
#include <dlfcn.h>
#endif

namespace mvr_openvdb {
using Callback=int (*)(double,const char*);
struct Unavailable:std::runtime_error { using std::runtime_error::runtime_error; };
struct SolverError:std::runtime_error { using std::runtime_error::runtime_error; };
inline std::string libraryPath,reason;
inline bool failed=false;
struct Api {
    int (*version)()=nullptr;
    int (*remesh)(const double*,int,const int*,int,const int*,const int*,int,const double*,int,int,Callback,void**,char*,int)=nullptr;
    int (*polish)(void*,int,Callback,void**,char*,int)=nullptr;
    int (*info)(void*,double*,int*,int*)=nullptr;
    int (*count)(void*,int)=nullptr;
    int (*copy)(void*,double*,int,int*,int)=nullptr;
    void (*release)(void*)=nullptr;
};
inline bool enabled() {
    const char* setting=std::getenv("MAYA_VOLUME_REMESH_OPENVDB");
    std::string mode=setting ? setting : "auto";
    std::transform(mode.begin(),mode.end(),mode.begin(),[](unsigned char c) { return char(std::tolower(c)); });
    if (mode=="off" || mode=="0") return false;
    if (mode!="auto" && mode!="on" && mode!="1") throw SolverError("MAYA_VOLUME_REMESH_OPENVDB must be auto, on, or off");
    return true;
}
inline Api* api() {
    static Api functions;
    static bool attempted=false;
    if (!enabled()) { reason="OpenVDB disabled by MAYA_VOLUME_REMESH_OPENVDB"; return nullptr; }
    if (failed) return nullptr;
    if (attempted) return &functions;
    attempted=true;
    const char* supplied=std::getenv("MAYA_VOLUME_REMESH_OPENVDB_LIBRARY");
    std::string path=supplied && *supplied ? supplied : libraryPath;
    auto fail=[&](const std::string& error)->Api* { failed=true; reason=error; return nullptr; };
    if (path.empty()) {
        const char* details=std::getenv("MAYA_VOLUME_REMESH_OPENVDB_REASON");
        return fail(details ? details : "No installed OpenVDB sidecar");
    }
#ifdef _WIN32
    auto handle=LoadLibraryA(path.c_str());
    if (!handle) return fail("Could not load the optional OpenVDB sidecar");
    auto symbol=[&](const char* name) { return reinterpret_cast<void*>(GetProcAddress(handle,name)); };
#else
    auto handle=dlopen(path.c_str(),RTLD_NOW|RTLD_LOCAL);
    if (!handle) { const char* error=dlerror(); return fail(error ? error : "Could not load OpenVDB"); }
    auto symbol=[&](const char* name) { return dlsym(handle,name); };
#endif
    // Loaded sidecars stay resident while Maya nodes own volume handles.
    functions.version=reinterpret_cast<decltype(functions.version)>(symbol("mvr_vdb_version"));
    functions.remesh=reinterpret_cast<decltype(functions.remesh)>(symbol("mvr_vdb_remesh"));
    functions.polish=reinterpret_cast<decltype(functions.polish)>(symbol("mvr_vdb_polish"));
    functions.info=reinterpret_cast<decltype(functions.info)>(symbol("mvr_vdb_info"));
    functions.count=reinterpret_cast<decltype(functions.count)>(symbol("mvr_vdb_count"));
    functions.copy=reinterpret_cast<decltype(functions.copy)>(symbol("mvr_vdb_copy"));
    functions.release=reinterpret_cast<decltype(functions.release)>(symbol("mvr_vdb_free"));
    if (!functions.version || !functions.remesh || !functions.polish || !functions.info ||
        !functions.count || !functions.copy || !functions.release || functions.version()!=5)
        return fail("OpenVDB sidecar API/ABI mismatch; rebuild and restart Maya");
    reason.clear();
    return &functions;
}
inline void check(int code,const char* message) {
    if (code==3) throw Unavailable(message);
    if (code) throw SolverError(message && *message ? message : "OpenVDB solve failed");
}
struct Output {
    std::vector<std::array<double,3>> points;
    std::vector<std::array<int,4>> quads;
    double spacing=0;
    int closed=0;
};
inline std::shared_ptr<void> own(void* pointer,Api* functions) {
    return std::shared_ptr<void>(pointer,[functions](void* value) { if (value) functions->release(value); });
}
inline Output read(void* handle,Api* functions) {
    int nv=functions->count(handle,0),nq=functions->count(handle,1);
    if (!handle || nv<4 || nq<4) throw Unavailable("OpenVDB returned an invalid result handle");
    if (static_cast<unsigned>(nq)>std::numeric_limits<unsigned>::max()/4)
        throw SolverError("Quad indices exceed Maya's mesh array range");
    Output output;
    std::vector<double> points(size_t(nv)*3);
    std::vector<int> quads(size_t(nq)*4);
    int dims[3];
    if (functions->info(handle,&output.spacing,dims,&output.closed) ||
        functions->copy(handle,points.data(),nv,quads.data(),nq))
        throw Unavailable("OpenVDB output copy failed");
    output.points.reserve(size_t(nv)); output.quads.reserve(size_t(nq));
    for (int i=0;i<nv;++i) output.points.push_back({points[3LL*i],points[3LL*i+1],points[3LL*i+2]});
    for (int i=0;i<nq;++i) output.quads.push_back({quads[4LL*i],quads[4LL*i+1],quads[4LL*i+2],quads[4LL*i+3]});
    return output;
}
struct Cache {
    std::shared_ptr<void> base;
    Api* functions=nullptr;
    void build(const double* points,int nv,const int* triangles,int nt,const int* vertices,const int* faces,
               int meshes,const double* matrices,int resolution) {
        auto* nextApi=api();
        if (!nextApi) throw Unavailable(reason);
        void* pointer=nullptr; char error[4096]{};
        int code=nextApi->remesh(points,nv,triangles,nt,vertices,faces,meshes,matrices,resolution,0,nullptr,&pointer,error,sizeof(error));
        auto next=own(pointer,nextApi);
        check(code,error);
        read(pointer,nextApi);
        base=std::move(next); functions=nextApi;
    }
    Output polished(int passes) const {
        void* pointer=nullptr; char error[4096]{};
        int code=functions->polish(base.get(),passes,nullptr,&pointer,error,sizeof(error));
        auto output=own(pointer,functions);
        check(code,error);
        return read(pointer,functions);
    }
};
} // namespace mvr_openvdb
