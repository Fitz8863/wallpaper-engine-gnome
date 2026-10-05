"""PKGV 包内 .tex 头解析（场景壁纸分辨率探测）。"""

import struct

import pytest

import wallpaper_picker.picker as picker


def build_pkg(entries):
    """构造一个最小 PKGV 包：头 + 文件表 + 数据区。"""
    out = bytearray()
    out += struct.pack("<I", 8) + b"PKGV0000"
    out += struct.pack("<I", len(entries))
    for name, offset, length in entries:
        raw = name.encode()
        out += struct.pack("<I", len(raw)) + raw
        out += struct.pack("<II", offset, length)
    base = len(out)
    return bytes(out), base


def build_tex(width, height):
    """TEXV0005/TEXI0001 头 + 六个 u32（format/flags/tw/th/w/h）。"""
    head = b"TEXV0005\x00" + b"TEXI0001\x00"
    head += struct.pack("<IIIIII", 0, 0, width, height, width, height)
    return head


def test_largest_tex_dims(tmp_path):
    tex = build_tex(3840, 2160)
    data, base = build_pkg([("materials/big.tex", 0, len(tex)),
                            ("materials/small.tex", len(tex), 24)])
    pkg = tmp_path / "scene.pkg"
    pkg.write_bytes(data + tex + build_tex(512, 512))
    assert picker._pkg_largest_tex_dims(str(pkg)) == (3840, 2160)


def test_no_tex_returns_none(tmp_path):
    data, _ = build_pkg([("scene.json", 0, 2)])
    pkg = tmp_path / "scene.pkg"
    pkg.write_bytes(data + b"{}")
    assert picker._pkg_largest_tex_dims(str(pkg)) is None


def test_bad_header_returns_none(tmp_path):
    pkg = tmp_path / "scene.pkg"
    pkg.write_bytes(b"\x04\x00\x00\x00JUNK")
    assert picker._pkg_largest_tex_dims(str(pkg)) is None


def test_missing_file_returns_none(tmp_path):
    with pytest.raises(FileNotFoundError):
        picker._pkg_largest_tex_dims(str(tmp_path / "nope.pkg"))
