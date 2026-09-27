"""
PyRunner — Console privée pour exécuter des scripts Python sur Render.
v2 : analyse intelligente (questions/menus), console interactive (stdin),
logs structurés parfaits, diagnostic de crash, webhooks.
"""
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Flask, jsonify, redirect, render_template, request,
    send_file, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from analyzer import analyze_source, diagnose_crash
from runner import (
    LiveProcess, get_live, register, unregister, is_pid_alive,
    jsonl_path, log_path, read_jsonl_since, read_jsonl_tail_text,
    utcnow_iso,
)

# ---------------------------------------------------------------- config ---

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
SCRIPTS_ROOT = DATA_DIR / "scripts"

AUTH_FILE = DATA_DIR / "auth.json"
APP_SECRET_FILE = DATA_DIR / "app_secret.key"
SCRIPTS_META_FILE = DATA_DIR / "scripts.json"
WEBHOOK_CONFIG_FILE = DATA_DIR / "webhook.json"
WEBHOOK_LOGS_FILE = DATA_DIR / "webhook_logs.jsonl"
ACTIVITY_FILE = DATA_DIR / "activity.jsonl"

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "16"))
MAX_WEBHOOK_LOGS = 500
MAX_WEBHOOK_BODY = 20 * 1024
MAX_RUNS = 20
MAX_ACTIVITY = 200

DATA_DIR.mkdir(parents=True, exist_ok=True)
SCRIPTS_ROOT.mkdir(parents=True, exist_ok=True)

META_LOCK = threading.Lock()

app = Flask(__name__)

def _get_or_create_app_secret() -> str:
    env_key = os.environ.get("FLASK_SECRET_KEY")
    if env_key:
        return env_key
    if APP_SECRET_FILE.exists():
        try:
            return APP_SECRET_FILE.read_text().strip()
        except OSError:
            pass
    new_key = secrets.token_hex(32)
    try:
        APP_SECRET_FILE.write_text(new_key)
    except OSError:
        pass
    return new_key

app.secret_key = _get_or_create_app_secret()
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.permanent_session_lifetime = 60 * 60 * 24 * 30

ENV_SECRET_CODE = os.environ.get("SECRET_CODE", "").strip()
BOOT_TIME = time.time()

# ------------------------------------------------------------- helpers ---

def _read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)

# --- activité ---

def log_activity(icon: str, text: str, script_id: str | None = None):
    try:
        with open(ACTIVITY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": utcnow_iso(), "icon": icon, "text": text,
                                "script_id": script_id}, ensure_ascii=False) + "\n")
        # rotation
        with open(ACTIVITY_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_ACTIVITY:
            with open(ACTIVITY_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_ACTIVITY:])
    except OSError:
        pass

