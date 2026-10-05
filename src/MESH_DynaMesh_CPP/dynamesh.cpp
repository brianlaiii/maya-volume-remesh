// SDK-free solid union, automatic border caps, and continuous quad surface nets.
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdint>
#include <exception>
#include <memory>
#include <limits>
#include <numeric>
#include <stdexcept>
#include <unordered_map>
#include <vector>
#ifdef BT_DYNAMESH_NODE
#include "dynamesh_cuda_loader.h"
#include <atomic>
#include <thread>
#endif

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
constexpr int edges[12][2] = {{0,1},{2,3},{4,5},{6,7},{0,2},{1,3},
                              {4,6},{5,7},{0,4},{1,5},{2,6},{3,7}};
constexpr int faces[6][4] = {{0,1,3,2},{4,5,7,6},{0,1,5,4},
                             {2,3,7,6},{0,2,6,4},{1,3,7,5}};
struct Cancelled {};
struct Mesh {
    std::vector<Point> points;
    std::vector<Quad> quads;
    double spacing=0;
    std::array<int,3> dimensions{};
    int closedBorders=0;
};
void progress(Callback callback, double value, const char* message) {
    if (callback && callback(value, message)) throw Cancelled{};
}
double edge(const Point& a, const Point& b, double x, double y) {
    return (b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0]);
}
bool owned(const Point& a, const Point& b, double value) {
    if (std::abs(value) > 1e-10) return value > 0;
    return b[1] > a[1] || (b[1] == a[1] && b[0] < a[0]);
}
bool badMask(int mask) {
    if (mask == 0 || mask == 255) return false;
    int crossings = 0;
    for (auto& e: edges) crossings += ((mask >> e[0]) ^ (mask >> e[1])) & 1;
    if (crossings > 6) return true;
    for (auto& f: faces) {
        int a=(mask>>f[0])&1, b=(mask>>f[1])&1;
        int c=(mask>>f[2])&1, d=(mask>>f[3])&1;
        if (a == c && b == d && a != b) return true;
    }
    for (int value: {0,1}) {
        int pending = 0;
        for (int i=0; i<8; ++i) if (((mask>>i)&1) == value) pending |= 1<<i;
        std::vector<int> stack;
        for (int i=0; i<8; ++i) if (pending & (1<<i)) {
            stack.push_back(i); pending &= ~(1<<i); break;
        }
        while (!stack.empty()) {
            int i=stack.back(); stack.pop_back();
            for (int step: {1,2,4}) {
                int j=i^step;
                if (pending & (1<<j)) { pending &= ~(1<<j); stack.push_back(j); }
            }
        }
        if (pending) return true;
    }
    return false;
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
        if (work>16000000) throw std::runtime_error("Automatic closure exceeds the complex-border budget.");
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
        meshes<1 || nv<1 || nt<1 || resolution<16 || resolution>512)
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
    long long samples=1;
    for (int k=0; k<3; ++k) {
        data.dimensions[k]=int(std::ceil((bounds.upper[k]-bounds.lower[k])/data.spacing))+5;
        samples*=data.dimensions[k]; data.origin[k]=bounds.lower[k]-2.5*data.spacing;
    }
    if (samples>32000000)
        throw std::runtime_error("This resolution exceeds the 32 million sample budget. Lower Resolution.");
    for (size_t i=0; i<data.points.size()/3; ++i) {
        if (i%8192==0) progress(callback,0.016,"Normalizing source points");
        for (int k=0; k<3; ++k) data.points[3LL*i+k]=(data.points[3LL*i+k]-data.origin[k])/data.spacing;
    }
    progress(callback,0.02,"Source shells checked");
    return data;
}

