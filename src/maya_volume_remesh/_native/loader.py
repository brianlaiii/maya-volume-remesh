"""Build and load a versioned Maya command plugin."""

import os
import subprocess
import sys


DEFAULT_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def shared_build_driver_path(tool_root):
    """Return the shared Maya plugin build driver used by a tool wrapper."""
    source_root = os.path.dirname(os.path.abspath(tool_root))
    return os.path.join(
        source_root,
        "maya_volume_remesh",
        "_native",
        "build_maya_plugin.sh",
    )


def run_streamed_subprocess(command, popen_process=None, **process_options):
    """Run a command, print each output line, and keep the full result."""
    options = dict(process_options)
    options.setdefault("stdout", subprocess.PIPE)
    options.setdefault("stderr", subprocess.STDOUT)
    options.setdefault("text", True)
    options.setdefault("bufsize", 1)
    popen = subprocess.Popen if popen_process is None else popen_process
    process = popen(command, **options)
    output_lines = []
    try:
        for line in process.stdout:
            output_lines.append(line)
            print(line, end="", flush=True)
    finally:
        process.stdout.close()
        return_code = process.wait()
    return subprocess.CompletedProcess(
        command,
        return_code,
        stdout="".join(output_lines),
    )


class NativePluginConfig:
    """Names and paths that identify one native Maya command plugin."""

    def __init__(
        self,
        command_name,
        tool_dir_name,
        source_name,
        build_script_name,
        tool_label,
        entry_file,
        fallback_source_root=DEFAULT_SOURCE_ROOT,
    ):
        self.command_name = command_name
        self.tool_dir_name = tool_dir_name
        self.source_name = source_name
        self.build_script_name = build_script_name
        self.tool_label = tool_label
        self.entry_file = entry_file
        self.fallback_source_root = fallback_source_root


class NativePluginLoader:
    """Resolve, rebuild, and load one configured Maya command plugin."""

    def __init__(
        self,
        config,
        maya_cmds,
        maya_messages,
        environ=None,
        platform=None,
        run_process=None,
        popen_process=None,
    ):
        self.config = config
        self.cmds = maya_cmds
        self.messages = maya_messages
        self.environ = os.environ if environ is None else environ
        self.platform = sys.platform if platform is None else platform
        self.run_process = run_process
        self.popen_process = subprocess.Popen if popen_process is None else popen_process

    def tool_root(self):
        """Return the first support folder containing the configured source."""
        here = os.path.dirname(os.path.abspath(self.config.entry_file))
        candidates = [
            os.path.join(here, self.config.tool_dir_name),
            os.path.join(
                self.config.fallback_source_root,
                self.config.tool_dir_name,
            ),
        ]
        for path in candidates:
            if os.path.exists(os.path.join(path, self.config.source_name)):
                return path
        return candidates[0]

    def maya_version(self):
        """Return Maya's major version as a string."""
        version = str(self.cmds.about(version=True))
        token = version.split()[0]
        return token.split(".")[0]

    def maya_plugin_suffix(self):
        """Return Maya's plugin suffix for the active platform."""
        if self.platform == "win32":
            return ".mll"
        if self.platform == "darwin":
            return ".bundle"
        return ".so"

    def maya_root(self):
        """Find the Maya SDK root with the legacy search order."""
        env_path = self.environ.get("MAYA_LOCATION", "")
        if env_path:
            path = os.path.normpath(env_path)
            if path.endswith(os.path.join("Maya.app", "Contents", "MacOS")):
                return os.path.dirname(os.path.dirname(os.path.dirname(path)))
            if path.endswith(os.path.join("Maya.app", "Contents")):
                return os.path.dirname(os.path.dirname(path))
            if path.endswith("Maya.app"):
                return os.path.dirname(path)
            if os.path.isdir(os.path.join(path, "include", "maya")):
                return path

        version = self.maya_version()
        candidates = [
            "/Applications/Autodesk/maya{}".format(version),
            "/usr/autodesk/maya{}".format(version),
            "/opt/Autodesk/maya{}".format(version),
            "/opt/maya",
        ]
        for candidate in candidates:
            if os.path.isdir(os.path.join(candidate, "include", "maya")):
                return candidate

        raise RuntimeError("Could not find the Maya SDK folder.")

    def plugin_path(self):
        """Return the versioned plugin path expected by the entry point."""
        return os.path.join(
            self.tool_root(),
            "plug-ins",
            "maya{}".format(self.maya_version()),
            "{}{}".format(
                self.config.command_name,
                self.maya_plugin_suffix(),
            ),
        )

    def source_path(self):
        """Return the configured C++ source path."""
        return os.path.join(self.tool_root(), self.config.source_name)

    def build_script_path(self):
        """Return the configured build script path."""
        return os.path.join(self.tool_root(), self.config.build_script_name)

    def needs_build(self, plugin_path):
        """Return whether the plugin is missing or older than build inputs."""
        if not os.path.exists(plugin_path):
            return True
        build_inputs = [self.source_path(), self.build_script_path()]
        shared_driver = shared_build_driver_path(self.tool_root())
        if os.path.exists(shared_driver):
            build_inputs.append(shared_driver)
        source_mtime = max(os.path.getmtime(path) for path in build_inputs)
        return os.path.getmtime(plugin_path) < source_mtime

    def build_plugin(self, plugin_path):
        """Run the configured build script and return its plugin output."""
        build_script = self.build_script_path()
        if not os.path.exists(build_script):
            raise RuntimeError("Build script is missing: {}".format(build_script))

        self.messages.displayInfo("Building {} C++ plugin.".format(self.config.tool_label))
        command = [build_script, self.maya_root(), self.maya_version()]
        process_options = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "cwd": self.tool_root(),
        }
        if self.run_process is not None:
            result = self.run_process(command, **process_options)
            build_output = result.stdout or ""
            if build_output:
                print(build_output, end="", flush=True)
            return_code = result.returncode
        else:
            result = run_streamed_subprocess(
                command,
                popen_process=self.popen_process,
                **process_options,
            )
            return_code = result.returncode
            build_output = result.stdout or ""

        if return_code != 0:
            raise RuntimeError("C++ plugin build failed:\n{}".format(build_output))
        built_paths = [line.strip() for line in build_output.splitlines() if os.path.exists(line.strip())]
        if built_paths:
            return built_paths[-1]
        if not os.path.exists(plugin_path):
            raise RuntimeError(
                "C++ plugin build finished, but the plugin is missing.\nExpected: {}\nBuild output:\n{}".format(
                    plugin_path, build_output
                )
            )
        return plugin_path