def read_activity(limit: int = 30) -> list:
    if not ACTIVITY_FILE.exists():
        return []
    try:
        with open(ACTIVITY_FILE, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
    except OSError:
        return []
    out = []
    for line in lines[-limit:][::-1]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out

# --- auth ---

def is_setup_done() -> bool:
    if ENV_SECRET_CODE:
        return True
    auth = _read_json(AUTH_FILE, None)
    return bool(auth and auth.get("password_hash"))

def verify_code(code: str) -> bool:
    if ENV_SECRET_CODE and code == ENV_SECRET_CODE:
        return True
    auth = _read_json(AUTH_FILE, None)
    if not auth or not auth.get("password_hash"):
        return False
    try:
        return check_password_hash(auth["password_hash"], code)
    except Exception:
        return False

def set_code(code: str) -> None:
    _write_json(AUTH_FILE, {
        "password_hash": generate_password_hash(code),
        "created_at": utcnow_iso(), "updated_at": utcnow_iso(),
    })

def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Non authentifié"}), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper

# --- scripts store ---

def load_scripts_meta() -> dict:
    data = _read_json(SCRIPTS_META_FILE, {})
    return data if isinstance(data, dict) else {}

def save_scripts_meta(meta: dict) -> None:
    _write_json(SCRIPTS_META_FILE, meta)

def script_dir(script_id: str) -> Path:
    return SCRIPTS_ROOT / script_id

def script_main_path(meta: dict) -> Path:
    return script_dir(meta["id"]) / meta.get("filename", "main.py")

def script_req_path(meta: dict) -> Path:
    return script_dir(meta["id"]) / "requirements.txt"

def script_code(meta: dict) -> str:
    p = script_main_path(meta)
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except OSError:
        return ""

def refresh_status(meta: dict) -> dict:
    """Recalcule le statut réel depuis le process live ou le PID persisté."""
    lp = get_live(meta["id"])
    if lp and lp.is_alive():
        meta["status"] = "running"
        meta["detached"] = False
        return meta
    pid = meta.get("pid")
    if pid and is_pid_alive(pid):
        meta["status"] = "running"
        meta["detached"] = True  # tourne mais plus pilotable (redémarrage serveur)
        return meta
    if meta.get("status") == "running":
        meta["status"] = "stopped"
        meta["stopped_at"] = meta.get("stopped_at") or utcnow_iso()
        meta["pid"] = None
        meta["detached"] = False
    return meta

# --- démarrage / arrêt ---

def on_script_exit(script_id: str, info: dict):
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            unregister(script_id)
            return
        m["status"] = "error" if info.get("reason") == "crash" else "stopped"
        m["last_exit"] = info.get("reason")
        m["exit_code"] = info.get("exit_code")
        m["stopped_at"] = utcnow_iso()
        m["duration_s"] = info.get("duration_s")
        m["pid"] = None
        m["detached"] = False
        if info.get("qa"):
            m["qa_history"] = info["qa"][-30:]
        if info.get("diagnosis"):
            m["last_diagnosis"] = info["diagnosis"]
        else:
            m["last_diagnosis"] = None
        runs = m.get("runs") or []
        runs.append({"started_at": m.get("started_at"), "ended_at": utcnow_iso(),
                     "exit_code": info.get("exit_code"), "reason": info.get("reason"),
                     "duration_s": info.get("duration_s"),
                     "answers": info.get("answers_sent", 0)})
        m["runs"] = runs[-MAX_RUNS:]
        save_scripts_meta(meta)
    unregister(script_id)
    name = m.get("name", script_id)
    if info.get("reason") == "success":
        log_activity("✅", f"« {name} » terminé avec succès ({info.get('duration_s', 0):.0f}s)", script_id)
    elif info.get("reason") == "crash":
        d = info.get("diagnosis") or {}
        log_activity("❌", f"« {name} » a crashé : {d.get('exception', 'erreur')}", script_id)
    else:
        log_activity("⏹", f"« {name} » arrêté", script_id)

def start_script(meta: dict, answers: list | None = None,
                 extra_env: dict | None = None) -> tuple[bool, str]:
    sid = meta["id"]
    if get_live(sid) and get_live(sid).is_alive():
        return False, "Script déjà en cours d'exécution"
    if meta.get("pid") and is_pid_alive(meta["pid"]):
        return False, "Un ancien process tourne encore (détaché) — redémarre le service si besoin"

    main_path = script_main_path(meta)
    if not main_path.exists():
        meta["status"] = "error"
        return False, f"Fichier introuvable : {main_path.name}"

    # Vérification syntaxe AVANT de lancer (zéro erreur surprise)
    code = script_code(meta)
    try:
        compile(code, main_path.name, "exec")
    except SyntaxError as e:
        meta["status"] = "error"
        return False, f"⛔ Erreur de syntaxe ligne {e.lineno} : {e.msg}"

    env = os.environ.copy()
    user_env = meta.get("env") or {}
    if isinstance(user_env, dict):
        for k, v in user_env.items():
            if k:
                env[str(k)] = str(v)
    if extra_env:
        for k, v in extra_env.items():
            env[str(k)] = str(v)
    env["PYRUNNER_SCRIPT_ID"] = sid
    env["PYRUNNER_SCRIPT_NAME"] = meta.get("name", sid)
    env["PYTHONUNBUFFERED"] = "1"

    argv = [sys.executable, "-u", str(main_path)]
    raw_args = (meta.get("args") or "").strip()
    if raw_args:
        try:
            argv += shlex.split(raw_args)
        except ValueError:
            argv += raw_args.split()

    # Réponses pré-remplies : que des strings propres
    clean_answers = []
    for a in (answers or []):
        s = str(a if not isinstance(a, dict) else a.get("answer", ""))
        if s != "":
            clean_answers.append(s[:2000])

    lp = LiveProcess(sid, script_dir(sid), on_exit=on_script_exit)
    try:
        pid = lp.spawn(argv, env, auto_answers=clean_answers)
    except Exception as e:
        meta["status"] = "error"
        return False, f"Échec démarrage : {e}"

    register(lp)
    time.sleep(0.5)
    rc = lp.proc.poll()
    if rc is not None and rc != 0:
        # Crash immédiat : diagnostiquer depuis les logs (META_LOCK est déjà
        # acquis par l'appelant, donc on ne touche pas au waiter ici — il
        # finalisera le meta dès qu'on relâchera le verrou).
        diag = {}
        for _ in range(10):
            time.sleep(0.3)
            tail = read_jsonl_tail_text(jsonl_path(script_dir(sid)))
            if "Traceback" in tail or "Error" in tail:
                try:
                    diag = diagnose_crash(tail, rc) or {}
                except Exception:
                    diag = {}
                break
        meta["status"] = "error"
        meta["exit_code"] = rc
        meta["stopped_at"] = utcnow_iso()
        meta["pid"] = None
        if diag:
            meta["last_diagnosis"] = diag
        if diag.get("exception"):
            return False, f"❌ Crash immédiat : {diag['exception']}: {diag.get('message','')[:120]}"
        return False, f"Le script s'est arrêté immédiatement (code {rc}) — voir la console"

    # NOTE : start_script est toujours appelé avec META_LOCK déjà acquis.
    meta["pid"] = pid
    meta["status"] = "running"
    meta["started_at"] = utcnow_iso()
    meta["stopped_at"] = None
    meta["exit_code"] = None
    meta["last_diagnosis"] = None
    meta["detached"] = False
    if clean_answers:
        meta["pending_answers"] = len(clean_answers)
    log_activity("▶", f"« {meta.get('name')} » démarré" +
                 (f" ({len(clean_answers)} réponse(s) auto)" if clean_answers else ""), sid)
    return True, f"Démarré (PID {pid})"

def stop_script(meta: dict) -> tuple[bool, str]:
    lp = get_live(meta["id"])
    if lp and lp.is_alive():
        lp.stop()
        unregister(meta["id"])
        # le waiter finalise le reste ; on met à jour vite pour l'UI
        meta["status"] = "stopped"
        meta["stopped_at"] = utcnow_iso()
        meta["pid"] = None
        return True, "Arrêté"
    pid = meta.get("pid")
    if pid and is_pid_alive(pid):
        try:
            import signal as _sig
            os.kill(int(pid), _sig.SIGTERM)
        except Exception:
            pass
        meta["status"] = "stopped"
        meta["pid"] = None
        return True, "Ancien process stoppé"
    meta["status"] = "stopped"
    meta["pid"] = None
    return True, "Déjà arrêté"

# --- webhook store ---

def load_webhook_config() -> dict:
    cfg = _read_json(WEBHOOK_CONFIG_FILE, None)
    if isinstance(cfg, dict) and cfg.get("token"):
        return cfg
    cfg = {"token": secrets.token_urlsafe(24), "created_at": utcnow_iso(),
           "linked_script_id": None, "auto_run": False,
           "restart_if_running": True, "pass_payload": True}
    _write_json(WEBHOOK_CONFIG_FILE, cfg)
    return cfg

def save_webhook_config(cfg: dict) -> None:
    _write_json(WEBHOOK_CONFIG_FILE, cfg)

def append_webhook_log(entry: dict) -> None:
    try:
        with open(WEBHOOK_LOGS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    try:
        with open(WEBHOOK_LOGS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_WEBHOOK_LOGS:
            with open(WEBHOOK_LOGS_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_WEBHOOK_LOGS:])
    except OSError:
        pass

def read_webhook_logs(limit: int = 100) -> list:
    if not WEBHOOK_LOGS_FILE.exists():
        return []
    try:
        with open(WEBHOOK_LOGS_FILE, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
    except OSError:
        return []
    out = []
    for line in lines[-limit:][::-1]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out

def webhook_stats() -> dict:
    logs = read_webhook_logs(MAX_WEBHOOK_LOGS)
    now = time.time()
    last_24h, per_hour = 0, [0] * 24
    for e in logs:
        try:
            ts = datetime.fromisoformat(e.get("time", "")).timestamp()
            age_h = (now - ts) / 3600
            if age_h < 24:
                last_24h += 1
                per_hour[min(23, int(age_h))] += 1
        except Exception:
            continue
    return {"total": len(logs), "last_24h": last_24h,
            "last_call": logs[0] if logs else None,
            "per_hour": list(reversed(per_hour))}  # plus ancien -> now

# ------------------------------------------------------------ pages ---

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if is_setup_done():
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        confirm = (request.form.get("confirm") or "").strip()
        if len(code) < 6:
            error = "Le code secret doit contenir au moins 6 caractères."
        elif code != confirm:
            error = "Les deux codes ne correspondent pas."
        else:
            set_code(code)
            session.permanent = True
            session["authenticated"] = True
            log_activity("🔐", "Console verrouillée avec un code secret")
            return redirect(url_for("dashboard"))
    return render_template("setup.html", error=error)

@app.route("/login", methods=["GET", "POST"])
def login():
    if not is_setup_done():
        return redirect(url_for("setup"))
    if session.get("authenticated"):
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        if verify_code(code):
            session.permanent = True
            session["authenticated"] = True
            nxt = request.args.get("next") or url_for("dashboard")
            if not nxt.startswith("/"):
                nxt = url_for("dashboard")
            return redirect(nxt)
        error = "Code incorrect."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
def dashboard():
    if not is_setup_done():
        return redirect(url_for("setup"))
    if not session.get("authenticated"):
        return redirect(url_for("login"))
    cfg = load_webhook_config()
    webhook_url = url_for("webhook_receiver", token=cfg["token"], _external=True)
    return render_template("dashboard.html", webhook_url=webhook_url)

# -------------------------------------------------------------- API ---

@app.route("/api/health")
def api_health():
    return jsonify({"ok": True, "time": utcnow_iso(),
                    "setup_done": is_setup_done(),
                    "uptime_s": int(time.time() - BOOT_TIME)})

def script_summary(m: dict) -> dict:
    refresh_status(m)
    lp = get_live(m["id"])
    runs = m.get("runs") or []
    ok_runs = sum(1 for r in runs if r.get("reason") == "success")
    return {
        "id": m["id"], "name": m.get("name", m["id"]),
        "filename": m.get("filename"), "status": m.get("status", "stopped"),
        "detached": bool(m.get("detached")), "pid": m.get("pid"),
        "args": m.get("args", ""), "created_at": m.get("created_at"),
        "started_at": m.get("started_at"), "stopped_at": m.get("stopped_at"),
        "exit_code": m.get("exit_code"), "duration_s": m.get("duration_s"),
        "has_requirements": script_req_path(m).exists(),
        "runs_count": len(runs), "success_count": ok_runs,
        "has_diagnosis": bool(m.get("last_diagnosis")),
        "waiting": bool(lp and lp.is_waiting()),
        "answers_sent": lp.answers_sent if lp else 0,
        "has_qa": bool(m.get("qa_history")),
    }

@app.route("/api/overview")
@login_required
def api_overview():
    with META_LOCK:
        meta = load_scripts_meta()
        scripts = [script_summary(m) for m in meta.values()]
        save_scripts_meta(meta)
    running = [s for s in scripts if s["status"] == "running"]
    total_runs = sum(s["runs_count"] for s in scripts)
    total_ok = sum(s["success_count"] for s in scripts)
    cfg = load_webhook_config()
    stats = webhook_stats()
    return jsonify({
        "ok": True,
        "stats": {
            "scripts_total": len(scripts), "running": len(running),
            "total_runs": total_runs,
            "success_rate": round(100 * total_ok / total_runs) if total_runs else None,
            "webhooks_total": stats["total"], "webhooks_24h": stats["last_24h"],
            "uptime_s": int(time.time() - BOOT_TIME),
        },
        "running": running,
        "scripts": sorted(scripts, key=lambda s: s.get("created_at") or "", reverse=True),
        "activity": read_activity(12),
        "webhook": {"url": url_for("webhook_receiver", token=cfg["token"], _external=True),
                    "per_hour": stats["per_hour"], "last_call": stats["last_call"]},
    })

@app.route("/api/status")
@login_required
def api_status():
    with META_LOCK:
        meta = load_scripts_meta()
        scripts = [script_summary(m) for m in meta.values()]
        save_scripts_meta(meta)
    scripts.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    cfg = load_webhook_config()
    stats = webhook_stats()
    return jsonify({
        "ok": True, "scripts": scripts,
        "counts": {"total": len(scripts),
                   "running": sum(1 for s in scripts if s["status"] == "running")},
        "webhook": {"url": url_for("webhook_receiver", token=cfg["token"], _external=True),
                    "linked_script_id": cfg.get("linked_script_id"),
                    "auto_run": cfg.get("auto_run"), "stats": stats},
        "activity": read_activity(8),
    })

# ------------------------- scripts : création / upload / analyse ---

TEMPLATES = {
    "blank": ('Script vierge', 'print("Hello !")\n'),
    "interactif": ('Menu interactif', '''print("=" * 30)
print("  🤖 MON ASSISTANT")
print("=" * 30)
print("1 - Dire bonjour")
print("2 - Calculer un double")
print("3 - Quitter")

choix = input("Ton choix (1-3) : ")

if choix == "1":
    prenom = input("Ton prénom : ")
    print(f"Salut {prenom} ! 👋")
elif choix == "2":
    n = input("Un nombre : ")
    print(f"Le double de {n} = {int(n) * 2}")
else:
    print("À bientôt ! 👋")
'''),
    "webhook": ('Bot webhook', '''"""Reçoit les données du webhook via WEBHOOK_PAYLOAD."""
import json
import os
import time
from datetime import datetime

print("🤖 Bot démarré, en attente de signaux...", flush=True)

payload_raw = os.environ.get("WEBHOOK_PAYLOAD")
if payload_raw:
    print(f"🔔 Déclenché par webhook à {os.environ.get('WEBHOOK_TIME')}", flush=True)
    try:
        data = json.loads(payload_raw)
        print(f"📦 Signal : {data}", flush=True)
        # 👉 Ta logique ici :
        # if data.get("signal") == "BUY": ...
    except json.JSONDecodeError:
        print(f"📦 Brut : {payload_raw[:500]}", flush=True)

while True:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 💓 en vie", flush=True)
    time.sleep(60)
'''),
    "boucle": ('Tâche en boucle', '''"""Se répète toutes les X secondes."""
import time
from datetime import datetime

while True:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚙️ exécution...", flush=True)
    # 👉 Ton code ici
    time.sleep(30)
'''),
}

@app.route("/api/scripts/create", methods=["POST"])
@login_required
def api_create():
    data = request.get_json(force=True, silent=True) or {}
    name = re.sub(r"[^\w\s\-àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]",
                  "", str(data.get("name") or "Nouveau script").strip())[:60] or "Nouveau script"
    template = str(data.get("template") or "blank")
    title, code = TEMPLATES.get(template, TEMPLATES["blank"])
    script_id = secrets.token_hex(6)
    filename = "main.py"
    sdir = script_dir(script_id)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / filename).write_text(code, encoding="utf-8")
    with META_LOCK:
        meta = load_scripts_meta()
        meta[script_id] = {"id": script_id, "name": name, "filename": filename,
                           "args": "", "env": {}, "status": "stopped", "pid": None,
                           "created_at": utcnow_iso(), "updated_at": utcnow_iso(),
                           "started_at": None, "stopped_at": None,
                           "exit_code": None, "runs": []}
        save_scripts_meta(meta)
    log_activity("📝", f"« {name} » créé (modèle : {title})", script_id)
    return jsonify({"ok": True, "id": script_id,
                    "analysis": analyze_source(code)})

@app.route("/api/scripts/upload", methods=["POST"])
@login_required
def api_upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Aucun fichier reçu"}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Nom de fichier vide"}), 400
    filename = secure_filename(f.filename)
    if not filename.endswith(".py"):
        return jsonify({"ok": False, "error": "Seuls les fichiers .py sont acceptés"}), 400
    try:
        content = f.read()
    except Exception as e:
        return jsonify({"ok": False, "error": f"Lecture impossible : {e}"}), 400
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        return jsonify({"ok": False, "error": f"Fichier trop gros (max {MAX_UPLOAD_MB} Mo)"}), 400
    try:
        code = content.decode("utf-8")
    except UnicodeDecodeError:
        return jsonify({"ok": False, "error": "Le fichier doit être encodé en UTF-8"}), 400

    name = (request.form.get("name") or "").strip() or Path(filename).stem
    name = re.sub(r"[^\w\s\-àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]", "", name).strip()[:60] or Path(filename).stem
    args = (request.form.get("args") or "").strip()[:500]
    env = {}
    for line in (request.form.get("env_text") or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip():
            env[k.strip()[:100]] = v.strip()[:2000]

    script_id = secrets.token_hex(6)
    sdir = script_dir(script_id)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / filename).write_bytes(content)

    req = request.files.get("requirements")
    if req and req.filename:
        try:
            (sdir / "requirements.txt").write_text(
                req.read().decode("utf-8", errors="replace")[:50000], encoding="utf-8")
        except Exception:
            pass
    req_text = (request.form.get("requirements_text") or "").strip()
    if req_text and not (sdir / "requirements.txt").exists():
        (sdir / "requirements.txt").write_text(req_text[:50000], encoding="utf-8")

    with META_LOCK:
        meta = load_scripts_meta()
        meta[script_id] = {"id": script_id, "name": name, "filename": filename,
                           "args": args, "env": env, "status": "stopped",
                           "pid": None, "created_at": utcnow_iso(),
                           "updated_at": utcnow_iso(), "started_at": None,
                           "stopped_at": None, "exit_code": None, "runs": []}
        save_scripts_meta(meta)
    log_activity("⬆️", f"« {name} » importé ({len(code.splitlines())} lignes)", script_id)

    analysis = analyze_source(code)
    # Auto-install des dépendances si demandé
    messages = []
    if request.form.get("install_deps") == "on" and (sdir / "requirements.txt").exists():
        ok, msg = pip_install(script_id)
        messages.append(("✅ " if ok else "⚠️ ") + msg)
    return jsonify({"ok": True, "id": script_id, "name": name,
                    "analysis": analysis, "messages": messages})

