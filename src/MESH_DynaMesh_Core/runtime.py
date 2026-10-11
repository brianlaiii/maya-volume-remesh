"""Prefer optional OpenVDB, built-in C++, then the independent Python node."""

import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from .schema import NODE_NAME, VERSION

PLUGIN_DIR_ENV = "MAYA_VOLUME_REMESH_PLUGIN_DIR"
FORCE_ENV = "MAYA_VOLUME_REMESH_FORCE_PY"
_cuda_attempted = False
_vdb_attempted = False
_vdb_ready = False


def _atomic_write(path, data):
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _openvdb(cmds):
    """Install a content-keyed optional library before registering the Maya node."""
    global _vdb_attempted, _vdb_ready
    from .openvdb import enabled
    from .vdb_build import build, kernel

    if _vdb_attempted or not enabled():
        return
    _vdb_attempted = True
    kernel.preload()
    source = build()
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()[:20]
    directory = Path(
        os.environ.get(PLUGIN_DIR_ENV)
        or os.path.join(cmds.internalVar(userAppDir=True), str(cmds.about(majorVersion=True)), "plug-ins")
    )
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / ("mvr_openvdb_" + digest + source.suffix)
    if target.exists() and target.read_bytes() != data:
        raise RuntimeError("Refusing to overwrite an edited OpenVDB runtime: " + str(target))
    manifest = directory / "btDynaMeshVdb.path"
    marker = directory / "btDynaMeshVdb.path.mvr.json"
    previous_data = manifest.read_bytes() if manifest.exists() else None
    previous = previous_data.decode().strip() if previous_data else None
    if previous and previous != target.name and "btDynaMesh" in (cmds.allNodeTypes() or []):
        raise RuntimeError("OpenVDB runtime changed. Save and restart Maya.")
    owned = json.loads(marker.read_text()) if marker.exists() else {}
    if previous_data and previous != target.name and hashlib.sha256(previous_data).hexdigest() != owned.get("sha256"):
        raise RuntimeError("Refusing to overwrite an unowned or edited OpenVDB manifest: " + str(manifest))
    if not target.exists():
        fd, temporary = tempfile.mkstemp(prefix=target.name + ".", dir=str(directory))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            try:
                os.link(temporary, target)
            except FileExistsError:
                if target.read_bytes() != data:
                    raise RuntimeError("Refusing to overwrite an edited OpenVDB runtime: " + str(target))
        finally:
            os.unlink(temporary)
    payload = (target.name + "\n").encode()
    if previous_data != payload:
        _atomic_write(manifest, payload)
    if previous_data is None or owned:
        _atomic_write(marker, json.dumps({"sha256": hashlib.sha256(payload).hexdigest()}).encode())
    os.environ["MAYA_VOLUME_REMESH_OPENVDB_LIBRARY"] = str(target)
    _vdb_ready = True


def _cuda(cmds):
    """Build an immutable sidecar on a supported NVIDIA host, once per session."""
    global _cuda_attempted
    if _cuda_attempted or os.environ.get("MAYA_VOLUME_REMESH_CUDA", "auto").lower() in ("0", "off"):
        return
    _cuda_attempted = True
    if sys.platform != "linux" or platform.machine() != "x86_64":
        return
    if os.environ.get("MAYA_VOLUME_REMESH_CUDA_LIBRARY"):
        return
    nvcc = os.environ.get("CUDACXX") or shutil.which("nvcc")
    if not nvcc:
        for directory in (
            os.environ.get("CUDA_ROOT"),
            os.environ.get("CUDA_HOME"),
            os.environ.get("CUDA_PATH"),
            "/opt/cuda",
            "/usr/local/cuda",
        ):
            candidate = Path(directory) / "bin" / "nvcc" if directory else None
            if candidate and candidate.is_file():
                nvcc = str(candidate)
                break
    if not nvcc:
        return
    root = Path(__file__).resolve().parents[1] / "MESH_DynaMesh_CPP"
    files = [root / name for name in ("dynamesh_cuda.cu", "dynamesh_cuda_api.h", "build_dynamesh_cuda.sh")]
    compiler = subprocess.run([nvcc, "--version"], capture_output=True, text=True, check=False)
    host = os.environ.get("CUDAHOSTCXX") or os.environ.get("CXX") or shutil.which("g++") or ""
    environment = repr(
        (nvcc, compiler.stdout, host, [(key, os.environ.get(key)) for key in ("CUDA_ROOT", "CUDA_HOME", "CUDA_PATH")])
    ).encode()
    key = hashlib.sha256(b"".join(p.read_bytes() for p in files) + environment).hexdigest()[:20]
    directory = Path(
        os.environ.get(PLUGIN_DIR_ENV)
        or os.path.join(cmds.internalVar(userAppDir=True), str(cmds.about(majorVersion=True)), "plug-ins")
    )
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / ("libbtDynaMeshCuda_" + key + ".so")
    if not output.exists():
        from maya_volume_remesh._native.loader import run_streamed_subprocess

        process = run_streamed_subprocess(["bash", str(files[-1]), str(output)])
        if process.returncode or not output.is_file():
            raise RuntimeError("DynaMesh CUDA build failed: " + process.stdout)
    # A simple relative manifest lets a saved C++ scene find the installed
    # sidecar without importing this repository. Never overwrite a live binary.
    (directory / "btDynaMeshCuda.path").write_text(output.name + "\n")
    os.environ["MAYA_VOLUME_REMESH_CUDA_LIBRARY"] = str(output)


