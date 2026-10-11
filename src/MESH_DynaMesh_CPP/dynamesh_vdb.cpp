// OpenVDB solid union, automatic border caps, and uniform quad extraction.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstddef>
#include <cstdint>
#include <exception>
#include <memory>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <unordered_map>
#include <vector>

#ifdef _WIN32
#define BT_EXPORT extern "C" __declspec(dllexport)
#else
#define BT_EXPORT extern "C" __attribute__((visibility("default")))
#endif

namespace {
using Point = std::array<double, 3>;
using TriangleIds = std::array<int, 3>;
using Quad = std::array<int, 4>;
using Callback = int (*)(double, const char*);
struct Cancelled {};
struct Workspace;
struct Mesh {
    std::shared_ptr<Workspace> workspace;
    Point origin{};
    std::vector<Point> points;
    std::vector<Quad> quads;
    double spacing=0;
    std::array<int,3> dimensions{};
    int closedBorders=0;
};
void progress(Callback callback, double value, const char* message) {
    if (callback && callback(value, message)) throw Cancelled{};
}
Point subtract(const Point& a, const Point& b) {
    return {a[0]-b[0],a[1]-b[1],a[2]-b[2]};
}
double dot(const Point& a, const Point& b) {
    return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
}
double triangleDistanceSquared(const Point& p, const Point& a, const Point& b, const Point& c) {
    Point ab=subtract(b,a), ac=subtract(c,a), ap=subtract(p,a);
    double d1=dot(ab,ap), d2=dot(ac,ap);
    if (d1<=0 && d2<=0) return dot(ap,ap);
    Point bp=subtract(p,b);
    double d3=dot(ab,bp), d4=dot(ac,bp);
    if (d3>=0 && d4<=d3) return dot(bp,bp);
    double vc=d1*d4-d3*d2;
    if (vc<=0 && d1>=0 && d3<=0) {
        double v=d1/(d1-d3); Point delta{};
        for (int k=0; k<3; ++k) delta[k]=ap[k]-v*ab[k];
        return dot(delta,delta);
    }
    Point cp=subtract(p,c);
    double d5=dot(ab,cp), d6=dot(ac,cp);
    if (d6>=0 && d5<=d6) return dot(cp,cp);
    double vb=d5*d2-d1*d6;
    if (vb<=0 && d2>=0 && d6<=0) {
        double w=d2/(d2-d6); Point delta{};
        for (int k=0; k<3; ++k) delta[k]=ap[k]-w*ac[k];
        return dot(delta,delta);
    }
    double va=d3*d6-d5*d4;
    if (va<=0 && d4-d3>=0 && d5-d6>=0) {
        double w=(d4-d3)/((d4-d3)+(d5-d6)); Point delta{};
        for (int k=0; k<3; ++k) delta[k]=bp[k]-w*(c[k]-b[k]);
        return dot(delta,delta);
    }
    double denominator=va+vb+vc, v=vb/denominator, w=vc/denominator; Point delta{};
    for (int k=0; k<3; ++k) delta[k]=ap[k]-v*ab[k]-w*ac[k];
    return dot(delta,delta);
}
struct Bounds {
    Point lower{INFINITY,INFINITY,INFINITY}, upper{-INFINITY,-INFINITY,-INFINITY};
    void include(const Point& p) {
        for (int k=0; k<3; ++k) { lower[k]=std::min(lower[k],p[k]); upper[k]=std::max(upper[k],p[k]); }
    }
    double distanceSquared(const Point& p) const {
        double sum=0;
        for (int k=0; k<3; ++k) { double d=std::max({lower[k]-p[k],0.0,p[k]-upper[k]}); sum+=d*d; }
        return sum;
    }
};

struct EdgeUse { int count=0, winding=0; };
std::uint64_t edgeKey(int a, int b) {
    return (std::uint64_t(std::min(a,b))<<32) | std::uint32_t(std::max(a,b));
}
using Point2 = std::array<double,2>;
double turn(const Point2& a,const Point2& b,const Point2& c) {
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
}
std::vector<TriangleIds> capLoop(std::vector<double>& vertices,std::vector<int> loop,Callback callback) {
    std::reverse(loop.begin()+1,loop.end());
    auto point=[&](int i) { return Point{vertices[3LL*i],vertices[3LL*i+1],vertices[3LL*i+2]}; };
    Bounds bounds;
    for (int i: loop) bounds.include(point(i));
    double span=0;
    for (int k=0; k<3; ++k) span=std::max(span,bounds.upper[k]-bounds.lower[k]);
    if (span<=0) throw std::runtime_error("An open border has no usable area.");
    Point origin=point(loop[0]);
    std::vector<Point> local;
    for (size_t i=0; i<loop.size(); ++i) {
        if (i%8192==0) progress(callback,0.012,"Closing open source borders");
        Point p=point(loop[i]);
        for (int k=0; k<3; ++k) p[k]=(p[k]-origin[k])/span;
        local.push_back(p);
    }
    Point normal{};
    for (size_t i=0; i<local.size(); ++i) {
        const auto& a=local[i]; const auto& b=local[(i+1)%local.size()];
        for (int k=0; k<3; ++k) normal[k]+=a[(k+1)%3]*b[(k+2)%3]-a[(k+2)%3]*b[(k+1)%3];
    }
    int axis=0;
    for (int k=1; k<3; ++k) if (std::abs(normal[k])>std::abs(normal[axis])) axis=k;
    if (std::abs(normal[axis])<=1e-14)
        throw std::runtime_error("An open border crosses itself or has no usable area.");
    std::vector<Point2> points;
    for (const auto& p: local) points.push_back({p[(axis+1)%3],p[(axis+2)%3]});
    int sign=normal[axis]>0 ? 1 : -1, count=int(loop.size());
    bool convex=true;
    double turning=0;
    for (int i=0; i<count; ++i) {
        const auto& a=points[(i+count-1)%count]; const auto& b=points[i]; const auto& c=points[(i+1)%count];
        double cross=turn(a,b,c);
        if (sign*cross < -1e-12) convex=false;
        turning+=std::atan2(cross,(b[0]-a[0])*(c[0]-b[0])+(b[1]-a[1])*(c[1]-b[1]));
    }
    convex=convex && std::abs(turning-sign*2*std::acos(-1.0))<1e-6;
    std::vector<TriangleIds> caps;
    if (convex) {
        Point center{};
        for (int i: loop) for (int k=0; k<3; ++k) center[k]+=vertices[3LL*i+k];
        for (int k=0; k<3; ++k) center[k]/=count;
        int index=int(vertices.size()/3);
        vertices.insert(vertices.end(),center.begin(),center.end());
        for (int i=0; i<count; ++i) caps.push_back({loop[i],loop[(i+1)%count],index});
        return caps;
    }
    long long work=0;
    auto check=[&]() {
        if (++work%8192==0) progress(callback,0.012,"Closing open source borders");
    };
    for (int i=0; i<count; ++i) {
        const auto& a=points[i]; const auto& b=points[(i+1)%count];
        for (int j=i+2; j<count; ++j) {
            if (i==0 && j==count-1) continue;
            check(); const auto& c=points[j]; const auto& d=points[(j+1)%count];
            if (turn(a,b,c)*turn(a,b,d)<-1e-24 && turn(c,d,a)*turn(c,d,b)<-1e-24)
                throw std::runtime_error("An open border crosses itself in its cap projection.");
        }
    }
    std::vector<int> pending(count);
    std::iota(pending.begin(),pending.end(),0);
    while (pending.size()>3) {
        progress(callback,0.012,"Closing open source borders");
        bool clipped=false;
        for (size_t i=0; i<pending.size(); ++i) {
            int a=pending[(i+pending.size()-1)%pending.size()], b=pending[i], c=pending[(i+1)%pending.size()];
            if (sign*turn(points[a],points[b],points[c])<=1e-14) continue;
            bool occupied=false;
            for (int p: pending) {
                if (p==a || p==b || p==c) continue;
                check();
                if (sign*turn(points[a],points[b],points[p])>=-1e-14 &&
                    sign*turn(points[b],points[c],points[p])>=-1e-14 &&
                    sign*turn(points[c],points[a],points[p])>=-1e-14) { occupied=true; break; }
            }
            if (!occupied) {
                caps.push_back({loop[a],loop[b],loop[c]}); pending.erase(pending.begin()+i); clipped=true; break;
            }
        }
        if (!clipped) throw std::runtime_error("Automatic closure could not triangulate this border.");
    }
    int a=pending[0],b=pending[1],c=pending[2];
    if (sign*turn(points[a],points[b],points[c])<=1e-14)
        throw std::runtime_error("Automatic closure produced a collapsed cap.");
    caps.push_back({loop[a],loop[b],loop[c]});
    return caps;
}
std::vector<TriangleIds> closeBorders(std::vector<double>& vertices,int first,int last,
    const std::unordered_map<std::uint64_t,EdgeUse>& uses,int& closed,Callback callback) {
    bool open=false;
    for (const auto& item: uses) {
        if (item.second.count==2 && item.second.winding==0) continue;
        if (item.second.count!=1)
            throw std::runtime_error("Source has nonmanifold edges or inconsistent face normals.");
        open=true;
    }
    if (!open) return {};
    std::vector<int> next(last-first,-1), incoming(last-first,-1);
    for (const auto& item: uses) if (item.second.count==1) {
        int a=int(item.first>>32),b=int(item.first&0xffffffff);
        if (item.second.winding<0) std::swap(a,b);
        if (next[a]>=0 || incoming[b]>=0) throw std::runtime_error("Source has a branching open border.");
        next[a]=b; incoming[b]=a;
    }
    for (int i=0; i<last-first; ++i) if ((next[i]<0)!=(incoming[i]<0))
        throw std::runtime_error("Source has an incomplete open border.");
    std::vector<bool> visited(last-first,false);
    std::vector<TriangleIds> caps;
    for (int start=0; start<last-first; ++start) {
        if (next[start]<0 || visited[start]) continue;
        progress(callback,0.012,"Closing open source borders");
        std::vector<int> loop;
        int vertex=start;
        while (!visited[vertex]) {
            if (loop.size()%8192==0) progress(callback,0.012,"Closing open source borders");
            loop.push_back(first+vertex); visited[vertex]=true; vertex=next[vertex];
        }
        if (vertex!=start || loop.size()<3) throw std::runtime_error("Source has an invalid open border.");
        auto patch=capLoop(vertices,std::move(loop),callback);
        caps.insert(caps.end(),patch.begin(),patch.end()); ++closed;
    }
    return caps;
}
struct Prepared {
    std::vector<double> points;
    std::vector<int> triangles, offsets{0};
    Point origin{};
    double spacing=0;
    std::array<int,3> dimensions{};
    int closedBorders=0;
};
Prepared prepare(const double* raw,int nv,const int* triangles,int nt,
                 const int* vertexOffsets,const int* triangleOffsets,int meshes,
                 const double* matrices,int resolution,Callback callback) {
    if (!raw || !triangles || !vertexOffsets || !triangleOffsets || !matrices ||
        meshes<1 || nv<1 || nt<1 || resolution<1)
        throw std::runtime_error("Invalid native input buffers.");
    if (vertexOffsets[0]!=0 || vertexOffsets[meshes]!=nv ||
        triangleOffsets[0]!=0 || triangleOffsets[meshes]!=nt)
        throw std::runtime_error("Invalid mesh offsets.");
    Prepared data;
    data.points.resize(3LL*nv); data.triangles.reserve(3LL*nt);
    Bounds bounds;
    for (int mesh=0; mesh<meshes; ++mesh) {
        int first=vertexOffsets[mesh], last=vertexOffsets[mesh+1];
        int begin=triangleOffsets[mesh], end=triangleOffsets[mesh+1];
        if (first<0 || last<=first || last>nv || begin<0 || end<=begin || end>nt)
            throw std::runtime_error("Source mesh has empty points or triangles.");
        const double* matrix=matrices+16LL*mesh;
        for (int k=0; k<16; ++k) if (!std::isfinite(matrix[k]))
            throw std::runtime_error("Source mesh has an invalid world matrix.");
        if (matrix[3]!=0 || matrix[7]!=0 || matrix[11]!=0 || matrix[15]!=1)
            throw std::runtime_error("Source world matrices must be affine.");
        for (int i=first; i<last; ++i) {
            if ((i-first)%8192==0) progress(callback,0.002,"Transforming source points");
            for (int k=0; k<3; ++k) if (!std::isfinite(raw[3LL*i+k]))
                throw std::runtime_error("Source mesh has empty or invalid points.");
            for (int k=0; k<3; ++k) {
                double value=raw[3LL*i]*matrix[k]+raw[3LL*i+1]*matrix[4+k]+
                             raw[3LL*i+2]*matrix[8+k]+matrix[12+k];
                if (!std::isfinite(value)) throw std::runtime_error("Source mesh has invalid world points.");
                data.points[3LL*i+k]=value;
            }
        }
        std::vector<int> parents(last-first), roots(end-begin);
        std::iota(parents.begin(),parents.end(),0);
        auto root=[&](int i) {
            while (parents[i]!=i) { parents[i]=parents[parents[i]]; i=parents[i]; }
            return i;
        };
        std::unordered_map<std::uint64_t,EdgeUse> edgeUses;
        edgeUses.reserve(3LL*(end-begin)/2);
        for (int t=begin; t<end; ++t) {
            if ((t-begin)%8192==0) progress(callback,0.008,"Checking source connectivity");
            std::array<int,3> face{triangles[3LL*t],triangles[3LL*t+1],triangles[3LL*t+2]};
            for (int id: face) if (id<0 || id>=last-first)
                throw std::runtime_error("Source mesh has invalid triangle indices.");
            if (face[0]==face[1] || face[1]==face[2] || face[0]==face[2])
                throw std::runtime_error("Source mesh has invalid triangle indices.");
            Point a{},b{},c{};
            for (int k=0; k<3; ++k) {
                a[k]=data.points[3LL*(first+face[0])+k];
                b[k]=data.points[3LL*(first+face[1])+k];
                c[k]=data.points[3LL*(first+face[2])+k];
            }
            Point ab=subtract(b,a), ac=subtract(c,a);
            Point cross{ab[1]*ac[2]-ab[2]*ac[1],ab[2]*ac[0]-ab[0]*ac[2],ab[0]*ac[1]-ab[1]*ac[0]};
            if (cross[0]==0 && cross[1]==0 && cross[2]==0)
                throw std::runtime_error("Source mesh has a zero-area triangle.");
            bounds.include(a); bounds.include(b); bounds.include(c);
            for (int k=0; k<3; ++k) {
                int u=face[k],v=face[(k+1)%3];
                auto& use=edgeUses[edgeKey(u,v)]; ++use.count; use.winding+=u<v ? 1 : -1;
                int ru=root(u),rv=root(v); if (ru!=rv) parents[rv]=ru;
            }
        }
        auto caps=closeBorders(data.points,first,last,edgeUses,data.closedBorders,callback);
        for (const auto& face: caps) for (int k=0; k<3; ++k) {
            int a=face[k]-first,b=face[(k+1)%3]-first;
            auto& use=edgeUses[edgeKey(a,b)]; ++use.count; use.winding+=a<b ? 1 : -1;
        }
        if (!caps.empty()) for (const auto& item: edgeUses) if (item.second.count!=2 || item.second.winding!=0)
            throw std::runtime_error("Automatic closure did not produce a manifold source.");
        // Preserve first-face shell order, independent of hash iteration order.
        std::unordered_map<int,int> shellIds;
        std::vector<int> counts;
        for (int t=begin; t<end; ++t) {
            if ((t-begin)%8192==0) progress(callback,0.012,"Separating source shells");
            int id=root(triangles[3LL*t]);
            auto found=shellIds.emplace(id,static_cast<int>(counts.size()));
            if (found.second) counts.push_back(0);
            int shell=found.first->second; roots[t-begin]=shell; ++counts[shell];
        }
        std::vector<int> capShells;
        for (const auto& face: caps) {
            int shell=shellIds.at(root(face[0]-first));
            capShells.push_back(shell); ++counts[shell];
        }
        int base=static_cast<int>(data.triangles.size()/3);
        std::vector<int> positions;
        for (int count: counts) {
            positions.push_back(base); base+=count; data.offsets.push_back(base);
        }
        data.triangles.resize(3LL*base);
        for (int t=begin; t<end; ++t) {
            int output=positions[roots[t-begin]]++;
            for (int k=0; k<3; ++k) data.triangles[3LL*output+k]=first+triangles[3LL*t+k];
        }
        for (size_t t=0; t<caps.size(); ++t) {
            int output=positions[capShells[t]]++;
            for (int k=0; k<3; ++k) data.triangles[3LL*output+k]=caps[t][k];
        }
    }
    double span=0;
    for (int k=0; k<3; ++k) span=std::max(span,bounds.upper[k]-bounds.lower[k]);
    data.spacing=span/resolution;
    if (!std::isfinite(data.spacing) || data.spacing<=0)
        throw std::runtime_error("Source bounds have no usable volume.");
    for (int k=0; k<3; ++k) {
        double dimension=std::ceil((bounds.upper[k]-bounds.lower[k])/data.spacing)+5;
        if (!std::isfinite(dimension) || dimension>std::numeric_limits<int>::max())
            throw std::runtime_error("Grid dimensions exceed the supported integer range.");
        data.dimensions[k]=int(dimension); data.origin[k]=bounds.lower[k]-2.5*data.spacing;
    }
    for (size_t i=0; i<data.points.size()/3; ++i) {
        if (i%8192==0) progress(callback,0.016,"Normalizing source points");
        for (int k=0; k<3; ++k) data.points[3LL*i+k]=(data.points[3LL*i+k]-data.origin[k])/data.spacing;
    }
    progress(callback,0.02,"Source shells checked");
    return data;
}

// Validate arbitrary-valence quad fans produced by OpenVDB.
struct FanEntry { int vertex=-1, links[2]{-1,-1}, count=0; };
struct VertexFan {
    std::vector<FanEntry> entries;
    int count=0;
    int index(int vertex) const {
        for (int i=0; i<count; ++i) if (entries[i].vertex==vertex) return i;
        return -1;
    }
    void add(int a,int b) {
        int i=index(a);
        if (i<0) {
            i=count++; entries.push_back(FanEntry{}); entries[i].vertex=a;
        }
        auto& row=entries[i];
        for (int k=0; k<row.count; ++k) if (row.links[k]==b) return;
        if (row.count==2) throw std::runtime_error("Remesh has invalid vertex flow. Increase Resolution.");
        row.links[row.count++]=b;
    }
};
void validate(const Mesh& mesh,Callback callback) {
    if (mesh.points.empty() || mesh.quads.empty()) throw std::runtime_error("Remesh is empty.");
    std::vector<VertexFan> fans(mesh.points.size());
    std::unordered_map<std::uint64_t,EdgeUse> edgeUses;
    edgeUses.reserve(mesh.quads.size()*2);
    for (size_t i=0; i<mesh.quads.size(); ++i) {
        if (i%8192==0) progress(callback,0.92,"Checking quad edges");
        const auto& q=mesh.quads[i];
        for (int k=0; k<4; ++k) {
            if (q[k]<0 || q[k]>=int(mesh.points.size())) throw std::runtime_error("Remesh has invalid quad indices.");
            for (int j=0; j<k; ++j) if (q[k]==q[j]) throw std::runtime_error("Remesh has invalid quad indices.");
        }
        for (int k=0; k<4; ++k) {
            int a=q[k],b=q[(k+1)%4];
            auto& use=edgeUses[edgeKey(a,b)]; ++use.count; use.winding+=a<b ? 1 : -1;
            fans[a].add(q[(k+3)%4],b); fans[a].add(b,q[(k+3)%4]);
        }
    }
    for (const auto& item: edgeUses) if (item.second.count!=2 || item.second.winding!=0)
        throw std::runtime_error("Remesh is not a closed manifold. Increase Resolution.");
    for (size_t i=0; i<fans.size(); ++i) {
        if (i%8192==0) progress(callback,0.94,"Checking vertex flow");
        const auto& fan=fans[i];
        if (fan.count<3) throw std::runtime_error("Remesh has invalid vertex flow. Increase Resolution.");
        for (int j=0; j<fan.count; ++j) if (fan.entries[j].count!=2)
            throw std::runtime_error("Remesh has invalid vertex flow. Increase Resolution.");
        std::vector<bool> visited(fan.count,false);
        std::vector<int> pending{0};
        int reached=0;
        while (!pending.empty()) {
            int j=pending.back(); pending.pop_back();
            if (visited[j]) continue;
            visited[j]=true; ++reached;
            for (int id: fan.entries[j].links) {
                int neighbor=fan.index(id);
                if (neighbor<0) throw std::runtime_error("Remesh has invalid vertex flow. Increase Resolution.");
                if (!visited[neighbor]) pending.push_back(neighbor);
            }
        }
        if (reached!=fan.count)
            throw std::runtime_error("Remesh has a disconnected vertex fan. Increase Resolution.");
        for (double value: mesh.points[i]) if (!std::isfinite(value))
            throw std::runtime_error("Remesh contains invalid points.");
    }
}
} // namespace
#include "vdb_solver.h"
namespace {
void polishMesh(Mesh& mesh,const Workspace& workspace,int polish,Callback callback) {
    auto* result=&mesh;
    const auto& cells=workspace.cells;
    auto& field=*workspace.field;
    if (polish<0 || polish>10) throw std::runtime_error("Polish must be from 0 to 10.");
    if (polish) {
        std::vector<std::vector<int>> neighbors(result->points.size());
        for (auto& q: result->quads) for (int k=0; k<4; ++k) {
            int a=q[k], b=q[(k+1)%4];
            neighbors[a].push_back(b); neighbors[b].push_back(a);
        }
        for (auto& row: neighbors) {
            std::sort(row.begin(),row.end()); row.erase(std::unique(row.begin(),row.end()),row.end());
            if (row.empty()) throw std::runtime_error("Unused surface vertex.");
        }
        std::vector<Point> output(result->points.size());
        for (int pass=0; pass<polish; ++pass) for (double weight: {0.45,-0.47}) {
            progress(callback,0.8+0.1*pass/polish,"Polishing quad surface");
            for (size_t i=0; i<result->points.size(); ++i) {
                if (i%8192==0) progress(callback,0.8+0.1*pass/polish,"Polishing quad surface");
                for (int k=0; k<3; ++k) {
                    double sum=0;
                    for (int j: neighbors[i]) sum+=result->points[j][k];
                    double p=result->points[i][k];
                    output[i][k]=std::clamp(p+weight*(sum/neighbors[i].size()-p),cells[i][k]+1e-4,cells[i][k]+1-1e-4);
                }
            }
            result->points.swap(output);
            if (weight<0) for (size_t i=0; i<result->points.size(); ++i) {
                if (i%8192==0) progress(callback,0.8+0.1*pass/polish,"Fitting smooth surface");
                result->points[i]=field.project(result->points[i],cells[i]);
            }
        }
    }
}
} // namespace

