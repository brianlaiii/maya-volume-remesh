"""MESH_Create_DynaMesh.py

Create a live DynaMesh node from selected polygon meshes, groups or components.

Run this script to create one btDynaMesh node and its output mesh. No tool window.

Use:
- Select polygon meshes or groups. Components use their whole meshes.
- Run the tool, then adjust Resolution, Polish and Offset in Maya's attributes.
- Raise Resolution for smaller details. Delete History bakes the output.

What this tool does:
- Creates a live quad volume union and retains the original geometry.
- Closes open borders automatically in calculation data.
- Follows active Smooth Mesh Preview and caches repeated parameter edits.
- Supports one-step creation undo and normal Maya parameter undo.
- Prefers optional CUDA, then C++, then independent Python. Fallback is automatic.
- Rebuilds topology. Source UVs, materials and deformation are not transferred.
- Can lose thin parts or small gaps. Flat sheets do not gain thickness.

System requirement:
- Maya 2024 or later. Enable Maya Undo.
- Native node: matching Maya SDK and C++17 compiler, or a supplied Maya binary.
- CUDA is optional and needs Linux x86_64, NVIDIA GPU and nvcc.
- Python fallback needs only Maya Python. No Qt, NumPy or compiler.
- Keep MESH_DynaMesh_Core, MESH_DynaMesh_CPP and maya_volume_remesh beside this entry.
"""

import os as _dynamesh_os
import sys as _dynamesh_sys


def _create_dynamesh(entry):
    root = _dynamesh_os.path.dirname(_dynamesh_os.path.abspath(entry))
    if not _dynamesh_os.path.isdir(_dynamesh_os.path.join(root, "MESH_DynaMesh_Core")):
        raise ImportError("Keep MESH_DynaMesh_Core beside MESH_Create_DynaMesh.py")
    loaded = _dynamesh_sys.modules.get("MESH_DynaMesh_Core")
    if loaded is not None:
        previous = _dynamesh_os.path.dirname(_dynamesh_os.path.dirname(loaded.__file__))
        if _dynamesh_os.path.realpath(previous) != _dynamesh_os.path.realpath(root):
            raise RuntimeError("Restart Maya before switching DynaMesh installations")
    if root in _dynamesh_sys.path:
        _dynamesh_sys.path.remove(root)
    _dynamesh_sys.path.insert(0, root)
    from MESH_DynaMesh_Core.scene import create_node_from_selection

    return create_node_from_selection()


_dynamesh_entry = _dynamesh_sys._getframe().f_code.co_filename
if not _dynamesh_os.path.isfile(_dynamesh_entry):
    _dynamesh_entry = globals().get("__file__", "")
_create_dynamesh(_dynamesh_entry)
