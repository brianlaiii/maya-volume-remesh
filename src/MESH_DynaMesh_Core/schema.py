"""The saved-scene schema shared by both DynaMesh node implementations."""

VERSION = "1.0.0"
NODE_NAME = "btDynaMesh"
TYPE_ID = 0x0012E55A

# name, kind, default, minimum, maximum, tooltip
PARAMETERS = (
    ("enabled", "bool", True, None, None, "Rebuild the volume. Off passes the first input through."),
    ("resolution", "int", 128, 16, 512, "Voxel divisions across the longest world-space bound."),
    ("polish", "int", 1, 0, 10, "Surface-fitted smoothing passes. Uses the cached volume."),
    ("offset", "distance", 0.0, None, None, "Move the rebuilt surface along its vertex normals."),
    ("useSmoothPreview", "bool", True, None, None, "Use each source's active Smooth Mesh Preview."),
    ("smoothNormals", "bool", True, None, None, "Display soft edges on the output mesh."),
)
DEFAULTS = {name: default for name, _kind, default, _lo, _hi, _tip in PARAMETERS}
OUTPUTS = (
    "outputMesh",
    "status",
    "vertexCount",
    "faceCount",
    "voxelWidth",
    "closedBorders",
    "backend",
    "backendReason",
    "computeMilliseconds",
    "volumeBuilds",
)
INPUT_CHILDREN = ("inputMesh", "inputMatrix", "inputSmoothMesh", "inputPreview", "inputSmoothLevel")


def ae_template():
    controls = "\n".join(
        'editorTemplate -annotation "{}" -addControl "{}";'.format(tip, name)
        for name, _kind, _default, _lo, _hi, tip in PARAMETERS
    )
    hidden = "\n".join(
        'editorTemplate -suppress "{}";'.format(name)
        for name in ("input",)
        + INPUT_CHILDREN
        + ("outputMesh", "backend", "backendReason", "computeMilliseconds", "volumeBuilds")
    )
    return """global proc AEbtDynaMeshTemplate(string $nodeName) {
        editorTemplate -beginScrollLayout;
        editorTemplate -beginLayout "DynaMesh" -collapse 0;
        %s
        editorTemplate -endLayout;
        editorTemplate -beginLayout "Result" -collapse 0;
        editorTemplate -addControl "status";
        editorTemplate -addControl "vertexCount";
        editorTemplate -addControl "faceCount";
        editorTemplate -addControl "voxelWidth";
        editorTemplate -addControl "closedBorders";
        editorTemplate -endLayout;
        %s
        AEdependNodeTemplate $nodeName;
        editorTemplate -addExtraControls;
        editorTemplate -endScrollLayout;
    }""" % (controls, hidden)
