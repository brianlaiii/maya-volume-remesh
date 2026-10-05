"""Host checks for standalone imports and reversible Maya module installation."""

# ruff: noqa: E402

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import maya_volume_remesh

spec = importlib.util.spec_from_file_location("mvr_installer", ROOT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class StandalonePackageTests(unittest.TestCase):
    def test_import_and_scalar_solve_without_private_packages_or_maya(self):
        code = """import importlib.abc,sys
sys.path.insert(0,sys.argv[1])
class BlockPrivate(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split(".")[0] in ("brian_tools", "maya", "numpy", "PySide2", "PySide6"):
            raise AssertionError("Unexpected dependency: "+fullname)
sys.meta_path.insert(0,BlockPrivate())
import maya_volume_remesh
from MESH_DynaMesh_Core.solver import remesh
points=[(x,y,z) for z in (-1,1) for y in (-1,1) for x in (-1,1)]
faces=((0,2,3,1),(4,5,7,6),(0,1,5,4),(2,6,7,3),(0,4,6,2),(1,3,7,5))
triangles=[t for a,b,c,d in faces for t in ((a,b,c),(a,c,d))]
result=remesh([(points,triangles)],16,0,"python")
assert result.backend=="Python" and result.quads
assert all(len(q)==4 for q in result.quads)
print("standalone scalar solve passed")
"""
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(ROOT / "src")], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_foreign_core_requires_restart(self):
        module = types.SimpleNamespace(__file__="/opt/other/MESH_DynaMesh_Core/__init__.py")
        with patch.dict(sys.modules, {"MESH_DynaMesh_Core": module}):
            with self.assertRaisesRegex(RuntimeError, "restart Maya"):
                maya_volume_remesh._check_source()

    def test_install_is_idempotent_and_registers_only_one_module(self):
        with tempfile.TemporaryDirectory(prefix="mvr-module-install-") as folder:
            target = installer.install(folder)
            payload = target.read_text()
            self.assertIn("+ maya_volume_remesh 0.1.0 " + str(ROOT), payload)
            self.assertIn("scripts: src", payload)
            self.assertIn("PYTHONPATH+:=src", payload)
            modified = target.stat().st_mtime_ns
            self.assertEqual(installer.install(folder), target)
            self.assertEqual(target.stat().st_mtime_ns, modified)
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["maya_volume_remesh.mod"])

    def test_install_protects_an_existing_module_file(self):
        with tempfile.TemporaryDirectory(prefix="mvr-module-existing-") as folder:
            target = Path(folder) / "maya_volume_remesh.mod"
            target.write_text("# user configuration\n")
            with self.assertRaisesRegex(RuntimeError, "Refusing"):
                installer.install(folder)
            self.assertEqual(target.read_text(), "# user configuration\n")

    def test_module_directory_honors_maya_app_dir(self):
        with patch.dict(os.environ, {"MAYA_APP_DIR": "/tmp/mvr-custom-prefs"}):
            self.assertEqual(installer.default_module_directory(), Path("/tmp/mvr-custom-prefs/modules"))

    def test_distribution_has_no_private_imports_or_capture_paths(self):
        for file in (ROOT / "src").rglob("*"):
            if file.is_file() and file.suffix in (".py", ".cpp", ".h", ".cu", ".sh"):
                text = file.read_text()
                for forbidden in (
                    "from brian_tools",
                    "import brian_tools",
                    "/Users/",
                    "/net/homedirs",
                    "/spfs/",
                    "docs/vault",
                ):
                    self.assertNotIn(forbidden, text, str(file.relative_to(ROOT)))


if __name__ == "__main__":
    unittest.main()
