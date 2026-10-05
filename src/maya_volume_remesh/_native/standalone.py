"""Content-addressed C++17 kernels with optional, explicit Python fallback."""

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
import warnings
from .fallback import failure_details, require_python_fallback


class Kernel:
    def __init__(self, source, environment, configure):
        self.source = Path(source)
        self.environment = environment
        self.configure = configure
        self.loaded = None
        self.error = None

    def build(self):
        compiler = shlex.split(os.environ.get("CXX", ""))
        if not compiler:
            compiler = [next((shutil.which(name) for name in ("clang++", "g++", "c++") if shutil.which(name)), "")]
        digest = hashlib.sha256(
            self.source.read_bytes() + Path(__file__).read_bytes() + repr(compiler).encode()
        ).hexdigest()[:16]
        suffix = ".dll" if sys.platform == "win32" else ".dylib" if sys.platform == "darwin" else ".so"
        folder = self.source.parent / "plug-ins" / (sys.platform + "-" + platform.machine())
        output = folder / (self.source.stem + "_" + digest + suffix)
        if output.exists():
            return output
        if not compiler[0]:
            raise RuntimeError("No C++ compiler found. Set CXX or install clang++/g++.")
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="kernel-", dir=str(folder)) as temp:
            target = Path(temp) / output.name
            command = compiler + ["-std=c++17", "-O3", "-ffp-contract=off", "-shared"]
            if sys.platform == "darwin":
                command += ["-arch", platform.machine()]
            if sys.platform != "win32":
                command += ["-fPIC"]
            command += [str(self.source), "-o", str(target)]
            print("[Maya Volume Remesh] Building " + self.source.stem, flush=True)
            with subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True
            ) as process:
                lines = []
                for line in process.stdout:
                    print(line, end="", flush=True)
                    lines.append(line)
                if process.wait() or not target.exists():
                    raise RuntimeError("C++ build failed: " + "".join(lines))
            os.replace(str(target), str(output))
        return output

    def preload(self):
        """Load C++ only. Retry earlier failures without offering a fallback."""
        if self.loaded is None:
            library = ctypes.CDLL(str(self.build()))
            self.configure(library)
            self.loaded = library
        self.error = None
        return self.loaded

    def library(self):
        mode = os.environ.get(self.environment, "auto").lower()
        if mode not in ("auto", "cpp", "python"):
            raise ValueError(self.environment + " must be auto, cpp, or python.")
        if mode == "python":
            return None
        first_failure = self.error is None
        if self.loaded is None and self.error is None:
            try:
                self.preload()
            except (OSError, RuntimeError, AttributeError) as exc:
                self.error = failure_details(exc)
        if self.error and mode == "cpp":
            raise RuntimeError(self.error)
        if self.error:
            require_python_fallback(self.source.stem, self.error)
            if first_failure:
                warnings.warn(self.source.stem + " uses Python: " + self.error, RuntimeWarning)
        return self.loaded
