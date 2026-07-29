from __future__ import annotations

import struct

from create_iso import (
    DEFAULT_SEED,
    FLOPPY_SIZE,
    SECTOR_SIZE,
    build_bootloader,
    build_kernel,
    make_floppy_image,
    make_iso_image,
)

ISO_BLOCK_SIZE = 2048


def _le16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _be16(data: bytes, offset: int) -> int:
    return struct.unpack_from(">H", data, offset)[0]


def _le32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _be32(data: bytes, offset: int) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _sector(image: bytes, lba: int) -> bytes:
    start = lba * ISO_BLOCK_SIZE
    return image[start : start + ISO_BLOCK_SIZE]


def _build_media() -> tuple[bytes, bytes]:
    kernel = build_kernel(seed=DEFAULT_SEED)
    boot = build_bootloader((len(kernel) + SECTOR_SIZE - 1) // SECTOR_SIZE)
    floppy = make_floppy_image(boot, kernel)
    return floppy, make_iso_image(floppy)


def test_iso_primary_volume_descriptor_is_structurally_valid() -> None:
    _, iso = _build_media()
    pvd = _sector(iso, 16)
    volume_sectors = len(iso) // ISO_BLOCK_SIZE

    assert len(iso) % ISO_BLOCK_SIZE == 0
    assert pvd[:7] == b"\x01CD001\x01"
    assert _le32(pvd, 80) == volume_sectors
    assert _be32(pvd, 84) == volume_sectors
    assert _le16(pvd, 120) == _be16(pvd, 122) == 1
    assert _le16(pvd, 124) == _be16(pvd, 126) == 1
    assert _le16(pvd, 128) == _be16(pvd, 130) == ISO_BLOCK_SIZE


def test_iso_contains_little_and_big_endian_root_path_tables() -> None:
    _, iso = _build_media()
    pvd = _sector(iso, 16)
    path_table_size = _le32(pvd, 132)

    assert path_table_size > 0
    assert _be32(pvd, 136) == path_table_size

    little_lba = _le32(pvd, 140)
    big_lba = _be32(pvd, 148)
    volume_sectors = len(iso) // ISO_BLOCK_SIZE
    assert 0 < little_lba < volume_sectors
    assert 0 < big_lba < volume_sectors

    little = _sector(iso, little_lba)[:path_table_size]
    big = _sector(iso, big_lba)[:path_table_size]
    assert little[0:2] == big[0:2] == b"\x01\x00"
    assert little[8] == big[8] == 0  # Root directory identifier.
    assert _le16(little, 6) == 1
    assert _be16(big, 6) == 1
    assert _le32(little, 2) == _be32(big, 2)


def test_el_torito_catalog_checksum_and_embedded_boot_image() -> None:
    floppy, iso = _build_media()
    boot_record = _sector(iso, 17)

    assert len(floppy) == FLOPPY_SIZE
    assert boot_record[:7] == b"\x00CD001\x01"
    assert boot_record[7:30] == b"EL TORITO SPECIFICATION"

    catalog_lba = _le32(boot_record, 71)
    catalog = _sector(iso, catalog_lba)
    assert sum(struct.unpack_from("<16H", catalog, 0)) & 0xFFFF == 0
    assert catalog[0] == 1
    assert catalog[30:32] == b"\x55\xaa"
    assert catalog[32] == 0x88
    assert catalog[33] == 0x02  # 1.44 MB floppy emulation.

    boot_image_lba = _le32(catalog, 40)
    boot_start = boot_image_lba * ISO_BLOCK_SIZE
    assert iso[boot_start : boot_start + FLOPPY_SIZE] == floppy
