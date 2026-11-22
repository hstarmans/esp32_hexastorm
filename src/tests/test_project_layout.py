import importlib.util
import json
import os
import re
import sys
import types

import build
import control
import pytest
from control import _paths, bootlib, constants
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
        "deps/esp32-camera",
        "deps/hexastorm",
    ):
        assert sub in gitmodules_text

    assert (root / "deps" / "micropython-ulab" / "code" / "micropython.cmake").is_file()
    assert (root / "deps" / "micropython-camera-API" / "micropython.cmake").is_file()
    assert (root / "deps" / "micropython-camera-API" / "src" / "acamera.py").is_file()
    assert (root / "deps" / "esp32-camera" / "CMakeLists.txt").is_file()

    cmake_file = root / "src" / "micropython.cmake"
    cmake_text = cmake_file.read_text()
    includes = re.findall(r"include\(\$\{CMAKE_CURRENT_LIST_DIR\}/([^)]+)\)", cmake_text)
    assert len(includes) == 2
    for rel_path in includes:
        target = (cmake_file.parent / rel_path).resolve()
        assert target.is_file(), f"Missing cmake include target: {target}"

    assert "_HEXASTORM_SAVED_MANIFEST" in cmake_text
    assert "idf_component_get_property(esp32_camera_lib esp32-camera COMPONENT_LIB)" in cmake_text


def test_out_of_tree_board_matches_platform_config():
    """Verify boards/HEXASTORM_S3 exists and camera/I2C macros match PlatformConfig."""
    root = get_repo_root()
    board_dir = root / "boards" / "HEXASTORM_S3"

    expected_files = (
        board_dir / "mpconfigboard.h",
        board_dir / "mpconfigboard.cmake",
        board_dir / "sdkconfig.board.in",
        board_dir / "partitions-32MiB-ota.csv",
    )
    for path in expected_files:
        assert path.is_file(), f"Missing board file: {path}"

    board_cmake = (board_dir / "mpconfigboard.cmake").read_text()
    assert "deps/esp32-camera" in board_cmake
    assert "IDF_COMPONENTS esp32-camera" in board_cmake

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
    """Verify Dockerfile, .dockerignore, build scripts, and GitHub CI workflow."""
    root = get_repo_root()

    dockerfile = root / "Dockerfile"
    dockerignore = root / ".dockerignore"
    script = root / "docker" / "build-firmware.sh"
    build_fpga = root / "docker" / "build_fpga.py"
    ci_yml = root / ".github" / "workflows" / "ci.yml"

    assert dockerfile.is_file()
    assert dockerignore.is_file()
    assert script.is_file()
    assert os.access(script, os.X_OK), "docker/build-firmware.sh must be executable"
    assert build_fpga.is_file()
    assert os.access(build_fpga, os.X_OK), "docker/build_fpga.py must be executable"
    assert ci_yml.is_file()

    df_text = dockerfile.read_text()
    for token in (
        "espressif/idf:v5.5.2",
        "v1.28.0",
        "freezefs",
        "pyclean",
        "MICROPY_MPYCROSS",
        "FPGA_VENV",
        "amaranth",
        "yowasp-yosys",
        "yowasp-nextpnr-ice40",
        "build_fpga.py",
    ):
        assert token in df_text

    script_text = script.read_text()
    for token in (
        "HEXASTORM_S3",
        "src/manifest.py",
        "src/micropython.cmake",
        "MICROPY_MPYCROSS",
        "build_fpga.py",
    ):
        assert token in script_text

    ci_text = ci_yml.read_text()
    for token in (
        "submodules: recursive",
        "micropython.bin",
        "sd/fpga/fpga.bit",
        "uv run pytest",
    ):
        assert token in ci_text


