from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
import subprocess
import sys

import pytest

from create_iso import (
    DEFAULT_SEED,
    FLOPPY_SIZE,
    SECTOR_SIZE,
    BuildError,
    build_artifacts,
    build_bootloader,
    build_kernel,
    make_floppy_image,
    make_iso_image,
)


ROOT = Path(__file__).resolve().parents[1]


def _artifact(bundle: object, name: str) -> bytes:
    candidates = (name, f"{name}_bin", f"{name}_image", f"{name}_img")
    if isinstance(bundle, Mapping):
        for candidate in candidates:
            if candidate in bundle:
                value = bundle[candidate]
                break
        else:
            raise AssertionError(f"build_artifacts() did not return {name!r}")
    else:
        for candidate in candidates:
            if hasattr(bundle, candidate):
                value = getattr(bundle, candidate)
                break
        else:
            raise AssertionError(f"build_artifacts() did not return {name!r}")

    assert isinstance(value, bytes), f"{name} artifact must be immutable bytes"
    return value


def test_bootloader_has_signature_and_encodes_dynamic_sector_count() -> None:
    one_sector = build_bootloader(1)
    seven_sectors = build_bootloader(7)

    assert len(one_sector) == 512
    assert one_sector[-2:] == b"\x55\xaa"
    assert seven_sectors[-2:] == b"\x55\xaa"
    assert one_sector != seven_sectors
    assert b"\xb8\x01\x02" in one_sector  # MOV AX, 0x0201
    assert b"\xb8\x07\x02" in seven_sectors  # MOV AX, 0x0207


@pytest.mark.parametrize("kernel_sectors", [0, -1, 256])
def test_bootloader_rejects_invalid_sector_count(kernel_sectors: int) -> None:
    with pytest.raises(BuildError):
        build_bootloader(kernel_sectors)


def test_kernel_seed_is_deterministic_and_effective() -> None:
    first = build_kernel(seed=DEFAULT_SEED)
    replay = build_kernel(seed=DEFAULT_SEED)
    alternate_seed = 2 if DEFAULT_SEED == 1 else 1
    alternate = build_kernel(seed=alternate_seed)

    assert first == replay
    assert first != alternate


def test_floppy_image_embeds_boot_and_kernel_without_resizing() -> None:
    kernel = build_kernel(seed=DEFAULT_SEED)
    sectors = (len(kernel) + SECTOR_SIZE - 1) // SECTOR_SIZE
    boot = build_bootloader(sectors)
    floppy = make_floppy_image(boot, kernel)

    assert isinstance(floppy, bytes)
    assert len(floppy) == FLOPPY_SIZE
    assert floppy[:SECTOR_SIZE] == boot
    assert floppy[SECTOR_SIZE : SECTOR_SIZE + len(kernel)] == kernel
    assert not any(floppy[SECTOR_SIZE + len(kernel) :])


def test_floppy_image_rejects_oversized_payload() -> None:
    boot = build_bootloader(1)
    oversized_kernel = bytes(FLOPPY_SIZE - 511)

    with pytest.raises(BuildError):
        make_floppy_image(boot, oversized_kernel)


def test_build_artifacts_is_reproducible_and_internally_consistent() -> None:
    first = build_artifacts(seed=DEFAULT_SEED)
    replay = build_artifacts(seed=DEFAULT_SEED)

    for name in ("boot", "kernel", "floppy", "iso"):
        assert _artifact(first, name) == _artifact(replay, name)

    kernel = _artifact(first, "kernel")
    boot = _artifact(first, "boot")
    floppy = _artifact(first, "floppy")
    assert kernel == build_kernel(seed=DEFAULT_SEED)
    assert boot == build_bootloader(
        (len(kernel) + SECTOR_SIZE - 1) // SECTOR_SIZE
    )
    assert floppy == make_floppy_image(boot, kernel)
    assert _artifact(first, "iso") == make_iso_image(floppy)


def test_cli_check_confirms_tracked_artifact_parity() -> None:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, str(ROOT / "create_iso.py"), "--check"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
