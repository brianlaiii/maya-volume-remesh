// Real optional sidecar loading and cached polish, without the Maya SDK.
#include "../src/MESH_DynaMesh_CPP/openvdb_loader.h"
#include <cassert>
#include <filesystem>
int main(int argc,char** argv) {
    assert(argc==2);
    mvr_openvdb::Api tooLarge;
    tooLarge.count=[](void*,int faces) { return faces ? std::numeric_limits<int>::max() : 8; };
    bool arrayLimit=false;
    try { mvr_openvdb::read(reinterpret_cast<void*>(1),&tooLarge); }
    catch (const mvr_openvdb::SolverError&) { arrayLimit=true; }
    assert(arrayLimit); // Reject before allocation or an overflowing Maya index loop.
    mvr_openvdb::libraryPath=argv[1];
    auto* api=mvr_openvdb::api();
    if (!std::filesystem::exists(argv[1])) {
        assert(!api); assert(!mvr_openvdb::reason.empty()); return 0;
    }
    assert(api);
    double points[]={-1,-1,-1,1,-1,-1,-1,1,-1,1,1,-1,-1,-1,1,1,-1,1,-1,1,1,1,1,1};
    int triangles[]={0,2,3,0,3,1,4,5,7,4,7,6,0,1,5,0,5,4,2,6,7,2,7,3,0,4,6,0,6,2,1,3,7,1,7,5};
    int vertices[]={0,8},faces[]={0,12};
    double matrix[]={1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1};
    mvr_openvdb::Cache cache;
    cache.build(points,8,triangles,12,vertices,faces,1,matrix,24);
    auto base=cache.base.get();
    auto raw=cache.polished(0),smooth=cache.polished(2);
    assert(cache.base.get()==base);
    assert(raw.quads==smooth.quads && !raw.quads.empty());
    assert(raw.spacing==2.0/24 && raw.closed==0);
    bool stopped=false;
    try { cache.build(points,8,triangles,12,vertices,faces,1,matrix,0); }
    catch (const mvr_openvdb::SolverError&) { stopped=true; }
    assert(stopped && cache.base.get()==base);
    return 0;
}