// Each generated vertex has at most six neighbors. Store its link without
// Python dictionaries/sets or one heap allocation per vertex.
struct FanEntry { int vertex=-1, links[2]{-1,-1}, count=0; };
struct VertexFan {
    std::array<FanEntry,6> entries{};
    int count=0;
    int index(int vertex) const {
        for (int i=0; i<count; ++i) if (entries[i].vertex==vertex) return i;
        return -1;
    }
    void add(int a,int b) {
        int i=index(a);
        if (i<0) {
            if (count==6) throw std::runtime_error("Remesh has invalid vertex flow. Increase Resolution.");
            i=count++; entries[i].vertex=a;
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
        int visited=0, pending=1;
        while (pending) {
            int j=0; while (!(pending&(1<<j))) ++j;
            pending&=~(1<<j); if (visited&(1<<j)) continue;
            visited|=1<<j;
            for (int id: fan.entries[j].links) {
                int neighbor=fan.index(id);
                if (neighbor<0) throw std::runtime_error("Remesh has invalid vertex flow. Increase Resolution.");
                if (!(visited&(1<<neighbor))) pending|=1<<neighbor;
            }
        }
        if (visited!=(1<<fan.count)-1)
            throw std::runtime_error("Remesh has a disconnected vertex fan. Increase Resolution.");
        for (double value: mesh.points[i]) if (!std::isfinite(value))
            throw std::runtime_error("Remesh contains invalid points.");
    }
}
struct Triangle { Point a,b,c; Bounds bounds; };
struct BVHNode { Bounds bounds; int left=-1, right=-1, begin=0, end=0; };
class TriangleBVH {
    std::vector<Triangle> triangles;
    std::vector<int> ids;
    std::vector<BVHNode> nodes;
    Callback callback;
    int makeNode(int begin, int end) {
        if (nodes.size()%256==0) progress(callback,0.55,"Measuring source surface");
        Bounds bounds;
        for (int i=begin; i<end; ++i) {
            bounds.include(triangles[ids[i]].bounds.lower); bounds.include(triangles[ids[i]].bounds.upper);
        }
        int index=static_cast<int>(nodes.size());
        nodes.push_back({bounds,-1,-1,begin,end});
        if (end-begin>8) {
            int axis=0;
            for (int k=1; k<3; ++k) if (bounds.upper[k]-bounds.lower[k]>bounds.upper[axis]-bounds.lower[axis]) axis=k;
            int middle=(begin+end)/2;
            std::nth_element(ids.begin()+begin,ids.begin()+middle,ids.begin()+end,[&](int a,int b){
                double ca=triangles[a].bounds.lower[axis]+triangles[a].bounds.upper[axis];
                double cb=triangles[b].bounds.lower[axis]+triangles[b].bounds.upper[axis];
                return ca==cb ? a<b : ca<cb;
            });
            int left=makeNode(begin,middle), right=makeNode(middle,end);
            nodes[index].left=left; nodes[index].right=right;
        }
        return index;
    }
public:
    TriangleBVH(const double* raw, const int* indices, int nt, Callback cb): callback(cb) {
        triangles.reserve(nt); ids.resize(nt); std::iota(ids.begin(),ids.end(),0);
        for (int i=0; i<nt; ++i) {
            if (i%8192==0) progress(callback,0.55,"Measuring source surface");
            Triangle t{};
            for (int k=0; k<3; ++k) {
                t.a[k]=raw[3LL*indices[3LL*i]+k]; t.b[k]=raw[3LL*indices[3LL*i+1]+k];
                t.c[k]=raw[3LL*indices[3LL*i+2]+k];
            }
            t.bounds.include(t.a); t.bounds.include(t.b); t.bounds.include(t.c); triangles.push_back(t);
        }
        makeNode(0,nt);
    }
    double distance(const Point& p) const {
        double best=INFINITY;
        // The median tree has logarithmic depth. Avoid one heap allocation per
        // grid query. A 64-entry stack covers every int-sized triangle buffer.
        std::array<std::pair<int,double>,64> stack{};
        int pending=1; stack[0]={0,0.0};
        while (pending) {
            auto item=stack[--pending];
            if (item.second>best) continue;
            const auto& node=nodes[item.first];
            if (node.left<0) {
                for (int i=node.begin; i<node.end; ++i) {
                    const auto& t=triangles[ids[i]];
                    best=std::min(best,triangleDistanceSquared(p,t.a,t.b,t.c));
                }
            } else {
                std::pair<int,double> a{node.left,nodes[node.left].bounds.distanceSquared(p)};
                std::pair<int,double> b{node.right,nodes[node.right].bounds.distanceSquared(p)};
                if (a.second>b.second) std::swap(a,b);
                if (pending>61) throw std::runtime_error("Distance tree exceeds the traversal budget.");
                if (b.second<=best) stack[pending++]=b;
                if (a.second<=best) stack[pending++]=a;
            }
        }
        return std::sqrt(std::max(0.0,best));
    }
#ifdef BT_DYNAMESH_NODE
    int cudaDistances(const std::vector<double>& queries,std::vector<double>& output,Callback callback,
                      std::string& reason) const {
        auto function=bt_dynamesh::cudaDistance();
        if (!function) { reason=bt_dynamesh::cudaError; return 1; }
        std::vector<BtDmTriangle> packedTriangles(triangles.size());
        for (size_t i=0; i<triangles.size(); ++i) for (int k=0; k<3; ++k) {
            packedTriangles[i].points[k]=triangles[i].a[k];
            packedTriangles[i].points[3+k]=triangles[i].b[k];
            packedTriangles[i].points[6+k]=triangles[i].c[k];
        }
        std::vector<BtDmBvhNode> packedNodes(nodes.size());
        for (size_t i=0; i<nodes.size(); ++i) {
            auto& n=packedNodes[i]; const auto& source=nodes[i];
            for (int k=0; k<3; ++k) { n.lower[k]=source.bounds.lower[k]; n.upper[k]=source.bounds.upper[k]; }
            n.left=source.left; n.right=source.right; n.begin=source.begin; n.end=source.end;
        }
        output.resize(queries.size()/3);
        char error[2048]{};
        int code=function(packedTriangles.data(),int(packedTriangles.size()),packedNodes.data(),int(packedNodes.size()),
                          ids.data(),queries.data(),int(output.size()),output.data(),callback,error,sizeof(error));
        reason=error;
        if (!code && std::any_of(output.begin(),output.end(),[](double v){return !std::isfinite(v) || v<0;})) {
            reason="CUDA returned invalid distances"; code=1;
        }
        return code;
    }
#endif
};
class SurfaceField {
    const std::vector<unsigned char>& occupied;
    TriangleBVH bvh;
    std::vector<double> cache;
    int nx,ny,nz;
public:
    SurfaceField(const double* raw,const int* triangles,int nt,const int* dims,
                 const std::vector<unsigned char>& values,Callback callback):
        occupied(values), bvh(raw,triangles,nt,callback),
        cache(values.size(),std::numeric_limits<double>::quiet_NaN()),nx(dims[0]),ny(dims[1]),nz(dims[2]) {}
    double sample(int index) {
        if (!std::isnan(cache[index])) return cache[index];
        int x=index/(ny*nz), y=(index%(ny*nz))/nz, z=index%nz;
        double distance=std::max(bvh.distance({double(x),double(y),double(z)}),1e-8);
        double value=occupied[index] ? -distance : distance;
        cache[index]=value; return value;
    }
#ifdef BT_DYNAMESH_NODE
    std::string backend="C++", reason;
    void prime(Callback callback) {
        const int sx=ny*nz,sy=nz;
        const int corners[8]={0,sx,sy,sx+sy,1,sx+1,sy+1,sx+sy+1};
        std::vector<unsigned char> marked(occupied.size(),0);
        std::vector<int> queries;
        size_t cells=0;
        for (int x=0; x<nx-1; ++x) {
            progress(callback,0.55,"Collecting surface samples");
            for (int y=0; y<ny-1; ++y) for (int z=0; z<nz-1; ++z) {
                int base=x*sx+y*sy+z,mask=0;
                for (int bit=0; bit<8; ++bit) mask|=occupied[base+corners[bit]]<<bit;
                if (!mask || mask==255) continue;
                if (++cells>500000) throw std::runtime_error("Surface exceeds the 500,000 vertex budget. Lower Resolution.");
                for (int delta:corners) if (!marked[base+delta]) {
                    marked[base+delta]=1; queries.push_back(base+delta);
                }
            }
        }
        std::vector<double> packed,distances;
        packed.reserve(3*queries.size());
        for (int i:queries) packed.insert(packed.end(),{double(i/sx),double((i%sx)/nz),double(i%nz)});
        int code=bvh.cudaDistances(packed,distances,callback,reason);
        if (code==2) throw Cancelled{};
        if (!code) backend="CUDA + C++";
        else {
            distances.resize(queries.size());
            // Independent queries share an immutable BVH. Maya API calls and
            // cancellation remain on this caller thread.
            std::atomic<size_t> next{0};
            std::atomic<bool> stop{false};
            unsigned count=std::min(16u,std::max(1u,std::thread::hardware_concurrency()));
            if (queries.size()<8192) count=1;
            std::vector<std::thread> workers;
            auto work=[&]() {
                while (!stop.load()) {
                    size_t start=next.fetch_add(256);
                    if (start>=queries.size()) break;
                    for (size_t q=start; q<std::min(start+256,queries.size()); ++q)
                        distances[q]=bvh.distance({packed[3*q],packed[3*q+1],packed[3*q+2]});
                }
            };
            try {
                for (unsigned i=1; i<count; ++i) workers.emplace_back(work);
                while (next.load()<queries.size()) {
                    progress(callback,0.55,"Measuring source surface (C++)");
                    size_t start=next.fetch_add(256);
                    for (size_t q=start; q<std::min(start+256,queries.size()); ++q)
                        distances[q]=bvh.distance({packed[3*q],packed[3*q+1],packed[3*q+2]});
                }
            } catch (...) {
                stop=true;
                for (auto& worker:workers) worker.join();
                throw;
            }
            for (auto& worker:workers) worker.join();
        }
        for (size_t q=0; q<queries.size(); ++q) {
            double distance=std::max(distances[q],1e-8);
            cache[queries[q]]=occupied[queries[q]] ? -distance : distance;
        }
    }
#endif
    std::pair<double,Point> valueGradient(const Point& p) {
        int cell[3]={std::clamp(int(std::floor(p[0])),0,nx-2),std::clamp(int(std::floor(p[1])),0,ny-2),
                     std::clamp(int(std::floor(p[2])),0,nz-2)};
        Point t{p[0]-cell[0],p[1]-cell[1],p[2]-cell[2]}, gradient{};
        double value=0;
        for (int bit=0; bit<8; ++bit) {
            int corner[3]={bit&1,(bit>>1)&1,(bit>>2)&1};
            Point weights{};
            for (int k=0; k<3; ++k) weights[k]=corner[k] ? t[k] : 1-t[k];
            double v=sample((cell[0]+corner[0])*ny*nz+(cell[1]+corner[1])*nz+cell[2]+corner[2]);
            value+=v*weights[0]*weights[1]*weights[2];
            for (int k=0; k<3; ++k) gradient[k]+=v*(corner[k] ? 1 : -1)*weights[(k+1)%3]*weights[(k+2)%3];
        }
        return {value,gradient};
    }
    Point project(Point p,const std::array<int,3>& cell) {
        for (int i=0; i<4; ++i) {
            auto field=valueGradient(p);
            double norm=dot(field.second,field.second);
            if (norm<1e-16 || std::abs(field.first)<1e-8) break;
            double weight=field.first/norm, step=std::abs(weight)*std::sqrt(norm);
            if (step>0.5) weight*=0.5/step;
            for (int k=0; k<3; ++k) p[k]=std::clamp(p[k]-weight*field.second[k],cell[k]+1e-4,cell[k]+1-1e-4);
        }
        return p;
    }
};

struct Workspace {
    std::vector<unsigned char> occupied;
    std::unique_ptr<SurfaceField> field;
    std::vector<std::array<int,3>> cells;
    Mesh base;
};
void polishMesh(Mesh& mesh,const Workspace& workspace,int polish,Callback callback);

std::unique_ptr<Mesh> build(const double* raw, int nv, const int* triangles, int nt,
                           const int* offsets, int solids, const int* dims, int polish,
                           Callback callback,std::shared_ptr<Workspace>* savedWorkspace=nullptr) {
    if (!raw || !triangles || !offsets || !dims || nv < 4 || nt < 4 || solids < 1 ||
        polish < 0 || polish > 10) throw std::runtime_error("Invalid native input buffers.");
    const int nx=dims[0], ny=dims[1], nz=dims[2];
    const long long count=static_cast<long long>(nx)*ny*nz;
    if (nx<5 || ny<5 || nz<5 || nx>517 || ny>517 || nz>517 || count>32000000)
        throw std::runtime_error("Invalid grid or sample budget exceeded.");
    if (offsets[0] != 0 || offsets[solids] != nt)
        throw std::runtime_error("Invalid solid offsets.");
    for (int s=0; s<solids; ++s) if (offsets[s]<0 || offsets[s+1]<=offsets[s])
        throw std::runtime_error("Invalid solid offsets.");
    for (long long i=0; i<3LL*nv; ++i) if (!std::isfinite(raw[i]))
        throw std::runtime_error("Nonfinite source coordinates.");
    for (long long i=0; i<3LL*nt; ++i) if (triangles[i]<0 || triangles[i]>=nv)
        throw std::runtime_error("Invalid triangle indices.");
    const int sx=ny*nz, sy=nz;
    std::vector<unsigned char> occupied(static_cast<size_t>(count), 0);
    long long events=0;
    for (int solid=0; solid<solids; ++solid) {
        std::unordered_map<int, std::vector<double>> rows;
        for (int t=offsets[solid]; t<offsets[solid+1]; ++t) {
            if (t%256 == 0) progress(callback, 0.02+0.38*t/nt, "Voxelizing solid union");
            Point a{}, b{}, c{};
            for (int k=0; k<3; ++k) {
                a[k]=raw[3LL*triangles[3LL*t]+k];
                b[k]=raw[3LL*triangles[3LL*t+1]+k];
                c[k]=raw[3LL*triangles[3LL*t+2]+k];
            }
            double area=edge(a,b,c[0],c[1]);
            if (std::abs(area)<1e-14) continue;
            if (area<0) { std::swap(b,c); area=-area; }
            const int x0=std::max(0, static_cast<int>(std::ceil(std::min({a[0],b[0],c[0]}))));
            const int x1=std::min(nx-1, static_cast<int>(std::floor(std::max({a[0],b[0],c[0]}))));
            const int y0=std::max(0, static_cast<int>(std::ceil(std::min({a[1],b[1],c[1]}))));
            const int y1=std::min(ny-1, static_cast<int>(std::floor(std::max({a[1],b[1],c[1]}))));
            for (int x=x0; x<=x1; ++x) {
                if (x%32 == 0) progress(callback, 0.02+0.38*t/nt, "Voxelizing solid union");
                for (int y=y0; y<=y1; ++y) {
                    double e0=edge(a,b,x,y), e1=edge(b,c,x,y), e2=edge(c,a,x,y);
                    if (owned(a,b,e0) && owned(b,c,e1) && owned(c,a,e2)) {
                        rows[x*ny+y].push_back((e1*a[2]+e2*b[2]+e0*c[2])/area);
                        if (++events>16000000)
                            throw std::runtime_error("Too many surface crossings. Lower Resolution or simplify the source.");
                    }
                }
            }
        }
        int rowIndex=0;
        for (auto& row: rows) {
            if (rowIndex++%256 == 0)
                progress(callback, 0.02+0.38*offsets[solid+1]/nt, "Filling solid samples");
            auto& hits=row.second;
            std::sort(hits.begin(), hits.end());
            if (hits.size()%2) throw std::runtime_error("Source cannot be sampled as a closed solid. Check overlapping or invalid faces.");
            for (size_t j=0; j<hits.size(); j+=2) {
                int begin=std::max(0, static_cast<int>(std::ceil(hits[j])));
                int end=std::min(nz, static_cast<int>(std::ceil(hits[j+1])));
                for (int z=begin; z<end; ++z) occupied[row.first*nz+z]=1;
            }
        }
    }
    if (std::none_of(occupied.begin(),occupied.end(), [](unsigned char v){ return v!=0; }))
        throw std::runtime_error("No enclosed volume survived sampling or automatic closure.");
    const int corners[8]={0,sx,sy,sx+sy,1,sx+1,sy+1,sx+sy+1};
    std::array<bool,256> bad{};
    for (int i=0; i<256; ++i) bad[i]=badMask(i);
    for (int pass=0; pass<8; ++pass) {
        bool changed=false;
        for (int x=0; x<nx-1; ++x) {
            progress(callback, 0.4+0.15*x/(nx-1), "Closing voxel contacts");
            for (int y=0; y<ny-1; ++y) for (int z=0; z<nz-1; ++z) {
                int i=x*sx+y*sy+z, mask=0;
                for (int b=0; b<8; ++b) mask |= occupied[i+corners[b]]<<b;
                if (bad[mask]) {
                    for (int offset: corners) occupied[i+offset]=1;
                    changed=true;
                }
            }
        }
        if (!changed) break;
        if (pass == 7) throw std::runtime_error("Voxel contacts remain ambiguous. Increase Resolution or separate touching parts.");
    }
    auto result=std::make_unique<Mesh>();
    auto workspace=std::make_shared<Workspace>();
    workspace->occupied=std::move(occupied);
    workspace->field=std::make_unique<SurfaceField>(raw,triangles,nt,dims,workspace->occupied,callback);
    auto& field=*workspace->field;
    auto& samples=workspace->occupied;
#ifdef BT_DYNAMESH_NODE
    field.prime(callback);
#endif
    std::vector<int> ids(static_cast<size_t>(count), -1);
    auto& cells=workspace->cells;
    std::vector<int> active;
    for (int x=0; x<nx-1; ++x) {
        progress(callback, 0.55+0.22*x/(nx-1), "Building quad surface");
        for (int y=0; y<ny-1; ++y) for (int z=0; z<nz-1; ++z) {
            int i=x*sx+y*sy+z, mask=0;
            for (int b=0; b<8; ++b) mask |= samples[i+corners[b]]<<b;
            if (mask==0 || mask==255) continue;
            Point p{};
            int crossed=0;
            for (auto& e: edges) if (((mask>>e[0])^(mask>>e[1]))&1) {
                ++crossed;
                double va=std::abs(field.sample(i+corners[e[0]])), vb=std::abs(field.sample(i+corners[e[1]]));
                double t=va/(va+vb);
                for (int k=0; k<3; ++k) {
                    int a=(e[0]>>k)&1, b=(e[1]>>k)&1;
                    p[k]+=a+t*(b-a);
                }
            }
            p[0]=x+p[0]/crossed; p[1]=y+p[1]/crossed; p[2]=z+p[2]/crossed;
            p=field.project(p,{x,y,z});
            ids[i]=static_cast<int>(result->points.size());
            result->points.push_back(p); cells.push_back({x,y,z}); active.push_back(i);
            if (result->points.size()>500000)
                throw std::runtime_error("Surface exceeds the 500,000 vertex budget. Lower Resolution.");
        }
    }
    const int strides[3]={sx,sy,1};
    const int around[3][4]={{0,-sy,-sy-1,-1},{0,-1,-sx-1,-sx},{0,-sx,-sx-sy,-sy}};
    for (size_t v=0; v<active.size(); ++v) {
        if (v%8192==0) progress(callback,0.77,"Connecting quads");
        int i=active[v];
        for (int axis=0; axis<3; ++axis) if (samples[i]!=samples[i+strides[axis]]) {
            Quad q{};
            for (int k=0; k<4; ++k) {
                int key=i+around[axis][k];
                if (key<0 || key>=count || ids[key]<0)
                    throw std::runtime_error("Surface reached the sampling boundary. Increase Resolution.");
                q[k]=ids[key];
            }
            if (!samples[i]) std::reverse(q.begin(),q.end());
            result->quads.push_back(q);
            if (result->quads.size()>500000)
                throw std::runtime_error("Surface exceeds the 500,000 quad budget. Lower Resolution.");
        }
    }
    workspace->base=*result;
    polishMesh(*result,*workspace,polish,callback);
    if (savedWorkspace) *savedWorkspace=workspace;
    return result;
}
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

BT_EXPORT int bt_dynamesh_version() { return 4; }
BT_EXPORT int bt_dynamesh_remesh(const double* points, int nv, const int* triangles, int nt,
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
        mesh->spacing=data.spacing; mesh->dimensions=data.dimensions; mesh->closedBorders=data.closedBorders;
        *output=mesh.release();
        return 0;
    } catch (const Cancelled&) { return 2;
    } catch (const std::exception& e) { std::snprintf(error,errorSize,"%s",e.what()); return 1;
    } catch (...) { std::snprintf(error,errorSize,"Unknown native remesh failure."); return 1; }
}
BT_EXPORT int bt_dynamesh_info(void* pointer,double* spacing,int* dimensions,int* closedBorders) {
    if (!pointer || !spacing || !dimensions || !closedBorders) return 1;
    const auto& mesh=*static_cast<Mesh*>(pointer);
    *spacing=mesh.spacing;
    *closedBorders=mesh.closedBorders;
    for (int k=0; k<3; ++k) dimensions[k]=mesh.dimensions[k];
    return 0;
}
BT_EXPORT int bt_dynamesh_copy(void* pointer, double* points, int nv, int* quads, int nq) {
    if (!pointer) return 1;
    auto& mesh=*static_cast<Mesh*>(pointer);
    if (nv!=static_cast<int>(mesh.points.size()) || nq!=static_cast<int>(mesh.quads.size()) || !points || !quads) return 1;
    for (int i=0; i<nv; ++i) for (int k=0; k<3; ++k) points[3LL*i+k]=mesh.points[i][k];
    for (int i=0; i<nq; ++i) for (int k=0; k<4; ++k) quads[4LL*i+k]=mesh.quads[i][k];
    return 0;
}
BT_EXPORT int bt_dynamesh_count(void* pointer, int faces) {
    if (!pointer) return 0;
    auto& mesh=*static_cast<Mesh*>(pointer);
    return static_cast<int>(faces ? mesh.quads.size() : mesh.points.size());
}
BT_EXPORT void bt_dynamesh_free(void* pointer) { delete static_cast<Mesh*>(pointer); }
