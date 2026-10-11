"""Validate this standalone checkout in an offline Linux container."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    return {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in sorted(set(names.decode().split("\0")) - {""})
        if (ROOT / name).is_file()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("test",), nargs="?", default="test")
    parser.add_argument("--image", required=True, help="Existing image with Python 3.10+, C++17, OpenVDB and TBB SDKs.")
    parser.add_argument("--context", default="desktop-linux" if sys.platform == "darwin" else "default")
    parser.add_argument("--python", default="python3")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.report and args.report.resolve().is_relative_to(ROOT):
        parser.error("Keep reports outside the source checkout.")
    docker = shutil.which("docker")
    if not docker:
        parser.error("Docker is unavailable.")
    command = [docker, "--context", args.context]
    inspected = subprocess.run(
        command + ["image", "inspect", args.image, "--format", "{{.Id}}"], capture_output=True, text=True
    )
    if inspected.returncode:
        parser.error("The requested Linux image is missing. Build or provision it first.")
    hashes = inputs()
    with tempfile.TemporaryDirectory(prefix="mvr-linux-input-") as folder:
        staged = Path(folder)
        for name in hashes:
            source = ROOT / name
            if source.is_symlink():
                raise RuntimeError("Unexpected source symlink: " + name)
            if source.suffix in (".so", ".dylib", ".bundle", ".mll", ".dll", ".pyc"):
                raise RuntimeError("Compiled runtime must not be a source input: " + name)
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        run = command + [
            "run",
            "--rm",
            "--pull",
            "never",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--read-only",
            "--mount",
            "type=bind,source=" + str(staged) + ",target=/snapshot,readonly",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,size=2g,mode=1777",
            "--entrypoint",
            args.python,
            inspected.stdout.strip(),
            "/snapshot/tests/linux_compat/run_in_container.py",
        ]
        with tempfile.NamedTemporaryFile(prefix="mvr-linux-compat-", suffix=".log", delete=False) as output:
            log = Path(output.name)
        print("Linux compatibility log: " + str(log), flush=True)
        report = None
        with (
            log.open("w") as output,
            subprocess.Popen(run, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process,
        ):
            for line in process.stdout:
                output.write(line)
                output.flush()
                if line.startswith("MVR_LINUX_RESULT "):
                    report = json.loads(line.split(" ", 1)[1])
                else:
                    print(line, end="", flush=True)
            code = process.wait()
    if report is None:
        print("Missing completed Linux report. Read " + str(log), file=sys.stderr)
        return code or 1
    changed = inputs() != hashes
    report.update(
        input_sha256=hashes, changed_inputs_during_run=changed, image_id=inspected.stdout.strip(), log=str(log)
    )
    if changed:
        report["success"] = False
        print("Checkout inputs changed during the run.", file=sys.stderr)
    path = args.report or log.with_suffix(".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("Linux compatibility report: " + str(path), flush=True)
    return code or (0 if report["success"] else 1)


if __name__ == "__main__":
    sys.exit(main())
