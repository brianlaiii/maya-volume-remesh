// Live Maya generator. Python owns selection, installation and undoable creation.
#define BT_DYNAMESH_NODE
#include "dynamesh.cpp"
#include <maya/MArrayDataHandle.h>
#include <maya/MDataBlock.h>
#include <maya/MDataHandle.h>
#include <maya/MDistance.h>
#include <maya/MFnCompoundAttribute.h>
#include <maya/MFnMatrixAttribute.h>
#include <maya/MFnMesh.h>
#include <maya/MFnMeshData.h>
#include <maya/MFnNumericAttribute.h>
#include <maya/MFnPlugin.h>
#include <maya/MFnTypedAttribute.h>
#include <maya/MFnUnitAttribute.h>
#include <maya/MGlobal.h>
#include <maya/MIntArray.h>
#include <maya/MMatrix.h>
#include <maya/MPlug.h>
#include <maya/MPointArray.h>
#include <maya/MPxNode.h>
#include <chrono>
#include <filesystem>
#include <fstream>

namespace {
enum Attr { Enabled, Resolution, Polish, Offset, Preview, SmoothNormals,
            Input, InputMesh, InputMatrix, InputSmoothMesh, InputPreview, InputSmoothLevel,
            OutputMesh, Status, VertexCount, FaceCount, VoxelWidth, ClosedBorders,
            Backend, BackendReason, ComputeMs, VolumeBuilds, AttrCount };
const char* names[AttrCount]={"enabled","resolution","polish","offset","useSmoothPreview","smoothNormals",
    "input","inputMesh","inputMatrix","inputSmoothMesh","inputPreview","inputSmoothLevel",
    "outputMesh","status","vertexCount","faceCount","voxelWidth","closedBorders",
    "backend","backendReason","computeMilliseconds","volumeBuilds"};
struct InputBuffers {
    std::vector<double> points,matrices;
    std::vector<int> triangles,vertices{0},faces{0};
    bool operator==(const InputBuffers& other) const {
        return points==other.points && matrices==other.matrices && triangles==other.triangles &&
               vertices==other.vertices && faces==other.faces;
    }
};
void require(MStatus status,const char* message) {
    if (!status) throw std::runtime_error(message);
}

class DynaMeshNode:public MPxNode {
    InputBuffers savedInput;
    int savedResolution=-1,savedPolish=-1,builds=0;
    Prepared prepared;
    std::shared_ptr<Workspace> workspace;
    Mesh polished;
public:
    static MObject attrs[AttrCount];
    static MTypeId id;
    static void* creator() { return new DynaMeshNode; }
    static MStatus initialize();
    MStatus compute(const MPlug& plug,MDataBlock& block) override;
    SchedulingType schedulingType() const override { return kSerial; }
};
MObject DynaMeshNode::attrs[AttrCount];
MTypeId DynaMeshNode::id(0x0012E55A);

MStatus DynaMeshNode::initialize() {
    for (int i=Enabled;i<=SmoothNormals;++i) {
        if (i==Offset) {
            MFnUnitAttribute fn; attrs[i]=fn.create(names[i],names[i],MFnUnitAttribute::kDistance,0.0); fn.setKeyable(true);
        } else {
            MFnNumericAttribute fn;
            bool boolean=i==Enabled || i==Preview || i==SmoothNormals;
            attrs[i]=fn.create(names[i],names[i],boolean ? MFnNumericData::kBoolean : MFnNumericData::kInt,i==Resolution ? 128 : 1);
            if (i==Resolution) { fn.setMin(16); fn.setMax(512); }
            if (i==Polish) { fn.setMin(0); fn.setMax(10); }
            fn.setKeyable(true);
        }
        addAttribute(attrs[i]);
    }
    for (int i:{InputMesh,InputSmoothMesh}) {
        MFnTypedAttribute fn; attrs[i]=fn.create(names[i],names[i],MFnData::kMesh); fn.setHidden(true);
    }
    { MFnMatrixAttribute fn; attrs[InputMatrix]=fn.create(names[InputMatrix],names[InputMatrix]); fn.setHidden(true); }
    for (int i:{InputPreview,InputSmoothLevel}) {
        MFnNumericAttribute fn; attrs[i]=fn.create(names[i],names[i],MFnNumericData::kInt,0); fn.setHidden(true);
    }
    { MFnCompoundAttribute fn; attrs[Input]=fn.create("input","input");
      for (int i=InputMesh;i<=InputSmoothLevel;++i) fn.addChild(attrs[i]);
      fn.setArray(true); fn.setUsesArrayDataBuilder(true); fn.setHidden(true); addAttribute(attrs[Input]); }
    for (int i=OutputMesh;i<AttrCount;++i) {
        bool hidden=i==OutputMesh || i==Backend || i==BackendReason || i==ComputeMs || i==VolumeBuilds;
        if (i==OutputMesh || i==Status || i==Backend || i==BackendReason) {
            MFnTypedAttribute fn; attrs[i]=fn.create(names[i],names[i],i==OutputMesh ? MFnData::kMesh : MFnData::kString);
            fn.setWritable(false); fn.setStorable(false); fn.setHidden(hidden); fn.setChannelBox(!hidden);
        } else if (i==VoxelWidth) {
            MFnUnitAttribute fn; attrs[i]=fn.create(names[i],names[i],MFnUnitAttribute::kDistance,0.0);
            fn.setWritable(false); fn.setStorable(false); fn.setChannelBox(true);
        } else {
            MFnNumericAttribute fn; attrs[i]=fn.create(names[i],names[i],i==ComputeMs ? MFnNumericData::kDouble : MFnNumericData::kInt,0);
            fn.setWritable(false); fn.setStorable(false); fn.setHidden(hidden); fn.setChannelBox(!hidden);
        }
        addAttribute(attrs[i]);
    }
    for (int input=Enabled;input<=InputSmoothLevel;++input)
        for (int output=OutputMesh;output<AttrCount;++output) attributeAffects(attrs[input],attrs[output]);
    return MS::kSuccess;
}

MStatus DynaMeshNode::compute(const MPlug& plug,MDataBlock& block) {
    bool known=false;
    for (int i=OutputMesh;i<AttrCount;++i) if (plug.attribute()==attrs[i]) known=true;
    if (!known) return MS::kUnknownParameter;
    auto started=std::chrono::steady_clock::now();
    MFnMeshData ownerFn;
    MObject output=ownerFn.create(),first;
    MMatrix firstMatrix;
    std::string status="Ready",backend="C++",backendReason;
    int vertexCount=0,faceCount=0,closedBorders=0;
    double width=0;
    auto passthrough=[&]() {
        if (first.isNull()) return;
        MFnMeshData owner; output=owner.create();
        MFnMesh fn; MStatus code;
        MObject mesh=fn.copy(first,output,&code); require(code,"Could not copy the first input"); fn.setObject(mesh);
        MPointArray points; require(fn.getPoints(points),"Could not read the first input");
        for (unsigned i=0;i<points.length();++i) points[i]*=firstMatrix;
        require(fn.setPoints(points),"Could not transform the first input");
        vertexCount=fn.numVertices(); faceCount=fn.numPolygons();
    };
    try {
        bool enabled=block.inputValue(attrs[Enabled]).asBool();
        bool preview=block.inputValue(attrs[Preview]).asBool();
        InputBuffers input;
        auto values=block.inputArrayValue(attrs[Input]);
        for (unsigned i=0;i<values.elementCount();++i) {
            require(values.jumpToArrayElement(i),"Could not read an input element");
            auto value=values.inputValue();
            MObject source=value.child(attrs[InputMesh]).asMesh();
            MMatrix matrix=value.child(attrs[InputMatrix]).asMatrix();
            if (first.isNull() && !source.isNull()) { first=source; firstMatrix=matrix; }
            if (!enabled && !first.isNull()) break;
            MStatus code; MFnMesh fn(source,&code); require(code,"Connect a valid polygon mesh");
            bool smooth=enabled && preview && value.child(attrs[InputPreview]).asInt()!=0;
            if (smooth) {
                int level=value.child(attrs[InputSmoothLevel]).asInt();
                if (level<0 || level>7 || fn.numPolygons()*std::pow(4.0,level)>2000000)
                    throw std::runtime_error("Smooth preview exceeds two million faces. Lower the source preview level");
                source=value.child(attrs[InputSmoothMesh]).asMesh();
                require(fn.setObject(source),"Could not read Smooth Mesh Preview");
            }
            if (fn.numPolygons()>2000000) throw std::runtime_error("Input exceeds two million faces");
            if (!enabled) continue;
            MPointArray points; MIntArray counts,triangles;
            require(fn.getPoints(points,MSpace::kObject),"Could not read input points");
            require(fn.getTriangles(counts,triangles),"Could not triangulate input");
            input.points.reserve(input.points.size()+3*points.length());
            for (unsigned p=0;p<points.length();++p) input.points.insert(input.points.end(),{points[p].x,points[p].y,points[p].z});
            input.triangles.insert(input.triangles.end(),triangles.begin(),triangles.end());
            for (int r=0;r<4;++r) for (int c=0;c<4;++c) input.matrices.push_back(matrix[r][c]);
            input.vertices.push_back(int(input.points.size()/3)); input.faces.push_back(int(input.triangles.size()/3));
        }
        if (first.isNull()) throw std::runtime_error("Connect at least one polygon mesh");
        if (!enabled) {
            status="Disabled; first input passed through"; backend="Pass through"; passthrough();
        } else {
            int resolution=block.inputValue(attrs[Resolution]).asInt(),polish=block.inputValue(attrs[Polish]).asInt();
            if (resolution!=savedResolution || !(input==savedInput) || !workspace) {
                auto newPrepared=prepare(input.points.data(),int(input.points.size()/3),input.triangles.data(),int(input.triangles.size()/3),
                                         input.vertices.data(),input.faces.data(),int(input.vertices.size())-1,input.matrices.data(),resolution,nullptr);
                std::shared_ptr<Workspace> newWorkspace;
                auto base=build(newPrepared.points.data(),int(newPrepared.points.size()/3),newPrepared.triangles.data(),int(newPrepared.triangles.size()/3),
                                newPrepared.offsets.data(),int(newPrepared.offsets.size())-1,newPrepared.dimensions.data(),0,nullptr,&newWorkspace);
                validate(*base,nullptr);
                prepared=std::move(newPrepared); workspace=std::move(newWorkspace); savedInput=std::move(input);
                savedResolution=resolution; savedPolish=-1; ++builds;
            }
            if (polish!=savedPolish) {
                Mesh next=workspace->base; polishMesh(next,*workspace,polish,nullptr);
                polished=std::move(next); savedPolish=polish;
            }
            Mesh mesh=polished;
            for (auto& p:mesh.points) for (int k=0;k<3;++k) p[k]=prepared.origin[k]+p[k]*prepared.spacing;
            double offset=block.inputValue(attrs[Offset]).asDistance().asCentimeters();
            if (!std::isfinite(offset)) throw std::runtime_error("Offset must be finite");
            if (offset) {
                std::vector<Point> normals(mesh.points.size());
                for (const auto& face:mesh.quads) {
                    Point normal{};
                    for (int j=0;j<4;++j) {
                        const auto& p=mesh.points[face[j]]; const auto& q=mesh.points[face[(j+1)%4]];
                        for (int k=0;k<3;++k) normal[k]+=p[(k+1)%3]*q[(k+2)%3]-p[(k+2)%3]*q[(k+1)%3];
                    }
                    for (int vertex:face) for (int k=0;k<3;++k) normals[vertex][k]+=normal[k];
                }
                for (size_t i=0;i<mesh.points.size();++i) {
                    double length=std::sqrt(dot(normals[i],normals[i]));
                    if (length>1e-20) for (int k=0;k<3;++k) mesh.points[i][k]+=offset*normals[i][k]/length;
                }
            }
            MPointArray points; points.setLength(unsigned(mesh.points.size()));
            MIntArray counts,indices; counts.setLength(unsigned(mesh.quads.size())); indices.setLength(unsigned(mesh.quads.size()*4));
            for (size_t i=0;i<mesh.points.size();++i) {
                const auto& p=mesh.points[i];
                for (double value:p) if (!std::isfinite(value)) throw std::runtime_error("Output contains nonfinite points");
                points[unsigned(i)]=MPoint(p[0],p[1],p[2]);
            }
            for (size_t i=0;i<mesh.quads.size();++i) { counts[unsigned(i)]=4;
                for (int k=0;k<4;++k) indices[unsigned(4*i+k)]=mesh.quads[i][k]; }
            MFnMesh fn; MStatus code;
            fn.create(int(points.length()),int(counts.length()),points,counts,indices,output,&code); require(code,"Could not create output mesh data");
            bool soft=block.inputValue(attrs[SmoothNormals]).asBool();
            for (int i=0;i<fn.numEdges();++i) fn.setEdgeSmoothing(i,soft);
            fn.cleanupEdgeSmoothing();
            vertexCount=fn.numVertices(); faceCount=fn.numPolygons(); width=prepared.spacing; closedBorders=prepared.closedBorders;
            backend=workspace->field->backend;
            backendReason=workspace->field->reason;
        }
    } catch (const std::exception& error) {
        status=std::string("Invalid: ")+error.what()+". First input passed through."; backend="Pass through";
        try { passthrough(); } catch (...) { output=ownerFn.create(); vertexCount=faceCount=0; }
    }
    block.outputValue(attrs[OutputMesh]).setMObject(output);
    block.outputValue(attrs[Status]).setString(status.c_str());
    block.outputValue(attrs[VertexCount]).setInt(vertexCount); block.outputValue(attrs[FaceCount]).setInt(faceCount);
    block.outputValue(attrs[VoxelWidth]).setMDistance(MDistance(width,MDistance::kCentimeters));
    block.outputValue(attrs[ClosedBorders]).setInt(closedBorders); block.outputValue(attrs[Backend]).setString(backend.c_str());
    block.outputValue(attrs[BackendReason]).setString(backendReason.c_str());
    block.outputValue(attrs[ComputeMs]).setDouble(std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-started).count());
    block.outputValue(attrs[VolumeBuilds]).setInt(builds);
    for (int i=OutputMesh;i<AttrCount;++i) block.outputValue(attrs[i]).setClean();
    return MS::kSuccess;
}
} // namespace

