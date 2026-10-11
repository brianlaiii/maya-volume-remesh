"""Run native inventory and all mandatory standalone host suites from disposable storage."""

import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

SNAPSHOT = Path(__file__).resolve().parents[2]


def main():
    inventory = json.loads((SNAPSHOT / "tests/linux_compat/native_inventory.json").read_text())
    actual = {
        str(p.relative_to(SNAPSHOT))
        for root in ("src", "tests")
        for p in (SNAPSHOT / root).rglob("*")
        if p.suffix in (".cpp", ".cu")
    }
    if actual != set(inventory["sources"]):
        raise RuntimeError("Native inventory does not match the source checkout.")
    mandatory = inventory["mandatory_suites"]
    declared = {suite for item in inventory["sources"].values() for suite in item.get("tests", [])}
    if not declared.issubset(set(mandatory)):
        raise RuntimeError("Native coverage points at a non-mandatory suite.")
    rows = []
    with tempfile.TemporaryDirectory(prefix="mvr-linux-tests-") as folder:
        root = Path(folder) / "source"
        shutil.copytree(SNAPSHOT, root)
        env = dict(os.environ, MVR_REQUIRE_OPENVDB="1", MAYA_VOLUME_REMESH_CACHE_DIR=str(Path(folder) / "native-cache"))
        for suite in mandatory:
            result = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", suite + ".py", "-v"],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=360,
            )
            output = result.stdout + result.stderr
            print(output, end="", flush=True)
            matched = re.search(r"Ran (\d+) tests? in", output)
            count = int(matched.group(1)) if matched else 0
            skipped = re.search(r"skipped=(\d+)", output)
            passed = result.returncode == 0 and count > 0 and not skipped
            rows.append({"suite": suite, "tests": count, "passed": passed, "returncode": result.returncode})
    report = {
        "success": all(row["passed"] for row in rows),
        "platform": platform.platform(),
        "python": sys.version,
        "suites": rows,
        "unavailable": {
            name: item["unavailable"] for name, item in inventory["sources"].items() if "unavailable" in item
        },
    }
    print("MVR_LINUX_RESULT " + json.dumps(report), flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
