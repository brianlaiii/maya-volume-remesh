"""Fallback selection and durable install safety without Maya."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from maya_volume_remesh._native.dual_backend import ensure_backend, runtime_version


class LoaderTests(unittest.TestCase):
    def setUp(self):
        gate = patch("maya_volume_remesh._native.fallback.require_python_fallback")
        gate.start()
        self.addCleanup(gate.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.sources = {name: root / name for name in ("node.py", "solver.py")}
        for name, path in self.sources.items():
            path.write_text("# " + name)
        self.directory = root / "installed"
        self.cmds = Mock()
        self.cmds.pluginInfo.return_value = []
        self.cmds.allNodeTypes.return_value = []
        self.native = Mock(return_value="native.bundle")
        self.env = patch.dict(os.environ, {"TEST_NODE_DIR": str(self.directory), "TEST_NODE_FORCE": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cmds.loadPlugin.side_effect = lambda *a, **k: setattr(self.cmds.allNodeTypes, "return_value", ["testNode"])

    def load(self):
        return ensure_backend(
            self.cmds,
            "testNode",
            self.native,
            self.sources,
            "1",
            "TEST_NODE_DIR",
            "TEST_NODE_FORCE",
            confirm_fallback=True,
        )

    def owner(self, version, path="loaded.py"):
        def info(plugin=None, **kw):
            if kw.get("listPlugins"):
                return ["owner"]
            if kw.get("dependNode"):
                return ["testNode"]
            if kw.get("version"):
                return version
            if kw.get("path"):
                return path

        self.cmds.pluginInfo.side_effect = info

    def test_native_preferred_without_python_install(self):
        self.assertEqual(self.load(), "native.bundle")
        self.assertFalse(self.directory.exists())

    def test_compiler_failure_installs_independent_runtime(self):
        self.native.side_effect = subprocess.CalledProcessError(1, ["compiler"])
        self.assertEqual(self.load(), str(self.directory / "node.py"))
        for name, path in self.sources.items():
            self.assertEqual((self.directory / name).read_bytes(), path.read_bytes())
        self.cmds.warning.assert_called_once()
        self.assertTrue((self.directory / "node.py.brian-tools.json").is_file())

    def test_force_skips_native_and_protects_edited_runtime(self):
        os.environ["TEST_NODE_FORCE"] = "1"
        self.load()
        self.native.assert_not_called()
        (self.directory / "solver.py").write_text("# user edit")
        with self.assertRaisesRegex(RuntimeError, "unowned or edited"):
            self.load()
        self.assertEqual((self.directory / "solver.py").read_text(), "# user edit")

    def test_matching_loaded_python_is_reused(self):
        self.owner(runtime_version("1", self.sources.values()))
        self.assertEqual(self.load(), "loaded.py")
        self.native.assert_not_called()
        self.cmds.loadPlugin.assert_not_called()

    def test_changed_live_runtime_requires_restart(self):
        self.owner("1+py.old")
        with self.assertRaisesRegex(RuntimeError, "restart Maya"):
            self.load()
        self.cmds.loadPlugin.assert_not_called()

    def test_force_does_not_replace_a_live_native_type(self):
        self.owner("1", "native.bundle")
        os.environ["TEST_NODE_FORCE"] = "1"
        with self.assertRaisesRegex(RuntimeError, "Restart Maya"):
            self.load()
        self.native.assert_not_called()
        self.cmds.loadPlugin.assert_not_called()

    def test_failed_native_registration_is_not_overwritten(self):
        self.native.side_effect = RuntimeError("incomplete load")
        self.cmds.allNodeTypes.return_value = ["testNode"]
        with self.assertRaisesRegex(RuntimeError, "incomplete load"):
            self.load()
        self.cmds.loadPlugin.assert_not_called()

    def test_authorized_automatic_fallback_skips_confirmation(self):
        self.native.side_effect = RuntimeError("compiler unavailable")
        with patch(
            "maya_volume_remesh._native.fallback.require_python_fallback", side_effect=AssertionError("dialog opened")
        ):
            result = ensure_backend(
                self.cmds,
                "testNode",
                self.native,
                self.sources,
                "1",
                "TEST_NODE_DIR",
                "TEST_NODE_FORCE",
                confirm_fallback=False,
            )
        self.assertEqual(result, str(self.directory / "node.py"))
        self.cmds.warning.assert_called_once()

    def test_default_fallback_still_requires_confirmation(self):
        self.native.side_effect = RuntimeError("compiler unavailable")
        with patch(
            "maya_volume_remesh._native.fallback.require_python_fallback", side_effect=RuntimeError("cancelled")
        ) as gate:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                self.load()
            gate.assert_called_once()
        self.cmds.loadPlugin.assert_not_called()


if __name__ == "__main__":
    unittest.main()