MStatus initializePlugin(MObject object) {
    MFnPlugin plugin(object,"Brian Lai","1.0.0","Any");
    std::filesystem::path folder(plugin.loadPath().asChar());
    if (!std::filesystem::is_directory(folder)) folder=folder.parent_path();
    std::ifstream manifest(folder/"btDynaMeshCuda.path");
    std::string file; if (std::getline(manifest,file) && std::filesystem::path(file).filename()==file)
        bt_dynamesh::cudaLibrary=(folder/file).string();
    MStatus code=plugin.registerNode("btDynaMesh",DynaMeshNode::id,DynaMeshNode::creator,DynaMeshNode::initialize);
    if (!code) return code;
    const char* ae=R"MEL(global proc AEbtDynaMeshTemplate(string $nodeName) {
        editorTemplate -beginScrollLayout;
        editorTemplate -beginLayout "DynaMesh" -collapse 0;
        editorTemplate -addControl "enabled";
        editorTemplate -annotation "Voxel divisions across the longest world-space bound." -addControl "resolution";
        editorTemplate -annotation "Surface-fitted smoothing passes. Uses the cached volume." -addControl "polish";
        editorTemplate -annotation "Move the rebuilt surface along its vertex normals." -addControl "offset";
        editorTemplate -addControl "useSmoothPreview";
        editorTemplate -addControl "smoothNormals";
        editorTemplate -endLayout;
        editorTemplate -beginLayout "Result" -collapse 0;
        editorTemplate -addControl "status";
        editorTemplate -addControl "vertexCount";
        editorTemplate -addControl "faceCount";
        editorTemplate -addControl "voxelWidth";
        editorTemplate -addControl "closedBorders";
        editorTemplate -endLayout;
        editorTemplate -suppress "input";
        editorTemplate -suppress "inputMesh";
        editorTemplate -suppress "inputMatrix";
        editorTemplate -suppress "inputSmoothMesh";
        editorTemplate -suppress "inputPreview";
        editorTemplate -suppress "inputSmoothLevel";
        editorTemplate -suppress "outputMesh";
        editorTemplate -suppress "backend";
        editorTemplate -suppress "backendReason";
        editorTemplate -suppress "computeMilliseconds";
        editorTemplate -suppress "volumeBuilds";
        AEdependNodeTemplate $nodeName;
        editorTemplate -addExtraControls;
        editorTemplate -endScrollLayout;
    })MEL";
    MGlobal::executeCommand(ae,false,false);
    return code;
}
MStatus uninitializePlugin(MObject object) { return MFnPlugin(object).deregisterNode(DynaMeshNode::id); }