def ensure_loaded(backend="auto", prepare_cuda=True):
    import maya.cmds as cmds
    from maya_volume_remesh._native import NativePluginConfig
    from maya_volume_remesh._native.dual_backend import ensure_backend, load_native_node

    if backend not in ("auto", "cpp", "python"):
        raise ValueError("Backend must be auto, cpp, or python")
    mode = os.environ.get("MAYA_VOLUME_REMESH_BACKEND", "auto") if backend == "auto" else backend
    if mode not in ("auto", "cpp", "python"):
        raise ValueError("MAYA_VOLUME_REMESH_BACKEND must be auto, cpp, or python")
    root = Path(__file__).resolve().parent
    config = NativePluginConfig(
        "btDynaMesh_0012E55A",
        "MESH_DynaMesh_CPP",
        "dynamesh_node.cpp",
        "build_dynamesh_node.sh",
        "DynaMesh",
        str(root.parent / "MESH_Create_DynaMesh.py"),
        fallback_source_root=str(root.parent),
    )

    def native():
        try:
            _openvdb(cmds)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            os.environ["MAYA_VOLUME_REMESH_OPENVDB_REASON"] = str(exc)
            cmds.warning("OpenVDB unavailable; using built-in C++. " + str(exc))
        try:
            path = load_native_node(cmds, NODE_NAME, config, PLUGIN_DIR_ENV)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            os.environ["MAYA_VOLUME_REMESH_NATIVE_REASON"] = str(exc)
            raise
        # The CUDA binary is immutable and is first resolved during evaluation.
        # This also supports C++ already loaded by the manual initializer.
        if prepare_cuda and not _vdb_ready:
            try:
                _cuda(cmds)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                cmds.warning("DynaMesh CUDA unavailable; using C++. " + str(exc))
        return path

    if mode == "cpp" and os.environ.get(FORCE_ENV) != "1":
        return native()
    previous = os.environ.get(FORCE_ENV)
    if mode == "python":
        os.environ[FORCE_ENV] = "1"
    try:
        return ensure_backend(
            cmds,
            NODE_NAME,
            native,
            {
                "bt_dynamesh_py.py": root / "dynamesh_py.py",
                "bt_dynamesh_schema.py": root / "schema.py",
                "bt_dynamesh_solver.py": root / "solver.py",
                "bt_dynamesh_surface.py": root / "surface.py",
                "bt_dynamesh_boundary.py": root / "boundary.py",
            },
            VERSION,
            PLUGIN_DIR_ENV,
            FORCE_ENV,
            confirm_fallback=False,
        )
    finally:
        if previous is None:
            os.environ.pop(FORCE_ENV, None)
        else:
            os.environ[FORCE_ENV] = previous


def preload():
    """Manual initialization/update prebuild: C++ only, with CUDA left on demand."""
    return ensure_loaded("cpp", prepare_cuda=False)
