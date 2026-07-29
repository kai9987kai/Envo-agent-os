from __future__ import annotations

from pathlib import Path

import pytest

from run_experiment import ExperimentRunError, main, qemu_command


def test_qemu_command_is_headless_and_media_specific(tmp_path) -> None:
    iso = tmp_path / "os.iso"
    trace = tmp_path / "trace.bin"

    command = qemu_command("qemu-system-i386", iso, "iso", trace)

    assert command[0] == "qemu-system-i386"
    assert command[command.index("-display") + 1] == "none"
    assert command[command.index("-debugcon") + 1] == f"file:{trace}"
    assert command[command.index("-boot") + 1] == "d"
    assert f"file={iso},format=raw,media=cdrom,readonly=on" in command


def test_floppy_command_and_invalid_media_type(tmp_path) -> None:
    floppy = tmp_path / "floppy.img"
    trace = tmp_path / "trace.bin"

    command = qemu_command("qemu", floppy, "floppy", trace)

    assert command[command.index("-boot") + 1] == "a"
    assert f"file={floppy},format=raw,if=floppy,readonly=on" in command
    with pytest.raises(ValueError, match="unsupported"):
        qemu_command("qemu", floppy, "tape", trace)


def test_cli_reports_missing_media_without_starting_qemu(
    tmp_path,
    capsys,
) -> None:
    result = main([str(tmp_path / "missing.iso")])
    output = capsys.readouterr()

    assert result == 1
    assert "boot media not found" in output.err


def test_runner_error_is_a_runtime_error() -> None:
    assert issubclass(ExperimentRunError, RuntimeError)
    assert Path("os.iso").suffix == ".iso"
