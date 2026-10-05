// Optional double-precision nearest-triangle batches. CPU owns solid topology,
// quad extraction, fitting, polish, and Maya data. No Maya or Python dependency.
#include "dynamesh_cuda_api.h"
#include <cuda_runtime.h>
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <limits>
#include <stdexcept>
#include <string>

namespace {
struct P { double v[3]; };
__device__ P sub(P a,P b) { return {{a.v[0]-b.v[0],a.v[1]-b.v[1],a.v[2]-b.v[2]}}; }
__device__ double dot(P a,P b) { return a.v[0]*b.v[0]+a.v[1]*b.v[1]+a.v[2]*b.v[2]; }
__device__ double triangleDistance(P p,const BtDmTriangle& triangle) {
    P a{{triangle.points[0],triangle.points[1],triangle.points[2]}},
      b{{triangle.points[3],triangle.points[4],triangle.points[5]}},
      c{{triangle.points[6],triangle.points[7],triangle.points[8]}};
    P ab=sub(b,a),ac=sub(c,a),ap=sub(p,a);
    double d1=dot(ab,ap),d2=dot(ac,ap);
    if (d1<=0 && d2<=0) return dot(ap,ap);
    P bp=sub(p,b); double d3=dot(ab,bp),d4=dot(ac,bp);
    if (d3>=0 && d4<=d3) return dot(bp,bp);
    double vc=d1*d4-d3*d2;
    if (vc<=0 && d1>=0 && d3<=0) {
        double v=d1/(d1-d3); P delta{};
        for (int k=0;k<3;++k) delta.v[k]=ap.v[k]-v*ab.v[k];
        return dot(delta,delta);
    }
    P cp=sub(p,c); double d5=dot(ab,cp),d6=dot(ac,cp);
    if (d6>=0 && d5<=d6) return dot(cp,cp);
    double vb=d5*d2-d1*d6;
    if (vb<=0 && d2>=0 && d6<=0) {
        double w=d2/(d2-d6); P delta{};
        for (int k=0;k<3;++k) delta.v[k]=ap.v[k]-w*ac.v[k];
        return dot(delta,delta);
    }
    double va=d3*d6-d5*d4;
    if (va<=0 && d4-d3>=0 && d5-d6>=0) {
        double w=(d4-d3)/((d4-d3)+(d5-d6)); P delta{};
        for (int k=0;k<3;++k) delta.v[k]=bp.v[k]-w*(c.v[k]-b.v[k]);
        return dot(delta,delta);
    }
    double denominator=va+vb+vc,v=vb/denominator,w=vc/denominator; P delta{};
    for (int k=0;k<3;++k) delta.v[k]=ap.v[k]-v*ab.v[k]-w*ac.v[k];
    return dot(delta,delta);
}
__device__ double boxDistance(P p,const BtDmBvhNode& node) {
    double sum=0;
    for (int k=0;k<3;++k) {
        double d=fmax(fmax(node.lower[k]-p.v[k],0.0),p.v[k]-node.upper[k]); sum+=d*d;
    }
    return sum;
}
__global__ void distances(const BtDmTriangle* triangles,int nt,const BtDmBvhNode* nodes,int nn,
                         const int* ids,const double* queries,int count,double* output,int* failed) {
    int query=blockIdx.x*blockDim.x+threadIdx.x;
    if (query>=count) return;
    P p{{queries[3*query],queries[3*query+1],queries[3*query+2]}};
    int stack[64],pending=1; double bounds[64],best=INFINITY;
    stack[0]=0; bounds[0]=0;
    while (pending) {
        --pending; int index=stack[pending]; double lower=bounds[pending];
        if (lower>best) continue;
        if (index<0 || index>=nn) { atomicExch(failed,1); return; }
        const auto& node=nodes[index];
        if (node.left<0) {
            if (node.begin<0 || node.end>nt || node.end<node.begin) { atomicExch(failed,1); return; }
            for (int j=node.begin;j<node.end;++j) {
                int id=ids[j]; if (id<0 || id>=nt) { atomicExch(failed,1); return; }
                best=fmin(best,triangleDistance(p,triangles[id]));
            }
        } else {
            int a=node.left,b=node.right;
            if (a<0 || b<0 || a>=nn || b>=nn || pending>61) { atomicExch(failed,1); return; }
            double da=boxDistance(p,nodes[a]),db=boxDistance(p,nodes[b]);
            if (da>db) { int temp=a;a=b;b=temp; double d=da;da=db;db=d; }
            if (db<=best) { stack[pending]=b;bounds[pending++]=db; }
            if (da<=best) { stack[pending]=a;bounds[pending++]=da; }
        }
    }
    output[query]=sqrt(fmax(0.0,best));
    if (!isfinite(output[query])) atomicExch(failed,1);
}
void check(cudaError_t code,const char* action) {
    if (code!=cudaSuccess) throw std::runtime_error(std::string(action)+": "+cudaGetErrorString(code));
}
template<class T> struct Buffer {
    T* pointer=nullptr;
    void allocate(size_t count) { check(cudaMalloc(reinterpret_cast<void**>(&pointer),count*sizeof(T)),"CUDA allocation"); }
    void upload(const T* source,size_t count,cudaStream_t stream) {
        check(cudaMemcpyAsync(pointer,source,count*sizeof(T),cudaMemcpyHostToDevice,stream),"CUDA upload");
    }
    ~Buffer() { if (pointer) cudaFree(pointer); }
};
struct Stream {
    cudaStream_t value=nullptr;
    Stream() { check(cudaStreamCreateWithFlags(&value,cudaStreamNonBlocking),"CUDA stream"); }
    ~Stream() { if (value) cudaStreamDestroy(value); }
};
}