BT_EXPORT int mvr_vdb_version() { return 5; }
BT_EXPORT int mvr_vdb_prepared(const double* points,int nv,const int* triangles,int nt,
                                 const int* offsets,int solids,const int* dimensions,
                                 const double* origin,double spacing,int closed,int polish,
                                 Callback callback,void** output,char* error,int errorSize) {
    if (!output || !error || errorSize<1 || !origin || !dimensions) return 1;
    *output=nullptr; error[0]=0;
    try {
        if (!std::isfinite(spacing) || spacing<=0) throw std::runtime_error("Invalid voxel width.");
        auto mesh=build(points,nv,triangles,nt,offsets,solids,dimensions,polish,callback);
        validate(*mesh,callback);
        for (int k=0;k<3;++k) { mesh->origin[k]=origin[k]; mesh->dimensions[k]=dimensions[k]; }
        for (auto& p:mesh->points) for (int k=0;k<3;++k) p[k]=origin[k]+p[k]*spacing;
        mesh->spacing=spacing; mesh->closedBorders=closed;
        *output=mesh.release(); return 0;
    } catch (const Cancelled&) { return 2;
    } catch (const std::bad_alloc&) {
        std::snprintf(error,errorSize,"Not enough memory for this resolution. Lower Resolution or close other heavy scenes."); return 4;
    } catch (const std::exception& e) { std::snprintf(error,errorSize,"%s",e.what()); return 1;
    } catch (...) { std::snprintf(error,errorSize,"Unknown OpenVDB failure."); return 3; }
}
BT_EXPORT int mvr_vdb_polish(void* pointer,int polish,Callback callback,
                               void** output,char* error,int errorSize) {
    if (!pointer || !output || !error || errorSize<1) return 1;
    *output=nullptr; error[0]=0;
    try {
        auto& source=*static_cast<Mesh*>(pointer);
        if (!source.workspace) throw std::runtime_error("Missing cached OpenVDB volume.");
        auto mesh=std::make_unique<Mesh>(source.workspace->base);
        polishMesh(*mesh,*source.workspace,polish,callback);
        validate(*mesh,callback);
        for (auto& p:mesh->points) for (int k=0;k<3;++k) p[k]=source.origin[k]+p[k]*source.spacing;
        mesh->origin=source.origin; mesh->spacing=source.spacing; mesh->dimensions=source.dimensions;
        mesh->closedBorders=source.closedBorders; mesh->workspace=source.workspace;
        *output=mesh.release(); return 0;
    } catch (const Cancelled&) { return 2;
    } catch (const std::bad_alloc&) {
        std::snprintf(error,errorSize,"Not enough memory for this resolution. Lower Resolution or close other heavy scenes."); return 4;
    } catch (const std::exception& e) { std::snprintf(error,errorSize,"%s",e.what()); return 1;
    } catch (...) { std::snprintf(error,errorSize,"Unknown OpenVDB polish failure."); return 3; }
}
BT_EXPORT int mvr_vdb_stats(void* pointer,std::uint64_t* active,std::uint64_t* bytes) {
    if (!pointer || !active || !bytes) return 1;
    const auto& mesh=*static_cast<Mesh*>(pointer);
    if (!mesh.workspace) return 1;
    const auto& grid=*mesh.workspace->field->grid;
    *active=grid.activeVoxelCount(); *bytes=grid.memUsage(); return 0;
}
BT_EXPORT int mvr_vdb_remesh(const double* points, int nv, const int* triangles, int nt,
                               const int* vertexOffsets, const int* triangleOffsets, int meshes,
                               const double* matrices, int resolution, int polish,
                               Callback callback, void** output, char* error, int errorSize) {
    if (!output || !error || errorSize<1) return 1;
    *output=nullptr; error[0]=0;
    try {
        auto data=prepare(points,nv,triangles,nt,vertexOffsets,triangleOffsets,meshes,matrices,resolution,callback);
        auto mesh=build(data.points.data(),int(data.points.size()/3),data.triangles.data(),int(data.triangles.size()/3),data.offsets.data(),
                        int(data.offsets.size())-1,data.dimensions.data(),polish,callback);
        validate(*mesh,callback);
        for (auto& p: mesh->points) for (int k=0; k<3; ++k) p[k]=data.origin[k]+p[k]*data.spacing;
        mesh->origin=data.origin; mesh->spacing=data.spacing; mesh->dimensions=data.dimensions; mesh->closedBorders=data.closedBorders;
        *output=mesh.release();
        return 0;
    } catch (const Cancelled&) { return 2;
    } catch (const std::bad_alloc&) {
        std::snprintf(error,errorSize,"Not enough memory for this resolution. Lower Resolution or close other heavy scenes."); return 4;
    } catch (const std::exception& e) { std::snprintf(error,errorSize,"%s",e.what()); return 1;
    } catch (...) { std::snprintf(error,errorSize,"Unknown OpenVDB remesh failure."); return 3; }
}
BT_EXPORT int mvr_vdb_info(void* pointer,double* spacing,int* dimensions,int* closedBorders) {
    if (!pointer || !spacing || !dimensions || !closedBorders) return 1;
    const auto& mesh=*static_cast<Mesh*>(pointer);
    *spacing=mesh.spacing;
    *closedBorders=mesh.closedBorders;
    for (int k=0; k<3; ++k) dimensions[k]=mesh.dimensions[k];
    return 0;
}
BT_EXPORT int mvr_vdb_copy(void* pointer, double* points, int nv, int* quads, int nq) {
    if (!pointer) return 1;
    auto& mesh=*static_cast<Mesh*>(pointer);
    if (nv!=static_cast<int>(mesh.points.size()) || nq!=static_cast<int>(mesh.quads.size()) || !points || !quads) return 1;
    for (int i=0; i<nv; ++i) for (int k=0; k<3; ++k) points[3LL*i+k]=mesh.points[i][k];
    for (int i=0; i<nq; ++i) for (int k=0; k<4; ++k) quads[4LL*i+k]=mesh.quads[i][k];
    return 0;
}
BT_EXPORT int mvr_vdb_count(void* pointer, int faces) {
    if (!pointer) return 0;
    auto& mesh=*static_cast<Mesh*>(pointer);
    return static_cast<int>(faces ? mesh.quads.size() : mesh.points.size());
}
BT_EXPORT void mvr_vdb_free(void* pointer) { delete static_cast<Mesh*>(pointer); }
