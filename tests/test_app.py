"""Tests d'integration — app.py (client de test Flask, DATA_DIR temporaire)."""
import io
import json
import os
import tempfile
import time
import zipfile

DATA_TMP = tempfile.mkdtemp(prefix="zr-test-")
os.environ["DATA_DIR"] = DATA_TMP
os.environ["FLASK_SECRET_KEY"] = "test-secret-key"

import pytest  # noqa: E402

import app as appmod  # noqa: E402
from runtimes import bash_bin, node_bin  # noqa: E402


@pytest.fixture(scope="module")
def client():
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        yield c


@pytest.fixture(scope="module")
def authed(client):
    r = client.post("/setup", data={"code": "code-test-123",
                                    "confirm": "code-test-123"})
    assert r.status_code in (302, 200)
    return client


def _upload_py(c, code, name="T1"):
    data = {"file": (io.BytesIO(code.encode()), "s.py"), "name": name}
    r = c.post("/api/scripts/upload", data=data,
               content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    d = r.get_json()
    assert d["ok"] is True
    return d["id"]


def _wait_status(c, sid, want=("stopped", "error"), timeout=40):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = c.get(f"/api/scripts/{sid}/console?since=0")
        last = r.get_json()
        if last["status"] in want:
            return last
        time.sleep(0.5)
    raise AssertionError(f"timeout, dernier statut : {last['status'] if last else '?'}")


def _wait_runs(c, sid, timeout=15):
    """Attend que le waiter ait journalise la fin d'execution."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = c.get(f"/api/scripts/{sid}").get_json()["script"]
        if d["runs"]:
            return d
        time.sleep(0.5)
    raise AssertionError("runs vide apres timeout")


def _console_text(console_json):
    return "\n".join(l["text"] for l in console_json["lines"]
                     if l["type"] in ("out", "err"))


# ---------------------------------------------------------- auth ---

def test_health_and_setup_flow(client):
    r = client.get("/api/health")
    assert r.get_json()["ok"] is True
    # Deconnecte : API protegee
    client.get("/logout")
    r = client.get("/api/status")
    assert r.status_code == 401


# ---------------------------------------------------------- cycle de vie ---

def test_upload_analyze_prefill_run(authed):
    c = authed
    code = ('print("1 - A")\nprint("2 - B")\n'
            'c = input("Choix (1-2) : ")\n'
            'n = input("Nom : ")\n'
            'print(f"RESULTAT:{c}:{n}")\n')
    sid = _upload_py(c, code, "Prefill")
    r = c.get(f"/api/scripts/{sid}/analyze")
    a = r.get_json()["analysis"]
    assert len(a["inputs"]) == 2
    assert a["inputs"][0]["options"] == ["1", "2"]

    r = c.post(f"/api/scripts/{sid}/run", json={"answers": ["2", "Ada"]})
    assert r.get_json()["ok"] is True
    fin = _wait_status(c, sid)
    assert "RESULTAT:2:Ada" in _console_text(fin)

    d = _wait_runs(c, sid)
    assert d["runs"][0]["reason"] == "success"
    assert len(d["qa_history"]) == 2


def test_interactive_waiting_then_input(authed):
    c = authed
    sid = _upload_py(c, 'r = input("Couleur ? ")\nprint(f"OK:{r}")\n', "Interactif")
    assert c.post(f"/api/scripts/{sid}/run", json={}).get_json()["ok"] is True
    # Attendre la detection d'attente
    prompt = None
    for _ in range(60):
        d = c.get(f"/api/scripts/{sid}/console?since=0").get_json()
        if d.get("waiting"):
            prompt = d["prompt"]
            break
        time.sleep(0.5)
    assert prompt and "Couleur" in prompt
    assert c.post(f"/api/scripts/{sid}/input", json={"text": "bleu"}).get_json()["ok"] is True
    fin = _wait_status(c, sid)
    assert "OK:bleu" in _console_text(fin)


def test_crash_diagnosis(authed):
    c = authed
    sid = _upload_py(c, "import module_absent_zr_xyz\n", "Crash")
    r = c.post(f"/api/scripts/{sid}/run", json={})
    assert r.get_json()["ok"] is False  # echec immediat
    d = c.get(f"/api/scripts/{sid}").get_json()["script"]
    assert d["status"] == "error"
    dg = d["diagnosis"]
    assert dg and dg["exception"] == "ModuleNotFoundError"
    assert dg["action"]["package"] == "module_absent_zr_xyz"


def test_syntax_refused(authed):
    c = authed
    sid = _upload_py(c, 'print("x"\n', "Syntax")
    r = c.post(f"/api/scripts/{sid}/run", json={})
    assert r.get_json()["ok"] is False
    assert "syntaxe" in r.get_json()["message"].lower()


def test_stop_long_running(authed):
    c = authed
    sid = _upload_py(c, "import time\nprint('go')\ntime.sleep(120)\n", "Long")
    assert c.post(f"/api/scripts/{sid}/run", json={}).get_json()["ok"] is True
    time.sleep(2)
    r = c.post(f"/api/scripts/{sid}/stop")
    assert r.get_json()["ok"] is True
    d = _wait_runs(c, sid)
    assert d["status"] == "stopped"
    assert d["runs"][0]["reason"] == "stopped"


def test_file_edit_and_traversal(authed):
    c = authed
    sid = _upload_py(c, "print('v1')\n", "Edit")
    r = c.put(f"/api/scripts/{sid}/file",
              json={"path": "ignored.py", "content": "print('v2')\n"})
    assert r.get_json()["ok"] is True
    d = c.get(f"/api/scripts/{sid}/file?path=x").get_json()
    assert "v2" in d["content"]
    # Python invalide refuse
    r = c.put(f"/api/scripts/{sid}/file",
              json={"path": "x", "content": "print(\n"})
    assert r.get_json()["ok"] is False


# ---------------------------------------------------------- projets ---

def _make_project_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("main.py", "from lib.calc import double\n"
                    'n = input("Nombre : ")\nprint(f"D:{double(int(n))}")\n')
        zf.writestr("lib/calc.py", "def double(x):\n    return x * 2\n")
        zf.writestr("requirements.txt", "")
        zf.writestr("../evil.py", "print('evil')\n")
    buf.seek(0)
    return buf


def test_archive_upload_scan_run(authed):
    c = authed
    data = {"file": (_make_project_zip(), "proj.zip"), "name": "ProjZip"}
    r = c.post("/api/scripts/upload-archive", data=data,
               content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)[:400]
    d = r.get_json()
    assert d["ok"] is True, d
    sid = d["id"]
    paths = [e["path"] for e in d["scan"]["entries"]]
    assert "main.py" in paths
    assert d["scan"]["suggested_entry"]["path"] == "main.py"
    assert "requirements.txt" in d["scan"]["manifests"]
    # zip-slip : aucun fichier hors projet
    import glob
    assert not os.path.exists(os.path.join(DATA_TMP, "scripts", sid, "evil.py"))
    assert not glob.glob(os.path.join(DATA_TMP, "scripts", sid, "*", "evil.py"))

    r = c.get(f"/api/scripts/{sid}/tree")
    assert r.get_json()["ok"] is True

    r = c.post(f"/api/scripts/{sid}/run", json={"answers": ["21"]})
    assert r.get_json()["ok"] is True
    fin = _wait_status(c, sid)
    assert "D:42" in _console_text(fin)

    # Lecture d'un fichier du projet + traversal refuse
    r = c.get(f"/api/scripts/{sid}/file", query_string={"path": "lib/calc.py"})
    assert "double" in r.get_json()["content"]
    r = c.get(f"/api/scripts/{sid}/file", query_string={"path": "../../app.py"})
    assert r.get_json()["ok"] is False


def test_entry_switch(authed):
    c = authed
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.py", "print('A')\n")
        zf.writestr("b.py", "print('B')\n")
    buf.seek(0)
    data = {"file": (buf, "two.zip"), "name": "Two"}
    sid = c.post("/api/scripts/upload-archive", data=data,
                 content_type="multipart/form-data").get_json()["id"]
    r = c.post(f"/api/scripts/{sid}/entry", json={"path": "b.py"})
    assert r.get_json()["ok"] is True
    assert c.post(f"/api/scripts/{sid}/run", json={}).get_json()["ok"] is True
    fin = _wait_status(c, sid)
    assert "\nB" in "\n" + _console_text(fin) or "B" in _console_text(fin)
    # Entree inexistante refusee
    r = c.post(f"/api/scripts/{sid}/entry", json={"path": "nope.py"})
    assert r.get_json()["ok"] is False


# ---------------------------------------------------------- runtimes ---

def test_shell_script(authed):
    if not bash_bin():
        pytest.skip("bash absent")
    c = authed
    code = 'echo "1 - Oui"\necho "2 - Non"\nread -p "Choix : " c\necho "R:$c"\n'
    data = {"file": (io.BytesIO(code.encode()), "s.sh"), "name": "Shell"}
    sid = c.post("/api/scripts/upload", data=data,
                 content_type="multipart/form-data").get_json()["id"]
    a = c.get(f"/api/scripts/{sid}/analyze").get_json()["analysis"]
    assert a["language"] == "shell"
    assert a["inputs"][0]["options"] == ["1", "2"]
    assert c.post(f"/api/scripts/{sid}/run", json={"answers": ["1"]}).get_json()["ok"] is True
    fin = _wait_status(c, sid)
    assert "R:1" in _console_text(fin)


def test_node_script(authed):
    if not node_bin():
        pytest.skip("node absent")
    c = authed
    code = ("import readline from 'readline';\n"
            "const rl = readline.createInterface({input: process.stdin, output: process.stdout});\n"
            "const n = await new Promise(r => rl.question('Nombre : ', r));\n"
            "console.log('N:' + n);\nrl.close();\n")
    data = {"file": (io.BytesIO(code.encode()), "s.mjs"), "name": "NodeT"}
    sid = c.post("/api/scripts/upload", data=data,
                 content_type="multipart/form-data").get_json()["id"]
    assert c.post(f"/api/scripts/{sid}/run", json={"answers": ["7"]}).get_json()["ok"] is True
    fin = _wait_status(c, sid)
    assert "N:7" in _console_text(fin)


def test_custom_runtime(authed):
    c = authed
    sid = _upload_py(c, "print('CUSTOM_OK')\n", "Custom")
    c.put(f"/api/scripts/{sid}",
          json={"runtime": "custom", "custom_cmd": "python3 -u"})
    import sys
    c.put(f"/api/scripts/{sid}",
          json={"runtime": "custom", "custom_cmd": f"{sys.executable} -u"})
    assert c.post(f"/api/scripts/{sid}/run", json={}).get_json()["ok"] is True
    fin = _wait_status(c, sid)
    assert "CUSTOM_OK" in _console_text(fin)


# ---------------------------------------------------------- webhooks ---

def test_webhook_flow(authed):
    c = authed
    cfg = c.get("/api/webhooks/config").get_json()["config"]
    path = "/" + "/".join(cfg["url"].split("/")[3:])
    r = c.post(path, json={"signal": "BUY", "x": 1})
    assert r.status_code == 200
    logs = c.get("/api/webhooks/logs?limit=5").get_json()
    assert logs["stats"]["total"] >= 1
    assert logs["logs"][0]["body"]["signal"] == "BUY"
    r = c.post("/webhook/token-faux", json={})
    assert r.status_code == 404


def test_webhook_autorun(authed):
    c = authed
    sid = _upload_py(c, "import os\nprint('P:' + os.environ.get('WEBHOOK_PAYLOAD', '{}'))\n", "WH")
    c.put("/api/webhooks/config",
          json={"linked_script_id": sid, "auto_run": True})
    cfg = c.get("/api/webhooks/config").get_json()["config"]
    path = "/" + "/".join(cfg["url"].split("/")[3:])
    r = c.post(path, json={"a": 1})
    assert r.get_json()["triggered"]["action"] == "run"
    fin = _wait_status(c, sid)
    assert '"a": 1' in _console_text(fin) or "'a': 1" in _console_text(fin)


# ---------------------------------------------------------- divers ---

def test_overview_and_runtimes(authed):
    c = authed
    d = c.get("/api/overview").get_json()
    assert d["ok"] is True
    assert d["stats"]["scripts_total"] >= 1
    assert "python" in d["runtimes"]
    r = c.get("/api/runtimes").get_json()
    assert r["archives"]["zip"] is True


def test_change_code(authed):
    c = authed
    r = c.post("/api/change-code",
               json={"current": "code-test-123", "new": "nouveau-code-456"})
    assert r.get_json()["ok"] is True
    c.get("/logout")
    r = c.post("/login", data={"code": "nouveau-code-456"})
    assert r.status_code in (302, 200)
    # Restaure pour la suite eventuelle
    c.post("/api/change-code",
           json={"current": "nouveau-code-456", "new": "code-test-123"})
