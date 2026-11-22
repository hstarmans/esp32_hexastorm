import os
import re
import sys

import build
import pytest
from control import _paths
from control._paths import get_repo_root, resolve_hexastorm_src
from hexastorm.config import PlatformConfig


def test_vendor_separation_and_no_src_symlinks():
    """Verify third-party libs live in vendor/ and legacy symlinks/install.sh are gone."""
    root = get_repo_root()
    vendor_pkgs = ("microdot", "utemplate", "ota", "mrequests", "winbond", "tmc")

    for pkg in vendor_pkgs:
        assert (root / "vendor" / pkg).is_dir(), f"Missing vendor/{pkg}"
        assert not (root / "src" / pkg).exists(), f"Unexpected src/{pkg}"

    assert (root / "vendor" / "VERSIONS.md").is_file()

    for removed in (
        root / "src" / "hexastorm",
        root / "src" / "micropython-ulab",
        root / "src" / "micropython-camera-API",
        root / "install.sh",
    ):
        assert not removed.exists() and not removed.is_symlink(), (
            f"Legacy path still exists: {removed}"
        )


def test_submodules_and_micropython_cmake_paths():
    """Verify .gitmodules, deps/ checkouts, and src/micropython.cmake include paths."""
    root = get_repo_root()
    gitmodules = root / ".gitmodules"
    assert gitmodules.is_file()
    gitmodules_text = gitmodules.read_text()

    for sub in (
        "deps/micropython-ulab",
        "deps/micropython-camera-API",
        "deps/hexastorm",
    ):
        assert sub in gitmodules_text

    assert (root / "deps" / "micropython-ulab" / "code" / "micropython.cmake").is_file()
    assert (root / "deps" / "micropython-camera-API" / "micropython.cmake").is_file()
    assert (root / "deps" / "micropython-camera-API" / "src" / "acamera.py").is_file()

    cmake_file = root / "src" / "micropython.cmake"
    cmake_text = cmake_file.read_text()
    includes = re.findall(r"include\(\$\{CMAKE_CURRENT_LIST_DIR\}/([^)]+)\)", cmake_text)
    assert len(includes) == 2
    for rel_path in includes:
        target = (cmake_file.parent / rel_path).resolve()
        assert target.is_file(), f"Missing cmake include target: {target}"


def test_out_of_tree_board_matches_platform_config():
    """Verify boards/HEXASTORM_S3 exists and camera/I2C macros match PlatformConfig."""
    root = get_repo_root()
    board_dir = root / "boards" / "HEXASTORM_S3"

    expected_files = (
        board_dir / "mpconfigboard.h",
        board_dir / "mpconfigboard.cmake",
        board_dir / "sdkconfig.board.in",
        board_dir / "partitions-32MiB-ota.csv",
        board_dir / "components" / "hexastorm_camera_dep" / "CMakeLists.txt",
        board_dir / "components" / "hexastorm_camera_dep" / "idf_component.yml",
    )
    for path in expected_files:
        assert path.is_file(), f"Missing board file: {path}"

    header_text = (board_dir / "mpconfigboard.h").read_text()
    defines = {
        name: int(val)
        for name, val in re.findall(
            r"#define\s+([A-Z0-9_]+)\s+\((-?\d+)\)", header_text
        )
    }

    esp_cfg = PlatformConfig(test=False).esp32_cfg
    cam_cfg = esp_cfg["camera"]
    i2c_cfg = esp_cfg["i2c"]

    assert defines["MICROPY_HW_I2C0_SCL"] == i2c_cfg["scl"]
    assert defines["MICROPY_HW_I2C0_SDA"] == i2c_cfg["sda"]
    assert defines["MICROPY_CAMERA_PIN_SIOD"] == i2c_cfg["sda"]
    assert defines["MICROPY_CAMERA_PIN_SIOC"] == i2c_cfg["scl"]
    assert defines["MICROPY_CAMERA_XCLK_FREQ"] == cam_cfg["xclk_freq"]
    assert defines["MICROPY_CAMERA_XCLK_FREQ"] == 10_000_000

    for idx in range(8):
        assert defines[f"MICROPY_CAMERA_PIN_D{idx}"] == cam_cfg[f"d{idx}"]
    for key in ("pclk", "vsync", "href", "xclk", "pwdn", "reset"):
        assert defines[f"MICROPY_CAMERA_PIN_{key.upper()}"] == cam_cfg[key]


