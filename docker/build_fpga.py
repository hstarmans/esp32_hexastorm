#!/usr/bin/env python3
"""Synthesize or stage the iCE40UP5K FPGA bitstream for freezefs bundling."""

import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MIN_BITSTREAM_BYTES = 50_000


def _sha256_prefix(path: Path, length: int = 8) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:length]


def _load_paths_helper(workspace: Path):
    paths_file = workspace / "src" / "control" / "_paths.py"
    spec = spec_from_file_location("_control_paths", paths_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load paths helper from {paths_file}")
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_or_copy_fpga(
    workspace: Path,
    output: Path,
    build_dir: Path,
    bitstream: str | Path | None = None,
    component: str = "dispatcher",
    rebuild: bool = False,
) -> Path:
    """Copy a prebuilt bitstream override or synthesize top.bin from hexastorm HDL."""
    workspace = Path(workspace).resolve()
    output = Path(output).resolve()
    build_dir = Path(build_dir).resolve()

    if bitstream:
        src_bit = Path(bitstream).expanduser().resolve()
        if not src_bit.is_file() or src_bit.stat().st_size == 0:
            raise FileNotFoundError(
                f"FPGA bitstream override missing or empty: {src_bit}"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        if src_bit != output:
            shutil.copy2(src_bit, output)
        size = output.stat().st_size
        logger.info(
            "Copied FPGA bitstream override (%d bytes, sha256:%s) -> %s",
            size,
            _sha256_prefix(output),
            output,
        )
        return output

    paths_helper = _load_paths_helper(workspace)
    hexastorm_src = paths_helper.resolve_hexastorm_src(workspace)
    src_hash = paths_helper.compute_fpga_source_hash(hexastorm_src)

    hash_file = build_dir / ".fpga_hash"
    cached_bit = build_dir / "build" / "top.bin"
    if (
        not rebuild
        and hash_file.is_file()
        and hash_file.read_text(encoding="utf-8").strip() == src_hash
        and cached_bit.is_file()
        and cached_bit.stat().st_size >= MIN_BITSTREAM_BYTES
    ):
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cached_bit, output)
        size = output.stat().st_size
        logger.info(
            "FPGA bitstream cache hit (%d bytes, sha256:%s) -> %s",
            size,
            _sha256_prefix(output),
            output,
        )
        return output

    logger.info(
        "Synthesizing iCE40UP5K FPGA bitstream (component=%s) from %s...",
        component,
        hexastorm_src,
    )
    build_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    py_path_entries = [str(hexastorm_src)]
    if existing_py_path := env.get("PYTHONPATH"):
        py_path_entries.append(existing_py_path)
    env["PYTHONPATH"] = os.pathsep.join(py_path_entries)

    bin_dir = str(Path(sys.executable).resolve().parent)
    existing_path = env.get("PATH", "")
    env["PATH"] = f"{bin_dir}{os.pathsep}{existing_path}" if existing_path else bin_dir

    subprocess.run(
        [
            sys.executable,
            "-m",
            "hexastorm.build",
            "--component",
            component,
            "--no-program",
        ],
        cwd=str(build_dir),
        env=env,
        check=True,
    )

    if not cached_bit.is_file() or cached_bit.stat().st_size < MIN_BITSTREAM_BYTES:
        raise RuntimeError(
            f"FPGA synthesis produced missing or undersized bitstream: {cached_bit}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached_bit, output)
    hash_file.write_text(f"{src_hash}\n", encoding="utf-8")

    size = output.stat().st_size
    logger.info(
        "Synthesized FPGA bitstream (%d bytes, sha256:%s) -> %s",
        size,
        _sha256_prefix(output),
        output,
    )
    return output


def main() -> None:
    default_workspace = (
        os.environ.get("WORKSPACE_DIR") or str(Path(__file__).resolve().parents[1])
    )
    parser = argparse.ArgumentParser(
        description="Synthesize or copy iCE40UP5K FPGA bitstream into src/root/sd/fpga/"
    )
    parser.add_argument(
        "--workspace",
        default=default_workspace,
        help="Path to esp32_hexastorm workspace root",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Destination path for fpga.bit",
    )
    parser.add_argument(
        "--build-dir",
        default=None,
        help="Working directory for Amaranth/YoWASP FPGA synthesis artifacts",
    )
    parser.add_argument(
        "--bitstream",
        default=os.environ.get("FPGA_BITSTREAM", ""),
        help="Optional path to a prebuilt top.bin bitstream override",
    )
    parser.add_argument(
        "--component",
        default="dispatcher",
        help="Top-level Hexastorm gateware component to synthesize (default: dispatcher)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force resynthesis even when HDL source hash matches cache",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    output = (
        Path(args.output)
        if args.output
        else workspace / "src" / "root" / "sd" / "fpga" / "fpga.bit"
    )
    build_dir = (
        Path(args.build_dir) if args.build_dir else workspace / "build" / "fpga"
    )
    rebuild = args.rebuild or os.environ.get("REBUILD_FPGA", "").strip() == "1"

    build_or_copy_fpga(
        workspace=workspace,
        output=output,
        build_dir=build_dir,
        bitstream=args.bitstream or None,
        component=args.component,
        rebuild=rebuild,
    )


if __name__ == "__main__":
    main()