@app.route("/api/scripts/<script_id>/analyze")
@login_required
def api_analyze(script_id):
    with META_LOCK:
        m = load_scripts_meta().get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Script introuvable"}), 404
    return jsonify({"ok": True, "analysis": analyze_source(script_code(m))})

# ------------------------- scripts : détail / config ---

@app.route("/api/scripts/<script_id>", methods=["GET"])
@login_required
def api_script_detail(script_id):
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Script introuvable"}), 404
        summ = script_summary(m)
        save_scripts_meta(meta)
    rp = script_req_path(m)
    try:
        requirements = rp.read_text(encoding="utf-8") if rp.exists() else ""
    except OSError:
        requirements = ""
    lp_p = log_path(script_dir(script_id))
    return jsonify({"ok": True, "script": {
        **summ,
        "env": m.get("env") or {},
        "code": script_code(m),
        "requirements": requirements,
        "log_size": lp_p.stat().st_size if lp_p.exists() else 0,
        "runs": list(reversed(m.get("runs") or [])),
        "diagnosis": m.get("last_diagnosis"),
        "qa_history": m.get("qa_history") or [],
    }})

@app.route("/api/scripts/<script_id>", methods=["PUT"])
@login_required
def api_script_update(script_id):
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Script introuvable"}), 404
        data = request.get_json(force=True, silent=True) or {}
        if "name" in data and str(data["name"]).strip():
            m["name"] = re.sub(r"[^\w\s\-àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]",
                                "", str(data["name"]).strip())[:60]
        if "args" in data:
            m["args"] = str(data["args"] or "")[:500]
        if "env" in data and isinstance(data["env"], dict):
            m["env"] = {str(k)[:100]: str(v)[:2000]
                        for k, v in data["env"].items() if str(k).strip()}
        if "code" in data and isinstance(data["code"], str):
            if len(data["code"]) > 2 * 1024 * 1024:
                return jsonify({"ok": False, "error": "Code trop volumineux (max 2 Mo)"}), 400
            try:
                compile(data["code"], m.get("filename", "main.py"), "exec")
            except SyntaxError as e:
                return jsonify({"ok": False, "error": f"⛔ Syntaxe invalide ligne {e.lineno} : {e.msg} — non enregistré"}), 400
            script_main_path(m).write_text(data["code"], encoding="utf-8")
            m["last_diagnosis"] = None
        if "requirements" in data and isinstance(data["requirements"], str):
            rp = script_req_path(m)
            if data["requirements"].strip():
                rp.write_text(data["requirements"][:50000], encoding="utf-8")
            elif rp.exists():
                rp.unlink()
        m["updated_at"] = utcnow_iso()
        save_scripts_meta(meta)
    return jsonify({"ok": True, "analysis": analyze_source(script_code(m))})

