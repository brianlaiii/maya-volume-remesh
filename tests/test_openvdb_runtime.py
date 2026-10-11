"""Ownership and restart protection for the optional OpenVDB runtime."""

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from MESH_DynaMesh_Core import runtime, vdb_build


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mvr-vdb-install-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.source = self.folder / "prebuilt.so"
        self.source.write_bytes(b"test-runtime")
        self.destination = self.folder / "runtime"
        self.destination.mkdir()
        self.cmds = types.SimpleNamespace(allNodeTypes=lambda: [])
        patches = [
            patch.dict(
                os.environ, {runtime.PLUGIN_DIR_ENV: str(self.destination), "MAYA_VOLUME_REMESH_OPENVDB": "auto"}
            ),
            patch.object(runtime, "_vdb_attempted", False),
            patch.object(runtime, "_vdb_ready", False),
            patch.object(vdb_build, "build", return_value=self.source),
            patch.object(vdb_build.kernel, "preload"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_install_is_immutable_and_idempotent(self):
        runtime._openvdb(self.cmds)
        manifest = self.destination / "btDynaMeshVdb.path"
        target = self.destination / manifest.read_text().strip()
        self.assertEqual(target.read_bytes(), self.source.read_bytes())
        before = target.stat().st_mtime_ns
        runtime._openvdb(self.cmds)
        self.assertEqual(target.stat().st_mtime_ns, before)
        self.assertTrue((self.destination / "btDynaMeshVdb.path.mvr.json").is_file())

    def test_edited_binary_is_not_overwritten(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()[:20]
        target = self.destination / ("mvr_openvdb_" + digest + ".so")
        target.write_bytes(b"user-edit")
        with self.assertRaisesRegex(RuntimeError, "edited OpenVDB runtime"):
            runtime._openvdb(self.cmds)
        self.assertEqual(target.read_bytes(), b"user-edit")

    def test_unowned_manifest_is_not_overwritten(self):
        manifest = self.destination / "btDynaMeshVdb.path"
        manifest.write_text("user-custom.so\n")
        with self.assertRaisesRegex(RuntimeError, "unowned or edited"):
            runtime._openvdb(self.cmds)
        self.assertEqual(manifest.read_text(), "user-custom.so\n")

    def test_changed_active_runtime_requires_restart(self):
        manifest = self.destination / "btDynaMeshVdb.path"
        manifest.write_text("old-runtime.so\n")
        self.cmds.allNodeTypes = lambda: ["btDynaMesh"]
        with self.assertRaisesRegex(RuntimeError, "restart Maya"):
            runtime._openvdb(self.cmds)
        self.assertEqual(manifest.read_text(), "old-runtime.so\n")

    def test_disabled_openvdb_does_not_build_or_install(self):
        with (
            patch.dict(os.environ, MAYA_VOLUME_REMESH_OPENVDB="off"),
            patch.object(vdb_build.kernel, "preload") as build,
        ):
            runtime._openvdb(self.cmds)
        build.assert_not_called()
        self.assertEqual(list(self.destination.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