def test_resolve_hexastorm_src_priority(tmp_path, monkeypatch):
    """Verify HEXASTORM_DIR override, deps/ preference, sibling fallback, and error case."""
    monkeypatch.delenv("HEXASTORM_DIR", raising=False)

    fake_parent = tmp_path / "workspace"
    repo_root = fake_parent / "esp32_hexastorm"
    sub_src = repo_root / "deps" / "hexastorm" / "src"
    sib_src = fake_parent / "hexastorm" / "src"

    (sub_src / "hexastorm").mkdir(parents=True)
    (sub_src / "hexastorm" / "__init__.py").write_text("")
    (sib_src / "hexastorm" / "calibration").mkdir(parents=True)
    (sib_src / "hexastorm" / "__init__.py").write_text("")
    (sib_src / "hexastorm" / "calibration" / "core.py").write_text("")

    # Submodule lacks calibration/core.py -> sibling with core.py is preferred
    assert resolve_hexastorm_src(repo_root) == sib_src

    # Once submodule also has calibration/core.py, submodule takes priority
    (sub_src / "hexastorm" / "calibration").mkdir(parents=True)
    (sub_src / "hexastorm" / "calibration" / "core.py").write_text("")
    assert resolve_hexastorm_src(repo_root) == sub_src

    # Explicit HEXASTORM_DIR env var overrides both (accepts repo root or src dir)
    custom_repo = tmp_path / "custom_hs"
    custom_src = custom_repo / "src"
    (custom_src / "hexastorm").mkdir(parents=True)
    (custom_src / "hexastorm" / "__init__.py").write_text("")
    monkeypatch.setenv("HEXASTORM_DIR", str(custom_repo))
    assert resolve_hexastorm_src(repo_root) == custom_src.resolve()

    monkeypatch.setenv("HEXASTORM_DIR", str(custom_src))
    assert resolve_hexastorm_src(repo_root) == custom_src.resolve()

    monkeypatch.setenv("HEXASTORM_DIR", str(tmp_path / "nonexistent"))
    with pytest.raises(FileNotFoundError, match="HEXASTORM_DIR"):
        resolve_hexastorm_src(repo_root)

    monkeypatch.delenv("HEXASTORM_DIR", raising=False)
    empty_root = tmp_path / "isolated" / "esp32_hexastorm"
    empty_root.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="git submodule update"):
        resolve_hexastorm_src(empty_root)


def test_build_resolve_paths_respects_env_and_cli(tmp_path, monkeypatch):
    """Verify build.resolve_build_paths uses MICROPYTHON_DIR / IDF_PATH env vars and CLI args."""
    mp_env = tmp_path / "mp_env"
    idf_env = tmp_path / "idf_env"
    monkeypatch.setenv("MICROPYTHON_DIR", str(mp_env))
    monkeypatch.setenv("IDF_PATH", str(idf_env))

    paths_env = build.resolve_build_paths()
    assert paths_env["board_name"] == "HEXASTORM_S3"
    assert paths_env["board_dir"] == os.path.join(
        str(get_repo_root()), "boards", "HEXASTORM_S3"
    )
    assert paths_env["esp32_port_dir"] == os.path.join(str(mp_env), "ports", "esp32")
    assert paths_env["idf_export_path"] == os.path.join(str(idf_env), "export.sh")
    assert paths_env["local_build_dir"] == os.path.join(
        str(mp_env), "ports", "esp32", "build-HEXASTORM_S3"
    )

    mp_cli_port = tmp_path / "mp_cli" / "ports" / "esp32"
    idf_cli = tmp_path / "idf_cli"
    paths_cli = build.resolve_build_paths(
        micropython_dir=str(mp_cli_port),
        idf_path=str(idf_cli),
    )
    assert paths_cli["esp32_port_dir"] == str(mp_cli_port)
    assert paths_cli["idf_export_path"] == os.path.join(str(idf_cli), "export.sh")
    assert paths_cli["local_build_dir"] == os.path.join(
        str(mp_cli_port), "build-HEXASTORM_S3"
    )


