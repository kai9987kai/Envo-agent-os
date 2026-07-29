import pytest

from create_iso import ASM, AssemblyError


def test_duplicate_label_is_rejected() -> None:
    asm = ASM(org=0x1000)
    asm.label("loop")

    with pytest.raises(AssemblyError, match=r"(?i)duplicate|already defined"):
        asm.label("loop")


def test_undefined_label_is_rejected() -> None:
    asm = ASM(org=0x1000)
    asm.jmp("missing")

    with pytest.raises(AssemblyError, match=r"(?i)undefined|missing"):
        asm.resolve()


def test_short_forward_branch_out_of_range_is_rejected() -> None:
    asm = ASM(org=0x1000)
    asm.je("target")
    asm.db(bytes(128))
    asm.label("target")

    with pytest.raises(AssemblyError, match=r"(?i)range"):
        asm.resolve()


def test_short_backward_branch_out_of_range_is_rejected() -> None:
    asm = ASM(org=0x1000)
    asm.label("target")
    asm.db(bytes(127))
    asm.je("target")

    with pytest.raises(AssemblyError, match=r"(?i)range"):
        asm.resolve()


def test_short_branch_range_boundaries_are_valid() -> None:
    forward = ASM(org=0x1000)
    forward.je("target")
    forward.db(bytes(127))
    forward.label("target")
    forward_code = forward.resolve()

    backward = ASM(org=0x1000)
    backward.label("target")
    backward.db(bytes(126))
    backward.je("target")
    backward_code = backward.resolve()

    assert forward_code[1] == 0x7F
    assert backward_code[-1] == 0x80