def test_docker_build_command_and_local_flash_command(tmp_path, monkeypatch):
    """Verify Docker run invocation, build/HEXASTORM_S3 path, and host esptool flash args."""
    docker_paths = build.resolve_build_paths(use_docker=True)
    assert docker_paths["local_build_dir"] == os.path.join(
        str(get_repo_root()), "build", "HEXASTORM_S3"
    )

    hs_override = tmp_path / "custom_hexastorm"
    hs_override.mkdir()
    custom_bit = tmp_path / "custom_top.bin"
    custom_bit.write_bytes(b"\xff" * 64)
    monkeypatch.delenv("HEXASTORM_DIR", raising=False)
    monkeypatch.delenv("FPGA_BITSTREAM", raising=False)
    cmd = build.build_docker_run_command(
        docker_paths,
        image_name="custom-builder:test",
        hexastorm_dir=str(hs_override),
        host_uid=1000,
        host_gid=1000,
        fpga_bit=str(custom_bit),
        rebuild_fpga=True,
    )
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert f"{docker_paths['repo_root']}:/workspace" in cmd
    assert f"{build.DOCKER_CACHE_VOLUME}:/root/.cache" in cmd
    assert f"{hs_override.resolve()}:/hexastorm_override:ro" in cmd
    assert "HEXASTORM_DIR=/hexastorm_override" in cmd
    assert f"{custom_bit.resolve()}:/fpga_override.bit:ro" in cmd
    assert "FPGA_BITSTREAM=/fpga_override.bit" in cmd
    assert "REBUILD_FPGA=1" in cmd
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


def test_firmware_version_resolution_and_ota_comparison(tmp_path, monkeypatch):
    """Verify frozen FIRMWARE_VERSION resolution, Docker forwarding, and OTA comparison."""
    monkeypatch.setenv("FIRMWARE_VERSION", "v0.2.5")
    assert _paths.resolve_firmware_version(tmp_path) == "v0.2.5"

    monkeypatch.delenv("FIRMWARE_VERSION", raising=False)
    cfg_dir = tmp_path / "src" / "root"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "config.json").write_text(json.dumps({"github": {"version": "0.9"}}))
    assert _paths.resolve_firmware_version(tmp_path) == "0.9"

    docker_paths = build.resolve_build_paths(use_docker=True)
    cmd = build.build_docker_run_command(docker_paths, firmware_version="v0.2.0")
    idx = cmd.index("FIRMWARE_VERSION=v0.2.0")
    assert cmd[idx - 1] == "-e"

    stub_build_info = types.ModuleType("control.build_info")
    stub_build_info.BUILD_ID = "build_test_fw"
    stub_build_info.FIRMWARE_VERSION = "v0.2.0"
    monkeypatch.setitem(sys.modules, "control.build_info", stub_build_info)
    monkeypatch.setattr(control, "build_info", stub_build_info, raising=False)
    monkeypatch.setitem(constants.CONFIG["github"], "version", "0.1")

    assert constants.get_firmware_version() == "v0.2.0"

    class DummyResp:
        def __init__(self, tag_name):
            self.status_code = 200
            self.text = json.dumps({"tag_name": tag_name})

    monkeypatch.setattr(bootlib.requests, "get", lambda *a, **kw: DummyResp("v0.2.0"))
    assert bootlib.get_firmware_dct(require_new=True) == {}

    monkeypatch.setattr(bootlib.requests, "get", lambda *a, **kw: DummyResp("v0.3.0"))
    assert bootlib.get_firmware_dct(require_new=True)["tag_name"] == "v0.3.0"

    # Also verify deploy_assets() preserves FIRMWARE_VERSION when migrating config_old.json
    old_cfg = tmp_path / "config_old.json"
    new_cfg = tmp_path / "config.json"
    old_cfg.write_text(json.dumps({"github": {"version": "0.1"}, "serial": "SN-42"}))

    class StubFinder:
        def find_spec(self, fullname, path, target=None):
            if fullname in ("control.build_info", "control.frozen_root"):
                return importlib.util.spec_from_loader(fullname, self)
            return None

        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            if module.__name__ == "control.build_info":
                module.BUILD_ID = "build_test_fw"
                module.FIRMWARE_VERSION = "v0.2.0"
            elif module.__name__ == "control.frozen_root":
                new_cfg.write_text(json.dumps({"github": {"version": "0.1"}}))

    monkeypatch.chdir(tmp_path)
    monkeypatch.delitem(sys.modules, "control.build_info", raising=False)
    monkeypatch.delitem(sys.modules, "control.frozen_root", raising=False)
    monkeypatch.delattr(control, "build_info", raising=False)
    monkeypatch.delattr(control, "frozen_root", raising=False)
    monkeypatch.setattr(sys, "meta_path", [StubFinder(), *sys.meta_path])
    monkeypatch.setattr(constants, "ESP32", True)
    monkeypatch.setattr(constants, "CONFIG_FILE", str(new_cfg))
    monkeypatch.setattr(constants, "CONFIG_OLD_FILE", str(old_cfg))

    constants.deploy_assets(overwrite=True)
    migrated = json.loads(new_cfg.read_text())
    assert migrated["serial"] == "SN-42"
    assert migrated["github"]["version"] == "v0.2.0"