extern "C" __attribute__((visibility("default"))) int bt_dynamesh_cuda_version() { return 1; }
extern "C" __attribute__((visibility("default"))) int bt_dynamesh_cuda_distances_v1(
    const BtDmTriangle* triangles,int nt,const BtDmBvhNode* nodes,int nn,const int* ids,
    const double* queries,int nq,double* output,BtDmProgress progress,char* error,int errorSize) {
    if (!error || errorSize<1) return 1;
    error[0]=0;
    try {
        if (!triangles || !nodes || !ids || !queries || !output || nt<1 || nn<1 || nq<1 ||
            nt>16000000 || nn>32000000 || nq>4000000)
            throw std::runtime_error("Invalid CUDA distance buffers");
        int devices=0; check(cudaGetDeviceCount(&devices),"CUDA device query");
        if (!devices) throw std::runtime_error("No CUDA device");
        // Respect the process's selected device. Do not reset Maya's context.
        size_t freeBytes=0,totalBytes=0; check(cudaMemGetInfo(&freeBytes,&totalBytes),"CUDA memory query");
        const int batch=std::min(nq,65536);
        size_t required=size_t(nt)*(sizeof(BtDmTriangle)+sizeof(int))+size_t(nn)*sizeof(BtDmBvhNode)+size_t(batch)*4*sizeof(double)+sizeof(int);
        const size_t reserve=256ULL*1024*1024;
        if (required>freeBytes*3/5 || freeBytes<reserve || required>freeBytes-reserve)
            throw std::runtime_error("CUDA memory budget exceeded; use C++");
        Stream stream;
        Buffer<BtDmTriangle> deviceTriangles; Buffer<BtDmBvhNode> deviceNodes;
        Buffer<int> deviceIds,failed; Buffer<double> deviceQueries,deviceOutput;
        deviceTriangles.allocate(nt);deviceNodes.allocate(nn);deviceIds.allocate(nt);
        failed.allocate(1);deviceQueries.allocate(3*batch);deviceOutput.allocate(batch);
        deviceTriangles.upload(triangles,nt,stream.value); deviceNodes.upload(nodes,nn,stream.value); deviceIds.upload(ids,nt,stream.value);
        for (int begin=0;begin<nq;begin+=batch) {
            if (progress && progress(0.55,"Measuring source surface (CUDA)")) return 2;
            int count=std::min(batch,nq-begin),bad=0;
            check(cudaMemsetAsync(failed.pointer,0,sizeof(int),stream.value),"CUDA flag clear");
            deviceQueries.upload(queries+3LL*begin,3*count,stream.value);
            distances<<<(count+127)/128,128,0,stream.value>>>(deviceTriangles.pointer,nt,deviceNodes.pointer,nn,deviceIds.pointer,
                                                           deviceQueries.pointer,count,deviceOutput.pointer,failed.pointer);
            check(cudaGetLastError(),"CUDA distance launch");
            check(cudaMemcpyAsync(output+begin,deviceOutput.pointer,count*sizeof(double),cudaMemcpyDeviceToHost,stream.value),"CUDA result download");
            check(cudaMemcpyAsync(&bad,failed.pointer,sizeof(int),cudaMemcpyDeviceToHost,stream.value),"CUDA flag download");
            check(cudaStreamSynchronize(stream.value),"CUDA distance completion");
            if (bad) throw std::runtime_error("Invalid CUDA distance traversal");
        }
        return 0;
    } catch (const std::exception& exc) {
        std::snprintf(error,errorSize,"%s",exc.what()); return 1;
    } catch (...) {
        std::snprintf(error,errorSize,"Unknown CUDA distance failure"); return 1;
    }
}
