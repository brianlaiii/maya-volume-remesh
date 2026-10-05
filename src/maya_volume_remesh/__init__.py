"""Live volume remeshing for Maya. Importing this package does not load Maya."""

from pathlib import Path
import sys

__version__ = "0.1.0"


def _check_source():
    """Do not mix this standalone package with another DynaMesh installation."""
    expected = Path(__file__).resolve().parents[1] / "MESH_DynaMesh_Core"
    core = sys.modules.get("MESH_DynaMesh_Core")
    if core is not None and Path(core.__file__).resolve().parent != expected:
        raise RuntimeError("Save and restart Maya before changing DynaMesh installations")


def create(
    resolution=128, polish=1, offset=0.0, useSmoothPreview=True, smoothNormals=True, enabled=True, backend="auto"
):
    """Create and select a live node. Return (node, output_transform).

    Inputs come from Maya's current mesh, group or component selection.
    Offset is expressed in Maya's internal centimeters.
    """
    _check_source()
    from MESH_DynaMesh_Core.scene import create_node_from_selection

    return create_node_from_selection(
        resolution=resolution,
        polish=polish,
        offset=offset,
        useSmoothPreview=useSmoothPreview,
        smoothNormals=smoothNormals,
        enabled=enabled,
        backend=backend,
    )


def preload(backend="auto"):
    """Prepare the runtime without creating scene nodes."""
    _check_source()
    from MESH_DynaMesh_Core.runtime import ensure_loaded

    return ensure_loaded(backend)
