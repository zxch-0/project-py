"""Tests unitaires — runtimes.py"""
import pytest

from runtimes import (
    build_argv, detect_runtime, node_bin, runtime_available, syntax_check,
)


def test_detect():
    assert detect_runtime("a.py") == "python"
    assert detect_runtime("A.PY") == "python"
    assert detect_runtime("a.js") == "node"
    assert detect_runtime("a.mjs") == "node"
    assert detect_runtime("a.sh") == "bash"
    assert detect_runtime("a.txt") is None
    assert detect_runtime("") is None


def test_build_argv_python():
    argv, err = build_argv("python", "/x/a.py", "--a b")
    assert err is None
    assert argv[-3:] == ["/x/a.py", "--a", "b"]
    assert argv[1] == "-u"


def test_build_argv_custom():
    import sys
    py = sys.executable
    argv, err = build_argv("custom", "/x/a.py", "", f"{py} -u")
    assert err is None
    assert argv[-2:] == ["-u", "/x/a.py"]
    argv2, err2 = build_argv("custom", "/x/a.py", "", f"{py} -u {{file}} --x")
    assert err2 is None
    assert argv2 == [py, "-u", "/x/a.py", "--x"]
    _, err3 = build_argv("custom", "/x/a.py", "", "cmd-inexistante-xyz")
    assert err3 is not None
    _, err4 = build_argv("custom", "/x/a.py", "", "")
    assert err4 is not None


def test_build_argv_unknown():
    argv, err = build_argv("cobol", "/x/a.cbl")
    assert argv is None and err is not None


def test_syntax_python(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("x = 1\nprint(x)\n")
    assert syntax_check("python", str(ok))[0] is True
    bad = tmp_path / "bad.py"
    bad.write_text('print("x"\n')
    ok2, msg = syntax_check("python", str(bad))
    assert ok2 is False and "ligne 1" in msg
    assert syntax_check("python", str(tmp_path / "nope.py"))[0] is False


def test_syntax_bash(tmp_path):
    import shutil
    if not shutil.which("bash"):
        pytest.skip("bash absent")
    ok = tmp_path / "ok.sh"
    ok.write_text('echo hello\necho "done"\n')
    assert syntax_check("bash", str(ok))[0] is True
    bad = tmp_path / "bad.sh"
    bad.write_text('if true; then\necho x\n')  # fi manquant
    assert syntax_check("bash", str(bad))[0] is False


def test_syntax_node(tmp_path):
    if not node_bin():
        pytest.skip("node absent")
    ok = tmp_path / "ok.js"
    ok.write_text("const x = 1;\nconsole.log(x);\n")
    assert syntax_check("node", str(ok))[0] is True
    bad = tmp_path / "bad.js"
    bad.write_text("const x = ;\n")
    assert syntax_check("node", str(bad))[0] is False


def test_availability():
    ok, info = runtime_available("python")
    assert ok is True and "Python" in info
    assert runtime_available("cobol")[0] is False