def test_docker_and_ci_files_exist_and_align():
    """Verify Dockerfile, .dockerignore, build-firmware.sh, and GitHub CI workflow."""
    root = get_repo_root()

    dockerfile = root / "Dockerfile"
    dockerignore = root / ".dockerignore"
    script = root / "docker" / "build-firmware.sh"
    ci_yml = root / ".github" / "workflows" / "ci.yml"

    assert dockerfile.is_file()
    assert dockerignore.is_file()
    assert script.is_file()
    assert os.access(script, os.X_OK), "docker/build-firmware.sh must be executable"
    assert ci_yml.is_file()

    df_text = dockerfile.read_text()
    for token in ("espressif/idf:v5.5.2", "v1.28.0", "freezefs", "pyclean"):
        assert token in df_text

    script_text = script.read_text()
    for token in ("HEXASTORM_S3", "src/manifest.py", "src/micropython.cmake"):
        assert token in script_text

    ci_text = ci_yml.read_text()
    for token in ("submodules: recursive", "micropython.bin", "uv run pytest"):
        assert token in ci_text


def test_docker_build_command_and_local_flash_command(tmp_path, monkeypatch):
    """Verify Docker run invocation, build/HEXASTORM_S3 path, and host esptool flash args."""
    docker_paths = build.resolve_build_paths(use_docker=True)
    assert docker_paths["local_build_dir"] == os.path.join(
        str(get_repo_root()), "build", "HEXASTORM_S3"
    )

    hs_override = tmp_path / "custom_hexastorm"
    hs_override.mkdir()
    monkeypatch.delenv("HEXASTORM_DIR", raising=False)
    cmd = build.build_docker_run_command(
        docker_paths,
        image_name="custom-builder:test",
        hexastorm_dir=str(hs_override),
        host_uid=1000,
        host_gid=1000,
    )
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert f"{docker_paths['repo_root']}:/workspace" in cmd
    assert f"{build.DOCKER_CACHE_VOLUME}:/root/.cache" in cmd
    assert f"{hs_override.resolve()}:/hexastorm_override:ro" in cmd
    assert "HEXASTORM_DIR=/hexastorm_override" in cmd
    assert "HOST_UID=1000" in cmd
    assert "HOST_GID=1000" in cmd
    assert cmd[-1] == "custom-builder:test"

    flash_cmd = build.build_local_flash_command("/dev/ttyACM0", str(tmp_path))
    assert flash_cmd[:3] == [sys.executable, "-m", "esptool"]
    assert "/dev/ttyACM0" in flash_cmd
    for offset, rel_bin in (
        ("0x0", os.path.join("bootloader", "bootloader.bin")),
        ("0x10000", "micropython.bin"),
        ("0x8000", os.path.join("partition_table", "partition-table.bin")),
        ("0xd000", "ota_data_initial.bin"),
    ):
        idx = flash_cmd.index(offset)
        assert flash_cmd[idx + 1] == os.path.join(str(tmp_path), rel_bin)


def test_manifest_python_module_cmd_fallback(monkeypatch):
    """Verify python_module_cmd runs via sys.executable when installed and falls back to uv."""
    monkeypatch.setattr(_paths, "find_spec", lambda name: object())
    assert _paths.python_module_cmd("freezefs") == [sys.executable, "-m", "freezefs"]

    monkeypatch.setattr(_paths, "find_spec", lambda name: None)
    assert _paths.python_module_cmd("freezefs") == [
        "uv",
        "run",
        "python",
        "-m",
        "freezefs",
    ]