def test_compute_fpga_source_hash_and_version(tmp_path, monkeypatch):
    """Verify HDL hash determinism, mutation sensitivity, and resolve_fpga_version."""
    real_hs_src = resolve_hexastorm_src()
    digest1 = _paths.compute_fpga_source_hash(real_hs_src)
    digest2 = _paths.compute_fpga_source_hash(real_hs_src)
    assert len(digest1) == 64
    assert digest1 == digest2

    fake_hs_src = tmp_path / "fake_hs" / "src"
    pkg_dir = fake_hs_src / "hexastorm"
    (pkg_dir / "blocks").mkdir(parents=True)
    (pkg_dir / "core.py").write_text("class Dispatcher: pass\n")
    (pkg_dir / "blocks" / "photodiode_debounce.py").write_text("x = 1\n")

    h_before = _paths.compute_fpga_source_hash(fake_hs_src)
    (pkg_dir / "blocks" / "photodiode_debounce.py").write_text("x = 2\n")
    h_after = _paths.compute_fpga_source_hash(fake_hs_src)
    assert h_before != h_after

    monkeypatch.setenv("FPGA_VERSION", "v9.9.9-custom")
    assert _paths.resolve_fpga_version(tmp_path) == "v9.9.9-custom"

    monkeypatch.delenv("FPGA_VERSION", raising=False)
    fake_bit = tmp_path / "fpga.bit"
    fake_bit.write_bytes(b"bitstream-data")
    ver_with_bit = _paths.resolve_fpga_version(
        get_repo_root(), hexastorm_src=real_hs_src, bit_path=fake_bit
    )
    assert re.search(r"-[0-9a-f]{8}$", ver_with_bit)

    iso_root = tmp_path / "isolated_repo"
    (iso_root / "src" / "root").mkdir(parents=True)
    (iso_root / "src" / "root" / "config.json").write_text(
        json.dumps({"fpga": {"version": "0.7"}})
    )
    assert _paths.resolve_fpga_version(iso_root, hexastorm_src=fake_hs_src) == "0.7"
    iso_ver_bit = _paths.resolve_fpga_version(
        iso_root, hexastorm_src=fake_hs_src, bit_path=fake_bit
    )
    assert re.fullmatch(r"sha256-[0-9a-f]{8}", iso_ver_bit)


