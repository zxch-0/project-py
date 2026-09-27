"""Tests unitaires — projects.py"""
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from projects import archive_kind, extract_archive, scan_project


def test_archive_kind():
    assert archive_kind("a.zip") == "zip"
    assert archive_kind("a.tar.gz") == "tar"
    assert archive_kind("a.tgz") == "tar"
    assert archive_kind("a.tar") == "tar"
    assert archive_kind("a.rar") == "rar"
    assert archive_kind("a.7z") == "7z"
    assert archive_kind("a.py") is None


def _make_zip(path: Path, members: dict):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)


def test_extract_zip_ok(tmp_path):
    arch = tmp_path / "p.zip"
    _make_zip(arch, {"main.py": "print(1)\n", "lib/u.py": "x=1\n",
                     "requirements.txt": "requests\n"})
    dest = tmp_path / "out"
    ok, msg, count = extract_archive(arch, dest)
    assert ok is True and count == 3, msg
    assert (dest / "main.py").exists()
    assert (dest / "lib" / "u.py").exists()


def test_zip_slip_blocked(tmp_path):
    arch = tmp_path / "evil.zip"
    _make_zip(arch, {"../evil.py": "x", "/abs.py": "x", "ok.py": "print(1)\n"})
    dest = tmp_path / "out"
    ok, msg, count = extract_archive(arch, dest)
    assert ok is True
    assert not (tmp_path / "evil.py").exists()
    assert (dest / "ok.py").exists()


def test_extract_tar_ok(tmp_path):
    arch = tmp_path / "p.tar.gz"
    with tarfile.open(arch, "w:gz") as tf:
        for name, content in {"app.py": "print(1)\n"}.items():
            data = content.encode()
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    dest = tmp_path / "out"
    ok, msg, count = extract_archive(arch, dest)
    assert ok is True and count == 1, msg


def test_extract_7z_ok(tmp_path):
    py7zr = pytest.importorskip("py7zr")
    arch = tmp_path / "p.7z"
    with py7zr.SevenZipFile(arch, "w") as zf:
        zf.writestr("print(7)\n", "seven.py")
        zf.writestr("x\n", "sub/nested.txt")
    dest = tmp_path / "out"
    ok, msg, count = extract_archive(arch, dest)
    assert ok is True and count == 2, msg
    assert (dest / "seven.py").exists()


def test_bad_archive(tmp_path):
    arch = tmp_path / "bad.zip"
    arch.write_text("ceci n'est pas un zip")
    ok, msg, _ = extract_archive(arch, tmp_path / "out")
    assert ok is False


def test_scan_project(tmp_path):
    root = tmp_path / "proj"
    (root / "lib").mkdir(parents=True)
    (root / "main.py").write_text("print(1)\n")
    (root / "lib" / "u.py").write_text("x=1\n")
    (root / "requirements.txt").write_text("requests\n")
    (root / "README.md").write_text("# hi\n")
    scan = scan_project(root)
    assert scan["total_files"] == 4
    assert scan["suggested_entry"]["path"] == "main.py"
    assert scan["manifests"]["requirements.txt"] == "requirements.txt"
    assert scan["readme"] == "README.md"
    assert all("path" in e and "runtime" in e for e in scan["entries"])