@app.route("/api/scripts/<script_id>", methods=["DELETE"])
@login_required
def api_script_delete(script_id):
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Script introuvable"}), 404
        stop_script(m)
        import shutil
        shutil.rmtree(script_dir(script_id), ignore_errors=True)
        meta.pop(script_id, None)
        save_scripts_meta(meta)
        cfg = load_webhook_config()
        if cfg.get("linked_script_id") == script_id:
            cfg["linked_script_id"] = None
            cfg["auto_run"] = False
            save_webhook_config(cfg)
    log_activity("🗑️", f"« {m.get('name')} » supprimé")
    return jsonify({"ok": True})

# ------------------------- scripts : exécution ---

@app.route("/api/scripts/<script_id>/run", methods=["POST"])
@login_required
def api_script_run(script_id):
    data = request.get_json(force=True, silent=True) or {}
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Script introuvable"}), 404
        answers = data.get("answers") or []
        if data.get("reuse_qa") and m.get("qa_history"):
            answers = [q.get("answer", "") for q in m["qa_history"]]
        ok, msg = start_script(m, answers=answers)
        save_scripts_meta(meta)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/scripts/<script_id>/stop", methods=["POST"])
@login_required
def api_script_stop(script_id):
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Script introuvable"}), 404
        was = refresh_status(m).get("status") == "running"
        ok, msg = stop_script(m)
        save_scripts_meta(meta)
    if was:
        log_activity("⏹", f"« {m.get('name')} » arrêté manuellement", script_id)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/scripts/<script_id>/restart", methods=["POST"])
