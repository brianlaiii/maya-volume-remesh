"""Build the optional, Maya-independent OpenVDB library outside the checkout."""

import ctypes
import hashlib
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile


ROOT_ENV = "MAYA_VOLUME_REMESH_OPENVDB_ROOT"
LIBRARY_ENV = "MAYA_VOLUME_REMESH_OPENVDB_LIBRARY"


def dependency_flags(architecture):
    override = os.environ.get(ROOT_ENV)
    roots = [Path(override)] if override else [Path(p) for p in ("/opt/homebrew", "/usr/local", "/usr", "/opt/openvdb")]
    for root in roots:
        header = root / "include/openvdb/version.h"
        if not header.is_file():
            continue
        folders = [root / p for p in ("lib", "lib64", "lib/x86_64-linux-gnu", "lib/aarch64-linux-gnu")]
        library = next(
            (
                p
                for folder in folders
                for p in folder.glob("libopenvdb.*")
                if p.suffix in (".dylib", ".so") and p.is_file()
            ),
            None,
        )
        if library is None:
            continue
        if sys.platform == "darwin":
            result = subprocess.run(
                ["lipo", str(library), "-verify_arch", architecture], capture_output=True, text=True
            )
            if result.returncode:
                continue
        tbb = next((p for p in library.parent.glob("libtbb.*") if p.suffix in (".dylib", ".so") and p.is_file()), None)
        flags = [
            "-I" + str(root / "include"),
            "-I" + str(root / "include/Imath"),
            "-L" + str(library.parent),
            str(library.resolve()),
            str(tbb.resolve()) if tbb else "-ltbb",
            "-Wl,-rpath," + str(library.parent.resolve()),
        ]
        identity = header.read_bytes() + str(library.resolve()).encode() + str(library.stat().st_mtime_ns).encode()
        if tbb:
            identity += str(tbb.resolve()).encode() + str(tbb.stat().st_mtime_ns).encode()
        return flags, identity
    raise RuntimeError("Compatible OpenVDB/TBB SDK not found. Set " + ROOT_ENV + " to its prefix.")


def build():
    supplied = os.environ.get(LIBRARY_ENV)
    if supplied:
        path = Path(supplied)
        if not path.is_file():
            raise RuntimeError("OpenVDB library does not exist: " + supplied)
        return path
    if sys.platform == "win32":
        raise RuntimeError("OpenVDB needs a matching prebuilt bridge on Windows.")
    architecture = platform.machine()
    flags, identity = dependency_flags(architecture)
    compiler = shlex.split(os.environ.get("CXX", "")) or [
        next((shutil.which(p) for p in ("clang++", "g++", "c++") if shutil.which(p)), "")
    ]
    if not compiler[0]:
        raise RuntimeError("A C++17 compiler is required for OpenVDB.")
    tool = Path(__file__).resolve().parents[1] / "MESH_DynaMesh_CPP"
    source = tool / "dynamesh_vdb.cpp"
    command = compiler + flags[:2] + ["-std=c++17", "-O2", "-DNDEBUG", "-ffp-contract=off"]
    if sys.platform == "darwin":
        command += ["-fvisibility=hidden", "-dynamiclib", "-arch", architecture]
    else:
        # OpenVDB 9.1 templates need default ELF visibility.
        command += ["-shared", "-fPIC", "-pthread"]
    digest = hashlib.sha256(
        source.read_bytes()
        + (tool / "vdb_solver.h").read_bytes()
        + Path(__file__).read_bytes()
        + identity
        + repr(command + flags[2:]).encode()
    ).hexdigest()[:20]
    folder = Path(
        os.environ.get("MAYA_VOLUME_REMESH_CACHE_DIR") or str(Path(tempfile.gettempdir()) / "maya-volume-remesh-cache")
    ) / (sys.platform + "-" + architecture)
    folder.mkdir(parents=True, exist_ok=True)
    suffix = ".dylib" if sys.platform == "darwin" else ".so"
    output = folder / ("mvr_openvdb_" + digest + suffix)
    import fcntl

    with output.with_suffix(suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if output.is_file():
            return output
        with tempfile.TemporaryDirectory(prefix="openvdb-build-", dir=folder) as temp:
            target = Path(temp) / output.name
            stable_id = ["-Wl,-install_name,@rpath/" + output.name] if sys.platform == "darwin" else []
            print("[Maya Volume Remesh] Building optional OpenVDB for " + architecture, flush=True)
            result = subprocess.run(
                command + [str(source)] + flags[2:] + stable_id + ["-o", str(target)], capture_output=True, text=True
            )
            if result.returncode:
                raise RuntimeError("OpenVDB build failed: " + result.stdout + result.stderr)
            os.replace(target, output)
    return output


class VdbKernel:
    def __init__(self):
        self.loaded = None
        self.error = None

    def preload(self):
        if self.error:
            raise RuntimeError(self.error)
        if self.loaded is None:
            from .openvdb import configure

            try:
                library = ctypes.CDLL(str(build()))
                configure(library)
            except (OSError, RuntimeError, AttributeError, subprocess.SubprocessError) as exc:
                self.error = str(exc)
                raise RuntimeError(self.error) from exc
            self.loaded = library
        return self.loaded


kernel = VdbKernel()


if __name__ == "__main__":
    print(build())
