"""Select a native node or a durable Python fallback without replacing live types."""

import hashlib
import json
import os
from pathlib import Path
import tempfile
import subprocess


def runtime_version(version, files):
    digest = hashlib.sha256()
    for source in files:
        digest.update(Path(source).read_bytes())
    return version + "+py." + digest.hexdigest()


def ensure_backend(cmds, node_type, native_load, files, version, plugin_dir_env, force_env, confirm_fallback=False):
    """Both backends register the same schema/ID; choose once per Maya session.

    files maps deployed filenames to sources, with the plugin first. Sources are
    installed beside an ownership manifest so Maya can reopen scenes directly.
    """
    expected = runtime_version(version, files.values())
    force = os.environ.get(force_env) == "1"
    for plugin in cmds.pluginInfo(query=True, listPlugins=True) or []:
        if node_type not in (cmds.pluginInfo(plugin, query=True, dependNode=True) or []):
            continue
        actual = cmds.pluginInfo(plugin, query=True, version=True)
        python = "+py." in actual
        if python:
            if actual != expected:
                raise RuntimeError(node_type + ": Python runtime changed. Save and restart Maya.")
            if not force and confirm_fallback:
                from .fallback import require_python_fallback

                require_python_fallback(node_type)
            return cmds.pluginInfo(plugin, query=True, path=True)
        if force:
            raise RuntimeError(node_type + ": C++ is already loaded. Restart Maya to force Python.")
        # Let the native loader enforce its existing stale-build checks. Never
        # catch its error and load a conflicting registration over a live type.
        return native_load()
    if not force:
        try:
            return native_load()
        except (RuntimeError, OSError, subprocess.SubprocessError) as error:
            if node_type in (cmds.allNodeTypes() or []):
                raise
            if confirm_fallback:
                from maya_volume_remesh._native.fallback import require_python_fallback

                require_python_fallback(node_type, error)
            cmds.warning(node_type + ": C++ unavailable; using Python. " + str(error))
    directory = Path(
        os.environ.get(plugin_dir_env)
        or os.path.join(cmds.internalVar(userAppDir=True), str(cmds.about(majorVersion=True)), "plug-ins")
    )
    directory.mkdir(parents=True, exist_ok=True)
    plugin_name = next(iter(files))
    marker = directory / (plugin_name + ".brian-tools.json")
    previous = json.loads(marker.read_text()) if marker.exists() else {}
    payload = {name: Path(path).read_bytes() for name, path in files.items()}
    for name, data in payload.items():
        dest = directory / name
        if dest.exists() and dest.read_bytes() != data:
            if hashlib.sha256(dest.read_bytes()).hexdigest() != previous.get(name):
                raise RuntimeError("Refusing to overwrite an unowned or edited Python runtime: " + str(dest))
    for name, data in payload.items():
        fd, temp = tempfile.mkstemp(prefix=name + ".", dir=str(directory))
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.replace(temp, directory / name)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    marker.write_text(json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}, indent=2))
    cmds.loadPlugin(str(directory / plugin_name), quiet=True)
    if node_type not in (cmds.allNodeTypes() or []):
        raise RuntimeError("Python fallback did not register " + node_type)
    return str(directory / plugin_name)


def load_native_node(cmds, node_type, config, plugin_dir_env):
    """Build a node and install its binary where saved scenes can find it."""
    from .loader import NativePluginLoader
    import maya.api.OpenMaya as om

    loader = NativePluginLoader(config, cmds, om.MGlobal)
    built = Path(loader.plugin_path())
    tool = Path(loader.source_path()).parent
    sources = [
        p for p in tool.rglob("*") if p.is_file() and p.suffix in (".cpp", ".h", ".cu") and "plug-ins" not in p.parts
    ]
    stale = loader.needs_build(str(built)) or (
        built.exists() and any(p.stat().st_mtime > built.stat().st_mtime for p in sources)
    )
    for plugin in cmds.pluginInfo(query=True, listPlugins=True) or []:
        if node_type not in (cmds.pluginInfo(plugin, query=True, dependNode=True) or []):
            continue
        loaded = Path(cmds.pluginInfo(plugin, query=True, path=True))
        if (
            stale
            or not loaded.is_file()
            or not built.is_file()
            or hashlib.sha256(loaded.read_bytes()).digest() != hashlib.sha256(built.read_bytes()).digest()
        ):
            raise RuntimeError(node_type + ": Native runtime changed. Save and restart Maya.")
        return str(loaded)
    if stale:
        built = Path(loader.build_plugin(str(built)))
    directory = Path(
        os.environ.get(plugin_dir_env)
        or os.path.join(cmds.internalVar(userAppDir=True), str(cmds.about(majorVersion=True)), "plug-ins")
    )
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / built.name
    marker = directory / (built.name + ".brian-tools.json")
    data = built.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if dest.exists() and dest.read_bytes() != data:
        previous = json.loads(marker.read_text()) if marker.exists() else {}
        if hashlib.sha256(dest.read_bytes()).hexdigest() != previous.get("sha256"):
            raise RuntimeError("Refusing to overwrite an unowned native runtime: " + str(dest))
    fd, temp = tempfile.mkstemp(prefix=built.name + ".", dir=str(directory))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.chmod(temp, 0o755)
        os.replace(temp, dest)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    marker.write_text(json.dumps({"sha256": digest}))
    cmds.loadPlugin(str(dest), quiet=True)
    if node_type not in (cmds.allNodeTypes() or []):
        raise RuntimeError("Native runtime did not register " + node_type)
    return str(dest)