@login_required
def api_script_restart(script_id):
    data = request.get_json(force=True, silent=True) or {}
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Script introuvable"}), 404
        stop_script(m)
        save_scripts_meta(meta)
    time.sleep(0.8)
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        answers = data.get("answers")
        if answers is None and data.get("reuse_qa", True) and m.get("qa_history"):
            answers = [q.get("answer", "") for q in m["qa_history"]]
        ok, msg = start_script(m, answers=answers or [])
        save_scripts_meta(meta)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/scripts/<script_id>/input", methods=["POST"])
@login_required
def api_script_input(script_id):
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get("text", ""))
    if len(text) > 5000:
        return jsonify({"ok": False, "error": "Réponse trop longue"}), 400
    lp = get_live(script_id)
    if not lp or not lp.is_alive():
        return jsonify({"ok": False, "error": "Le script n'est plus en cours"}), 400
    ok, msg = lp.send_input(text)
    # persiste le Q&A au fil de l'eau (pour "rejouer")
    if ok:
        with META_LOCK:
            meta = load_scripts_meta()
            m = meta.get(script_id)
            if m:
                m["qa_history"] = lp.qa[-30:]
                save_scripts_meta(meta)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/scripts/<script_id>/console")
@login_required
def api_console(script_id):
    try:
        since = max(0, int(request.args.get("since", 0)))
    except ValueError:
        since = 0
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Script introuvable"}), 404
        refresh_status(m)
        save_scripts_meta(meta)
    lines, next_seq = read_jsonl_since(jsonl_path(script_dir(script_id)), since)
    lp = get_live(script_id)
    waiting, prompt, options, answers_sent = False, "", [], 0
    if lp and lp.is_alive():
        try:
            waiting = lp.is_waiting()
        except Exception:
            waiting = False
        prompt = lp.current_prompt() if waiting else ""
        try:
            options = lp.prompt_options() if waiting else []
        except Exception:
            options = []
        answers_sent = lp.answers_sent
    return jsonify({
        "ok": True, "lines": lines, "next_seq": next_seq,
        "status": m.get("status"), "detached": bool(m.get("detached")),
        "pid": m.get("pid"), "exit_code": m.get("exit_code"),
        "waiting": waiting, "prompt": prompt, "options": options,
        "answers_sent": answers_sent,
        "diagnosis": m.get("last_diagnosis"),
    })

