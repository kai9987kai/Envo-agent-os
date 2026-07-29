from __future__ import annotations

import io

import pycdlib

from create_iso import (
    DEFAULT_SEED,
    SECTOR_SIZE,
    build_bootloader,
    build_kernel,
    make_floppy_image,
    make_iso_image,
)


def _build_media() -> tuple[bytes, bytes]:
    kernel = build_kernel(seed=DEFAULT_SEED)
    kernel_sectors = (len(kernel) + SECTOR_SIZE - 1) // SECTOR_SIZE
    boot = build_bootloader(kernel_sectors)
    floppy = make_floppy_image(boot, kernel)
    return floppy, make_iso_image(floppy)


def test_iso_opens_lists_and_extracts_with_pycdlib(tmp_path) -> None:
    """Exercise the ISO through an independent ISO9660/El Torito parser."""
    floppy, iso_image = _build_media()
    iso_path = tmp_path / "os.iso"
    iso_path.write_bytes(iso_image)

    iso = pycdlib.PyCdlib()
    opened = False
    try:
        iso.open(str(iso_path))
        opened = True

        identifiers = {
            child.file_identifier()
            for child in iso.list_children(iso_path="/")
        }
        assert {
            b"BOOT.CAT;1",
            b"BOOT.IMG;1",
            b"README.TXT;1",
        } <= identifiers

        extracted_boot = io.BytesIO()
        iso.get_file_from_iso_fp(
            extracted_boot,
            iso_path="/BOOT.IMG;1",
        )
        assert extracted_boot.getvalue() == floppy

        extracted_readme = io.BytesIO()
        iso.get_file_from_iso_fp(
            extracted_readme,
            iso_path="/README.TXT;1",
        )
        readme = extracted_readme.getvalue()
        assert b"ENVO AGENT OS" in readme
        assert b"Controls: P pauses; R restarts." in readme
    finally:
        if opened:
            iso.close()
