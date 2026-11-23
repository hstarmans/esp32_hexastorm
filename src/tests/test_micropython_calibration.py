import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import build
from control._paths import get_repo_root, resolve_hexastorm_src


def test_micropython_calibration_loop_with_ulab():
    """Verify full calibration loop executes on real MicroPython v1.28.0 + ulab."""
    repo_root = get_repo_root()
    test_script = repo_root / "src" / "tests" / "mpy_test_calibration.py"
    assert test_script.is_file()

    mpy_bin = os.environ.get("MICROPYTHON_BIN", "").strip()
    if mpy_bin:
        mpy_path = Path(mpy_bin).expanduser()
        if not (mpy_path.is_file() and os.access(mpy_path, os.X_OK)):
            pytest.fail(f"MICROPYTHON_BIN is not executable: {mpy_path}")
        env = os.environ.copy()
        env.setdefault("MPY_WORKSPACE", str(repo_root))
        env.setdefault("MPY_HEXASTORM_SRC", str(resolve_hexastorm_src(repo_root)))
        proc = subprocess.run(
            [str(mpy_path), "-X", "heapsize=8M", str(test_script)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    else:
        if shutil.which("docker") is None:
            pytest.skip("Neither MICROPYTHON_BIN nor docker executable is available")

        image_name = os.environ.get("MPY_DOCKER_IMAGE", build.DEFAULT_DOCKER_IMAGE)
        inspect = subprocess.run(
            ["docker", "image", "inspect", image_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if inspect.returncode != 0:
            pytest.skip(f"Docker builder image not available: {image_name}")

        cmd = [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/workspace/docker/run-mpy-tests.sh",
            "-v",
            f"{repo_root}:/workspace",
        ]
        hs_raw = os.environ.get("HEXASTORM_DIR", "").strip()
        if hs_raw:
            abs_hs = str(Path(hs_raw).expanduser().resolve())
            cmd.extend(
                [
                    "-v",
                    f"{abs_hs}:/hexastorm_override:ro",
                    "-e",
                    "HEXASTORM_DIR=/hexastorm_override",
                ]
            )
        if sys.platform != "darwin" and hasattr(os, "getuid") and hasattr(os, "getgid"):
            cmd.extend(
                ["-e", f"HOST_UID={os.getuid()}", "-e", f"HOST_GID={os.getgid()}"]
            )
        cmd.append(image_name)

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

    assert proc.returncode == 0, (
        f"MicroPython calibration test failed (exit={proc.returncode}):\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert "MICROPYTHON CALIBRATION SUITE PASSED" in proc.stdout, (
        f"Missing pass marker in stdout:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