# ------------------------- dépendances ---

def _pip_emit(script_id: str, text: str, level="info"):
    lp = get_live(script_id)
    if lp and lp.is_alive():
        lp.emit("sys", text, level)
    else:
        # journalise quand même dans les logs persistants
        jp = jsonl_path(script_dir(script_id))
        try:
            from runner import last_seq as _ls
            obj = {"seq": _ls(jp), "t": utcnow_iso(), "type": "sys",
                   "level": level, "text": text[:5000]}
            with open(jp, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        except OSError:
            pass

def pip_install(script_id: str, package: str | None = None) -> tuple[bool, str]:
    with META_LOCK:
        m = load_scripts_meta().get(script_id)
    if not m:
        return False, "Script introuvable"
    if package:
        cmd = [sys.executable, "-m", "pip", "install", package]
        _pip_emit(script_id, f"📦 Installation de « {package} »…")
    else:
        rp = script_req_path(m)
        if not rp.exists():
            return False, "Aucun requirements.txt"
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(rp)]
        _pip_emit(script_id, "📦 Installation des dépendances (requirements.txt)…")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if proc.returncode != 0 and "externally-managed-environment" in out:
            # Python système verrouillé (Debian/PEP 668) : on force l'install
            _pip_emit(script_id, "🔓 Environnement Python verrouillé — nouvel essai forcé…")
            cmd = cmd[:4] + ["--break-system-packages"] + cmd[4:]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if out:
            _pip_emit(script_id, out[-4000:], "info")
        if proc.returncode == 0:
            _pip_emit(script_id, "✅ Installation réussie", "success")
            return True, "Installation réussie ✅"
        _pip_emit(script_id, f"❌ pip a échoué (code {proc.returncode})", "error")
        return False, f"pip a échoué (code {proc.returncode}) — voir la console"
    except subprocess.TimeoutExpired:
        return False, "Timeout pip (5 min)"
    except Exception as e:
        return False, f"Erreur pip : {e}"