def test_build_fpga_override_and_cache_hit(tmp_path, monkeypatch):
    """Verify docker/build_fpga.py bitstream override copy and hash cache hit."""
    script_path = get_repo_root() / "docker" / "build_fpga.py"
    spec = importlib.util.spec_from_file_location("build_fpga_test", script_path)
    assert spec is not None and spec.loader is not None
    build_fpga_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_fpga_mod)

    override_src = tmp_path / "prebuilt_top.bin"
    override_payload = b"\xaa\x55" * 100
    override_src.write_bytes(override_payload)
    out_bit = tmp_path / "out" / "fpga.bit"
    fpga_build_dir = tmp_path / "build_fpga"

    result = build_fpga_mod.build_or_copy_fpga(
        workspace=get_repo_root(),
        output=out_bit,
        build_dir=fpga_build_dir,
        bitstream=override_src,
    )
    assert result == out_bit.resolve()
    assert out_bit.read_bytes() == override_payload

    hs_src = resolve_hexastorm_src()
    src_hash = _paths.compute_fpga_source_hash(hs_src)
    cached_bin = fpga_build_dir / "build" / "top.bin"
    cached_bin.parent.mkdir(parents=True)
    cached_payload = b"\x7e\xaa\x99\x7e" + (b"\x00" * 60_000)
    cached_bin.write_bytes(cached_payload)
    (fpga_build_dir / ".fpga_hash").write_text(f"{src_hash}\n")

    def _fail_subprocess(*a, **kw):
        raise AssertionError("Synthesis subprocess should not run on cache hit")

    monkeypatch.setattr(build_fpga_mod.subprocess, "run", _fail_subprocess)
    out_cached = tmp_path / "cached_out" / "fpga.bit"
    build_fpga_mod.build_or_copy_fpga(
        workspace=get_repo_root(),
        output=out_cached,
        build_dir=fpga_build_dir,
    )
    assert out_cached.read_bytes() == cached_payload


def test_fpga_version_in_constants_and_deploy_assets(tmp_path, monkeypatch):
    """Verify get_fpga_version() and deploy_assets() syncing FPGA_VERSION to config."""
    stub_build_info = types.ModuleType("control.build_info")
    stub_build_info.BUILD_ID = "build_fpga_test"
    stub_build_info.FIRMWARE_VERSION = "v0.2.0"
    stub_build_info.FPGA_VERSION = "e1111f0-deadbeef"
    monkeypatch.setitem(sys.modules, "control.build_info", stub_build_info)
    monkeypatch.setattr(control, "build_info", stub_build_info, raising=False)
    monkeypatch.setitem(constants.CONFIG["fpga"], "version", "0.1")

    assert constants.get_fpga_version() == "e1111f0-deadbeef"

    old_cfg = tmp_path / "config_old.json"
    new_cfg = tmp_path / "config.json"
    old_cfg.write_text(json.dumps({"fpga": {"version": "0.1"}, "serial": 7}))

    class StubFinder:
        def find_spec(self, fullname, path, target=None):
            if fullname in ("control.build_info", "control.frozen_root"):
                return importlib.util.spec_from_loader(fullname, self)
            return None

        def create_module(self, spec):
            return types.ModuleType(spec.name)

        def exec_module(self, module):
            if module.__name__ == "control.build_info":
                module.BUILD_ID = "build_fpga_test"
                module.FIRMWARE_VERSION = "v0.2.0"
                module.FPGA_VERSION = "e1111f0-deadbeef"
            elif module.__name__ == "control.frozen_root":
                new_cfg.write_text(json.dumps({"fpga": {"version": "0.1"}}))

    monkeypatch.chdir(tmp_path)
    monkeypatch.delitem(sys.modules, "control.build_info", raising=False)
    monkeypatch.delitem(sys.modules, "control.frozen_root", raising=False)
    monkeypatch.delattr(control, "build_info", raising=False)
    monkeypatch.delattr(control, "frozen_root", raising=False)
    monkeypatch.setattr(sys, "meta_path", [StubFinder(), *sys.meta_path])
    monkeypatch.setattr(constants, "ESP32", True)
    monkeypatch.setattr(constants, "CONFIG_FILE", str(new_cfg))
    monkeypatch.setattr(constants, "CONFIG_OLD_FILE", str(old_cfg))

    constants.deploy_assets(overwrite=True)
    migrated = json.loads(new_cfg.read_text())
    assert migrated["fpga"]["version"] == "e1111f0-deadbeef"
    assert migrated["github"]["version"] == "v0.2.0"
