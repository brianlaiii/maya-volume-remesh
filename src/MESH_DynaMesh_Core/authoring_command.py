"""Undoable live-node creation and the earlier static-output compatibility API."""

import maya.api.OpenMaya as om


def maya_useNewAPI():
    pass


class CreateDynaMesh(om.MPxCommand):
    def __init__(self):
        super().__init__()
        self._dag = self._sets = self._visibility = None
        self._dag_done = self._sets_done = self._visibility_done = False

    def isUndoable(self):
        return True

    def doIt(self, args):
        from MESH_DynaMesh_Core.scene import _PENDING

        if len(args) != 1 or args.asString(0) not in _PENDING:
            raise ValueError("Create DynaMesh through its selection tool.")
        result, sources, hide = _PENDING.pop(args.asString(0))
        self._before = om.MGlobal.getActiveSelectionList()
        data_fn = om.MFnMeshData()
        self._data = data_fn.create()
        mesh = om.MFnMesh()
        mesh.create(
            om.MPointArray(result.points),
            [4] * len(result.quads),
            [v for face in result.quads for v in face],
            parent=self._data,
        )
        mesh.setEdgeSmoothings(list(range(mesh.numEdges)), [True] * mesh.numEdges)
        mesh.cleanupEdgeSmoothing()
        self._dag = om.MDagModifier()
        self._transform = self._dag.createNode("transform")
        self._dag.renameNode(self._transform, "dynamesh1")
        self._shape = self._dag.createNode("mesh", self._transform)
        self._dag.renameNode(self._shape, "dynameshShape1")
        self._visibility = om.MDGModifier()
        if hide:
            for handle in sources:
                self._visibility.newPlugValueBool(
                    om.MFnDependencyNode(handle.object()).findPlug("visibility", False), False
                )
        self.redoIt()

    def redoIt(self):
        try:
            self._dag_done = True
            self._dag.doIt()
            # A history-free Maya mesh reads its cached input. Setting only
            # inMesh leaves outMesh unevaluable on these fresh shapes.
            shape_fn = om.MFnDependencyNode(self._shape)
            shape_fn.findPlug("cachedInMesh", False).setMObject(self._data)
            om.MFnMesh(shape_fn.findPlug("outMesh", False).asMObject())
            if self._sets is None:
                self._sets = om.MDGModifier()
                path = om.MFnDagNode(self._shape).fullPathName()
                self._sets.commandToExecute('sets -edit -forceElement initialShadingGroup "' + path + '";')
            self._sets_done = True
            self._sets.doIt()
            self._visibility_done = True
            self._visibility.doIt()
            self._name = om.MFnDagNode(self._transform).fullPathName()
            selection = om.MSelectionList()
            selection.add(self._name)
            om.MGlobal.setActiveSelectionList(selection)
            self.setResult(self._name)
        except BaseException:
            self.undoIt()
            raise

    def undoIt(self):
        try:
            if self._visibility_done:
                self._visibility.undoIt()
                self._visibility_done = False
            if self._sets_done:
                self._sets.undoIt()
                self._sets_done = False
            if self._dag_done:
                self._dag.undoIt()
                self._dag_done = False
        finally:
            om.MGlobal.setActiveSelectionList(self._before)


class CreateDynaMeshNode(om.MPxCommand):
    def __init__(self):
        super().__init__()
        self._dag = self._dg = self._sets = None
        self._dag_done = self._dg_done = self._sets_done = False

    def isUndoable(self):
        return True

    def doIt(self, args):
        from MESH_DynaMesh_Core.scene import _LIVE_PENDING
        from MESH_DynaMesh_Core.schema import NODE_NAME

        if len(args) != 1 or args.asString(0) not in _LIVE_PENDING:
            raise ValueError("Create DynaMesh through its selection tool")
        paths, settings = _LIVE_PENDING.pop(args.asString(0))
        self._before = om.MGlobal.getActiveSelectionList()
        self._dag = om.MDagModifier()
        self._transform = self._dag.createNode("transform")
        self._dag.renameNode(self._transform, "dynamesh1")
        self._shape = self._dag.createNode("mesh", self._transform)
        self._dag.renameNode(self._shape, "dynameshShape1")
        self._dg = om.MDGModifier()
        self._node = self._dg.createNode(NODE_NAME)
        self._dg.renameNode(self._node, "btDynaMesh1")
        fn = om.MFnDependencyNode(self._node)
        for name, value in settings.items():
            plug = fn.findPlug(name, False)
            if isinstance(value, bool):
                self._dg.newPlugValueBool(plug, value)
            elif isinstance(value, int):
                self._dg.newPlugValueInt(plug, value)
            else:
                # offset is in Maya's internal centimeters, like node evaluation.
                self._dg.newPlugValueDouble(plug, value)
        inputs = fn.findPlug("input", False)
        for i, path in enumerate(paths):
            source = om.MFnDependencyNode(path.node())
            element = inputs.elementByLogicalIndex(i)
            for target, attr in (
                ("inputMesh", "outMesh"),
                ("inputSmoothMesh", "outSmoothMesh"),
                ("inputPreview", "displaySmoothMesh"),
                ("inputSmoothLevel", "smoothLevel"),
            ):
                self._dg.connect(source.findPlug(attr, False), element.child(fn.attribute(target)))
            matrix = source.findPlug("worldMatrix", False).elementByLogicalIndex(path.instanceNumber())
            self._dg.connect(matrix, element.child(fn.attribute("inputMatrix")))
        self._dg.connect(fn.findPlug("outputMesh", False), om.MFnDependencyNode(self._shape).findPlug("inMesh", False))
        self.redoIt()

    def redoIt(self):
        try:
            self._dag_done = True
            self._dag.doIt()
            self._dg_done = True
            self._dg.doIt()
            fn = om.MFnDependencyNode(self._node)
            status = fn.findPlug("status", False).asString()
            if status.startswith("Invalid"):
                raise ValueError(status)
            if fn.findPlug("faceCount", False).asInt() == 0:
                raise ValueError("DynaMesh produced no faces")
            mesh_path = om.MFnDagNode(self._shape).fullPathName()
            if self._sets is None:
                self._sets = om.MDGModifier()
                self._sets.commandToExecute('sets -edit -forceElement initialShadingGroup "' + mesh_path + '";')
            self._sets_done = True
            self._sets.doIt()
            self._name = fn.name()
            self._mesh_name = om.MFnDagNode(self._transform).fullPathName()
            selection = om.MSelectionList()
            selection.add(self._name)
            om.MGlobal.setActiveSelectionList(selection)
            self.setResult([self._name, self._mesh_name])
        except BaseException:
            self.undoIt()
            raise

    def undoIt(self):
        try:
            if self._sets_done:
                self._sets.undoIt()
                self._sets_done = False
            if self._dg_done:
                self._dg.undoIt()
                self._dg_done = False
            if self._dag_done:
                self._dag.undoIt()
                self._dag_done = False
        finally:
            om.MGlobal.setActiveSelectionList(self._before)


def initializePlugin(plugin):
    fn = om.MFnPlugin(plugin, "Brian Lai", "2.0", "Any")
    fn.registerCommand("btCreateDynaMesh", CreateDynaMesh)
    fn.registerCommand("btCreateDynaMeshNode", CreateDynaMeshNode)


def uninitializePlugin(plugin):
    fn = om.MFnPlugin(plugin)
    fn.deregisterCommand("btCreateDynaMeshNode")
    fn.deregisterCommand("btCreateDynaMesh")