@app.route("/api/scripts/<script_id>/install-deps", methods=["POST"])
@login_required
def api_script_install(script_id):
    ok, msg = pip_install(script_id)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/scripts/<script_id>/fix-install", methods=["POST"])
@login_required
def api_fix_install(script_id):
    data = request.get_json(force=True, silent=True) or {}
    pkg = re.sub(r"[^A-Za-z0-9_\-.\[\]]", "", str(data.get("package", "")))[:80]
    if not pkg:
        return jsonify({"ok": False, "error": "Paquet invalide"}), 400
    ok, msg = pip_install(script_id, pkg)
    # ajoute aussi au requirements.txt pour la prochaine fois
    if ok:
        with META_LOCK:
            m = load_scripts_meta().get(script_id)
        if m:
            rp = script_req_path(m)
            try:
                existing = rp.read_text(encoding="utf-8") if rp.exists() else ""
                if pkg.lower() not in existing.lower():
                    with open(rp, "a", encoding="utf-8") as f:
                        if existing and not existing.endswith("\n"):
                            f.write("\n")
                        f.write(pkg + "\n")
            except OSError:
                pass
    return jsonify({"ok": ok, "message": msg})

# ------------------------- logs legacy / téléchargement ---

@app.route("/api/scripts/<script_id>/logs")
@login_required
def api_script_logs(script_id):
    with META_LOCK:
        m = load_scripts_meta().get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Script introuvable"}), 404
    tail_lines = max(1, min(int(request.args.get("tail", 300)), 2000))
    lp_p = log_path(script_dir(script_id))
    if not lp_p.exists():
        return jsonify({"ok": True, "logs": "(aucun log pour le moment)",
                        "status": m.get("status")})
    try:
        content = lp_p.read_text(encoding="utf-8", errors="replace")
        return jsonify({"ok": True, "logs": "\n".join(content.splitlines()[-tail_lines:]),
                        "status": m.get("status")})
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/scripts/<script_id>/logs/clear", methods=["POST"])
@login_required
def api_script_logs_clear(script_id):
    with META_LOCK:
        m = load_scripts_meta().get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Script introuvable"}), 404
    try:
        jp = jsonl_path(script_dir(script_id))
        if jp.exists():
            jp.unlink()
        log_path(script_dir(script_id)).write_text(
            f"[{utcnow_iso()}] 🧹 Console effacée\n", encoding="utf-8")
        lp = get_live(script_id)
        if lp:
            lp.seq = 0
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})

@app.route("/api/scripts/<script_id>/logs/download")
@login_required
def api_script_logs_download(script_id):
    with META_LOCK:
        m = load_scripts_meta().get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Script introuvable"}), 404
    lp_p = log_path(script_dir(script_id))
    if not lp_p.exists():
        return jsonify({"ok": False, "error": "Aucun log"}), 404
    return send_file(lp_p, as_attachment=True,
                     download_name=f"{m.get('name', 'script')}-console.log")

# ------------------------------------------------- webhook inbound ---

@app.route("/webhook/<token>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def webhook_receiver(token):
    cfg = load_webhook_config()
    if not secrets.compare_digest(token or "", cfg.get("token", "")):
        return jsonify({"ok": False, "error": "Token invalide"}), 404
    started = time.time()
    raw_body = request.get_data() or b""
    body_text = raw_body[:MAX_WEBHOOK_BODY].decode("utf-8", errors="replace")
    body_json = None
    if body_text.strip():
        try:
            body_json = json.loads(body_text)
        except Exception:
            body_json = None
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or request.remote_addr or "?")
    entry = {
        "id": secrets.token_hex(4), "time": utcnow_iso(), "method": request.method,
        "path": request.path, "ip": ip,
        "user_agent": request.headers.get("User-Agent", "")[:300],
        "content_type": request.headers.get("Content-Type", "")[:200],
        "query": dict(request.args),
        "headers": {k: v[:500] for k, v in request.headers.items()
                    if k.lower() not in ("cookie", "authorization")},
        "body": body_json if body_json is not None else body_text[:5000],
        "body_is_json": body_json is not None,
        "body_size": len(raw_body), "triggered_script": None,
    }
    action_msg = None
    if cfg.get("auto_run") and cfg.get("linked_script_id"):
        with META_LOCK:
            meta = load_scripts_meta()
            m = meta.get(cfg["linked_script_id"])
            if m:
                refresh_status(m)
                already = m.get("status") == "running"
                extra = {}
                if cfg.get("pass_payload", True):
                    extra["WEBHOOK_PAYLOAD"] = body_text[:50000]
                    extra["WEBHOOK_EVENT_ID"] = entry["id"]
                    extra["WEBHOOK_TIME"] = entry["time"]
                    extra["WEBHOOK_METHOD"] = request.method
                replay = [q.get("answer", "") for q in (m.get("qa_history") or [])]
                if already and cfg.get("restart_if_running", True):
                    stop_script(m)
                    save_scripts_meta(meta)
                    time.sleep(0.6)
                    meta = load_scripts_meta()
                    m = meta.get(cfg["linked_script_id"])
                    ok, msg = start_script(m, answers=replay, extra_env=extra)
                    action_msg = f"restart: {msg}"
                    entry["triggered_script"] = {"id": m["id"], "name": m.get("name"),
                                                 "action": "restart", "ok": ok, "message": msg}
                elif already:
                    action_msg = "script déjà en cours (relance désactivée)"
                    entry["triggered_script"] = {"id": m["id"], "name": m.get("name"),
                                                 "action": "skipped", "ok": True, "message": action_msg}
                else:
                    ok, msg = start_script(m, answers=replay, extra_env=extra)
                    action_msg = f"run: {msg}"
                    entry["triggered_script"] = {"id": m["id"], "name": m.get("name"),
                                                 "action": "run", "ok": ok, "message": msg}
                save_scripts_meta(meta)
            else:
                entry["triggered_script"] = {"error": "Script lié introuvable"}
    entry["duration_ms"] = round((time.time() - started) * 1000, 1)
    append_webhook_log(entry)
    log_activity("🔔", f"Webhook {request.method} reçu ({entry['body_size']} octets)" +
                 (f" → {action_msg}" if action_msg else ""))
    return jsonify({"ok": True, "event_id": entry["id"],
                    "triggered": entry["triggered_script"],
                    "message": action_msg or "Webhook reçu et journalisé"}), 200

