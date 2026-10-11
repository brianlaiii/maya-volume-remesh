// Sparse narrow-band solid union and uniform OpenVDB quad extraction.
// Included after DynaMesh's Maya-independent mesh/preparation definitions.
#include <openvdb/openvdb.h>
#include <openvdb/tools/MeshToVolume.h>
#include <openvdb/tools/VolumeToMesh.h>
#include <openvdb/tools/Composite.h>
#include <openvdb/tools/Interpolation.h>
#include <atomic>
#include <chrono>
#include <mutex>
#include <thread>

namespace {
struct TriangleAdapter {
    const double* points;
    const int* triangles;
    size_t vertices, polygons;
    size_t pointCount() const { return vertices; }
    size_t polygonCount() const { return polygons; }
    size_t vertexCount(size_t) const { return 3; }
    void getIndexSpacePoint(size_t face,size_t corner,openvdb::Vec3d& point) const {
        size_t i=size_t(triangles[3*face+corner])*3;
        point=openvdb::Vec3d(points[i],points[i+1],points[i+2]);
    }
};
// OpenVDB invokes its interrupter from worker threads. Python/Maya callbacks
// stay on the caller thread; worker threads only read an atomic cancel flag.
struct VdbInterrupter {
    std::atomic<bool> stopped{false};
    void start(const char* = nullptr) {}
    void end() {}
    bool wasInterrupted(int = -1) const { return stopped.load(); }
};
template<class Function>
auto vdbOperation(Function function,Callback callback,double fraction,const char* message) {
    progress(callback,fraction,message);
    using Return=decltype(function(std::declval<VdbInterrupter&>()));
    VdbInterrupter interrupter;
    if (!callback) return function(interrupter);
    Return result;
    std::exception_ptr error;
    std::atomic<bool> finished{false};
    std::thread worker([&] {
        try { result=function(interrupter); } catch (...) { error=std::current_exception(); }
        finished=true;
    });
    std::exception_ptr cancelled;
    while (!finished) {
        try { progress(callback,fraction,message); }
        catch (...) { cancelled=std::current_exception(); interrupter.stopped=true; break; }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    worker.join();
    if (cancelled) std::rethrow_exception(cancelled);
    if (error) std::rethrow_exception(error);
    return result;
}
class SurfaceField {
public:
    openvdb::FloatGrid::Ptr grid;
    std::string backend="OpenVDB", reason="Sparse narrow-band level set";
    explicit SurfaceField(openvdb::FloatGrid::Ptr value):grid(std::move(value)) {}
    double value(const Point& p) const {
        return openvdb::tools::BoxSampler::sample(grid->tree(),openvdb::Vec3d(p[0],p[1],p[2]));
    }
    Point project(Point p,const std::array<int,3>& cell) const {
        for (int pass=0;pass<4;++pass) {
            double v=value(p);
            Point gradient{};
            for (int k=0;k<3;++k) {
                Point a=p,b=p; a[k]+=0.01; b[k]-=0.01;
                gradient[k]=(value(a)-value(b))/0.02;
            }
            double norm=dot(gradient,gradient);
            if (norm<1e-16 || std::abs(v)<1e-7) break;
            double weight=v/norm,step=std::abs(weight)*std::sqrt(norm);
            if (step>0.5) weight*=0.5/step;
            for (int k=0;k<3;++k)
                p[k]=std::clamp(p[k]-weight*gradient[k],cell[k]+1e-4,cell[k]+1-1e-4);
        }
        return p;
    }
};
struct Workspace {
    std::unique_ptr<SurfaceField> field;
    std::vector<std::array<int,3>> cells;
    Mesh base;
};
void polishMesh(Mesh&,const Workspace&,int,Callback);
std::unique_ptr<Mesh> build(const double* raw,int nv,const int* triangles,int nt,
                           const int* offsets,int solids,const int* dims,int polish,
                           Callback callback,std::shared_ptr<Workspace>* savedWorkspace=nullptr) {
    if (!raw || !triangles || !offsets || !dims || nv<4 || nt<4 || solids<1 ||
        offsets[0]!=0 || offsets[solids]!=nt)
        throw std::runtime_error("Invalid OpenVDB input buffers.");
    static std::once_flag initialized;
    std::call_once(initialized,[] { openvdb::initialize(); });
    auto transform=openvdb::math::Transform::createLinearTransform(1.0);
    openvdb::FloatGrid::Ptr grid;
    for (int shell=0;shell<solids;++shell) {
        int begin=offsets[shell],end=offsets[shell+1];
        if (begin<0 || end<=begin || end>nt) throw std::runtime_error("Invalid solid offsets.");
        TriangleAdapter adapter{raw,triangles+3LL*begin,size_t(nv),size_t(end-begin)};
        auto next=vdbOperation([&](VdbInterrupter& stop) {
            return openvdb::tools::meshToVolume<openvdb::FloatGrid>(stop,adapter,*transform,3.0f,3.0f,
                openvdb::tools::DISABLE_RENORMALIZATION);
        },callback,0.02+0.38*shell/solids,"OpenVDB: voxelizing solid union");
        if (!grid) grid=next;
        else openvdb::tools::csgUnion(*grid,*next);
    }
    progress(callback,0.4,"OpenVDB: sparse union complete");
    grid->setGridClass(openvdb::GRID_LEVEL_SET);
    auto workspace=std::make_shared<Workspace>();
    workspace->field=std::make_unique<SurfaceField>(grid);
    std::vector<openvdb::Vec3s> points;
    std::vector<openvdb::Vec3I> trianglesOut;
    std::vector<openvdb::Vec4I> quads;
    vdbOperation([&](VdbInterrupter&) {
        openvdb::tools::volumeToMesh(*grid,points,trianglesOut,quads,0.0,0.0);
        return true;
    },callback,0.55,"OpenVDB: extracting quad surface");
    if (points.empty() || quads.empty()) throw std::runtime_error("No enclosed volume survived OpenVDB sampling.");
    if (!trianglesOut.empty()) throw std::runtime_error("OpenVDB uniform extraction returned triangles.");
    if (points.size()>size_t(std::numeric_limits<int>::max()) || quads.size()>size_t(std::numeric_limits<int>::max()))
        throw std::runtime_error("Output exceeds Maya's integer mesh index range.");
    auto result=std::make_unique<Mesh>();
    for (const auto& p:points) {
        result->points.push_back({p[0],p[1],p[2]});
        workspace->cells.push_back({int(std::floor(p[0])),int(std::floor(p[1])),int(std::floor(p[2]))});
    }
    // OpenVDB's winding is inward for negative-inside level sets. Maya's
    // outward normals and positive Offset require the reverse winding.
    for (const auto& q:quads) result->quads.push_back({int(q[0]),int(q[3]),int(q[2]),int(q[1])});
    progress(callback,0.77,"OpenVDB: quad surface extracted");
    workspace->base=*result;
    polishMesh(*result,*workspace,polish,callback);
    if (savedWorkspace) *savedWorkspace=workspace;
    result->workspace=workspace;
    return result;
}
} // namespace