@app.route("/api/webhooks/logs")
@login_required
def api_webhook_logs():
    limit = max(1, min(int(request.args.get("limit", 100)), MAX_WEBHOOK_LOGS))
    return jsonify({"ok": True, "logs": read_webhook_logs(limit),
                    "stats": webhook_stats()})

@app.route("/api/webhooks/logs", methods=["DELETE"])
@login_required
def api_webhook_logs_clear():
    try:
        if WEBHOOK_LOGS_FILE.exists():
            WEBHOOK_LOGS_FILE.unlink()
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})

@app.route("/api/webhooks/config", methods=["GET"])
@login_required
def api_webhook_config():
    cfg = load_webhook_config()
    return jsonify({"ok": True, "config": {
        "url": url_for("webhook_receiver", token=cfg["token"], _external=True),
        "linked_script_id": cfg.get("linked_script_id"),
        "auto_run": cfg.get("auto_run"),
        "restart_if_running": cfg.get("restart_if_running"),
        "pass_payload": cfg.get("pass_payload"),
        "created_at": cfg.get("created_at")}})

@app.route("/api/webhooks/config", methods=["PUT"])
@login_required
def api_webhook_config_update():
    cfg = load_webhook_config()
    data = request.get_json(force=True, silent=True) or {}
    if "linked_script_id" in data:
        lid = data["linked_script_id"]
        if lid:
            with META_LOCK:
                meta = load_scripts_meta()
            if lid not in meta:
                return jsonify({"ok": False, "error": "Script lié introuvable"}), 400
        cfg["linked_script_id"] = lid or None
    for key in ("auto_run", "restart_if_running", "pass_payload"):
        if key in data:
            cfg[key] = bool(data[key])
    if cfg.get("auto_run") and not cfg.get("linked_script_id"):
        return jsonify({"ok": False, "error": "Choisis un script à déclencher avant d'activer l'exécution auto"}), 400
    save_webhook_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/webhooks/regenerate", methods=["POST"])
@login_required
def api_webhook_regenerate():
    cfg = load_webhook_config()
    cfg["token"] = secrets.token_urlsafe(24)
    cfg["created_at"] = utcnow_iso()
    save_webhook_config(cfg)
    log_activity("🔄", "URL webhook régénérée")
    return jsonify({"ok": True, "url": url_for("webhook_receiver", token=cfg["token"], _external=True)})

@app.route("/api/webhooks/test", methods=["POST"])
@login_required
def api_webhook_test():
    import requests as rq
    cfg = load_webhook_config()
    url = url_for("webhook_receiver", token=cfg["token"], _external=True)
    try:
        r = rq.post(url, json={"test": True,
                               "message": "Appel de test depuis le dashboard",
                               "time": utcnow_iso()}, timeout=15)
        return jsonify({"ok": r.ok, "status": r.status_code,
                        "response": r.text[:2000]})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Envoi impossible : {e}"}), 502

# --- paramètres ---

@app.route("/api/change-code", methods=["POST"])
@login_required
def api_change_code():
    if ENV_SECRET_CODE:
        return jsonify({"ok": False, "error": "SECRET_CODE est défini via variable d'environnement — modifie-le sur Render"}), 400
    data = request.get_json(force=True, silent=True) or {}
    current = (data.get("current") or "").strip()
    new = (data.get("new") or "").strip()
    if not verify_code(current):
        return jsonify({"ok": False, "error": "Code actuel incorrect"}), 403
    if len(new) < 6:
        return jsonify({"ok": False, "error": "Le nouveau code doit contenir au moins 6 caractères"}), 400
    set_code(new)
    log_activity("🔐", "Code secret modifié")
    return jsonify({"ok": True, "message": "Code secret mis à jour"})

# ------------------------------------------------------- error pages ---

@app.errorhandler(413)
def too_large(_e):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": f"Fichier trop volumineux (max {MAX_UPLOAD_MB} Mo)"}), 413
    return "Fichier trop volumineux", 413

@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Route introuvable"}), 404
    return redirect(url_for("dashboard"))

# -------------------------------------------------------------- main ---

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
