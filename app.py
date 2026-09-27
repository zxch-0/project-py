"""
zach-runner — Console privee pour executer des projets et scripts sur Render.

- Upload de fichiers (.py, .js, .sh...) ou de projets complets (.zip, .tar.gz, .rar, .7z)
- Analyse intelligente : questions, menus a choix, dependances, risques
- Console interactive : reponses pre-remplies + reponses en direct (stdin)
- Logs structures, diagnostic de crash, webhooks avec historique detaille
- Acces protege par code secret defini a la premiere visite
"""
import json
import os
import re
import secrets
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

from analyzer import analyze_file
from projects import (
    MAX_ARCHIVE_MB, archive_kind, extract_archive, scan_project,
)
from runtimes import (
    SINGLE_FILE_EXTS, all_runtimes_status, build_argv, detect_runtime,
    npm_bin, runtime_available, syntax_check,
)
from runner import (
    LiveProcess, get_live, is_pid_alive, jsonl_path, log_path,
    read_jsonl_since, register, unregister, utcnow_iso,
)

# ---------------------------------------------------------------- config ---

BASE_DIR = Path(__file__).resolve().parent

# Outils vendores par le build Render (vendor/bin) : visibles via PATH
# pour rarfile (unrar) et tout sous-processus.
_VENDOR_BIN = BASE_DIR / "vendor" / "bin"
if (_VENDOR_BIN / "unrar").exists():
    os.environ["PATH"] = str(_VENDOR_BIN) + os.pathsep + os.environ.get("PATH", "")

DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
SCRIPTS_ROOT = DATA_DIR / "scripts"

AUTH_FILE = DATA_DIR / "auth.json"
APP_SECRET_FILE = DATA_DIR / "app_secret.key"
SCRIPTS_META_FILE = DATA_DIR / "scripts.json"
WEBHOOK_CONFIG_FILE = DATA_DIR / "webhook.json"
WEBHOOK_LOGS_FILE = DATA_DIR / "webhook_logs.jsonl"
ACTIVITY_FILE = DATA_DIR / "activity.jsonl"

MAX_FILE_MB = int(os.environ.get("MAX_FILE_MB", "16"))
MAX_WEBHOOK_LOGS = 500
MAX_WEBHOOK_BODY = 20 * 1024
MAX_RUNS = 20
MAX_ACTIVITY = 200
MAX_EDITOR_BYTES = 200 * 1024

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
app.config["MAX_CONTENT_LENGTH"] = (MAX_ARCHIVE_MB + 32) * 1024 * 1024
app.permanent_session_lifetime = 60 * 60 * 24 * 30
# Cookie de session : doit survivre dans l'apercu integre (contexte tiers, HTTPS).
# SameSite=None + Secure exige par les navigateurs ; Partitioned (CHIPS) pour les
# navigateurs qui bloquent les cookies tiers. Production servie en HTTPS (Render).
app.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_PARTITIONED=True,
    SESSION_COOKIE_HTTPONLY=True,
)

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

# --- activite (icones = cles, rendues en SVG cote frontend) ---

def log_activity(icon: str, text: str, script_id: str | None = None):
    try:
        with open(ACTIVITY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": utcnow_iso(), "icon": icon, "text": text,
                                "script_id": script_id}, ensure_ascii=False) + "\n")
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
                return jsonify({"ok": False, "error": "Non authentifie"}), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapper

# --- stockage scripts / projets ---

def load_scripts_meta() -> dict:
    data = _read_json(SCRIPTS_META_FILE, {})
    return data if isinstance(data, dict) else {}

def save_scripts_meta(meta: dict) -> None:
    _write_json(SCRIPTS_META_FILE, meta)

def script_dir(script_id: str) -> Path:
    return SCRIPTS_ROOT / script_id

def project_root(meta: dict) -> Path:
    if meta.get("kind") == "project":
        return script_dir(meta["id"]) / "project"
    return script_dir(meta["id"])

def entry_rel(meta: dict) -> str | None:
    if meta.get("kind") == "project":
        return meta.get("entry")
    return meta.get("filename")

def entry_abs(meta: dict) -> Path | None:
    rel = entry_rel(meta)
    if not rel:
        return None
    return project_root(meta) / rel

def project_file(root: Path, rel: str) -> Path | None:
    """Resout un chemin relatif en restant confine a root."""
    if not rel or not isinstance(rel, str):
        return None
    rp = Path(rel)
    if rp.is_absolute() or ".." in rp.parts:
        return None
    try:
        p = (root / rel).resolve()
        p.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return p

def effective_runtime(meta: dict) -> str | None:
    rt = (meta.get("runtime") or "auto").strip().lower()
    if rt and rt != "auto":
        return rt if rt in ("python", "node", "bash", "custom") else None
    return detect_runtime(entry_rel(meta) or "")

def entry_code(meta: dict) -> str:
    p = entry_abs(meta)
    try:
        if p and p.exists() and p.stat().st_size <= MAX_EDITOR_BYTES:
            return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        pass
    return ""

def project_local_modules(meta: dict) -> set:
    """Modules locaux d'un projet (dossiers + .py de premier niveau)."""
    mods = set()
    if meta.get("kind") != "project":
        return mods
    try:
        for child in project_root(meta).iterdir():
            if child.name.startswith("."):
                continue
            if child.is_dir():
                mods.add(child.name)
            elif child.suffix == ".py":
                mods.add(child.stem)
    except OSError:
        pass
    return mods

def analyze_entry(meta: dict):
    """Analyse le point d'entree (avec modules locaux pour les projets)."""
    rel = entry_rel(meta) or ""
    code = entry_code(meta)
    if not code:
        return None
    local = project_local_modules(meta) or None
    return analyze_file(rel, code, local_modules=local)

def refresh_status(meta: dict) -> dict:
    lp = get_live(meta["id"])
    if lp and lp.is_alive():
        meta["status"] = "running"
        meta["detached"] = False
        return meta
    pid = meta.get("pid")
    if pid and is_pid_alive(pid):
        meta["status"] = "running"
        meta["detached"] = True
        return meta
    if meta.get("status") == "running":
        meta["status"] = "stopped"
        meta["stopped_at"] = meta.get("stopped_at") or utcnow_iso()
        meta["pid"] = None
        meta["detached"] = False
    return meta

# --- demarrage / arret ---

def on_script_exit(script_id: str, info: dict):
    token = info.get("run_token")
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        lp = get_live(script_id)
        if lp is not None and getattr(lp, "run_token", None) == token:
            unregister(script_id)
        if not m:
            return
        if m.get("run_token") != token:
            # Un nouveau run a deja demarre : ne pas ecraser son etat.
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
        m["last_diagnosis"] = info.get("diagnosis")
        runs = m.get("runs") or []
        runs.append({"started_at": m.get("started_at"), "ended_at": utcnow_iso(),
                     "exit_code": info.get("exit_code"), "reason": info.get("reason"),
                     "duration_s": info.get("duration_s"),
                     "answers": info.get("answers_sent", 0)})
        m["runs"] = runs[-MAX_RUNS:]
        save_scripts_meta(meta)
    name = m.get("name", script_id)
    if info.get("reason") == "success":
        log_activity("ok", f"« {name} » termine avec succes ({info.get('duration_s', 0):.0f}s)", script_id)
    elif info.get("reason") == "crash":
        d = info.get("diagnosis") or {}
        log_activity("error", f"« {name} » : echec ({d.get('exception', 'erreur')})", script_id)
    else:
        log_activity("stop", f"« {name} » arrete", script_id)

def start_script(meta: dict, answers: list | None = None,
                 extra_env: dict | None = None) -> tuple[bool, str]:
    """Demarre le script. Appeler avec META_LOCK acquis."""
    sid = meta["id"]
    if get_live(sid) and get_live(sid).is_alive():
        return False, "Deja en cours d'execution"
    if meta.get("pid") and is_pid_alive(meta["pid"]):
        return False, "Un ancien processus tourne encore (mode detache)"

    main_path = entry_abs(meta)
    if not main_path or not main_path.exists():
        meta["status"] = "error"
        return False, "Fichier d'entree introuvable — selectionnez un point d'entree"

    runtime = effective_runtime(meta)
    if not runtime:
        meta["status"] = "error"
        return False, ("Runtime indetermine : choisissez Python, Node.js, Shell "
                       "ou une commande personnalisee")
    ok_rt, rt_info = runtime_available(runtime) if runtime != "custom" else (True, "")
    if not ok_rt:
        meta["status"] = "error"
        return False, rt_info

    ok_syn, syn_msg = syntax_check(runtime, str(main_path),
                                   meta.get("custom_cmd", ""))
    if not ok_syn:
        meta["status"] = "error"
        return False, syn_msg

    argv, err = build_argv(runtime, str(main_path), meta.get("args", ""),
                          meta.get("custom_cmd", ""))
    if argv is None:
        meta["status"] = "error"
        return False, err or "Commande invalide"

    env = os.environ.copy()
    user_env = meta.get("env") or {}
    if isinstance(user_env, dict):
        for k, v in user_env.items():
            if k:
                env[str(k)] = str(v)
    if extra_env:
        for k, v in extra_env.items():
            env[str(k)] = str(v)
    env["ZR_SCRIPT_ID"] = sid
    env["ZR_SCRIPT_NAME"] = meta.get("name", sid)
    env["PYRUNNER_SCRIPT_ID"] = sid  # compat ascendante
    env["PYRUNNER_SCRIPT_NAME"] = meta.get("name", sid)
    env["PYTHONUNBUFFERED"] = "1"

    clean_answers = []
    for a in (answers or []):
        s = str(a if not isinstance(a, dict) else a.get("answer", ""))
        if s != "":
            clean_answers.append(s[:2000])

    token = secrets.token_hex(4)
    lp = LiveProcess(sid, script_dir(sid), on_exit=on_script_exit)
    lp.run_token = token
    try:
        pid = lp.spawn(argv, env, auto_answers=clean_answers)
    except Exception as e:
        meta["status"] = "error"
        return False, f"Echec du demarrage : {e}"

    register(lp)
    time.sleep(0.5)
    rc = lp.proc.poll()
    if rc is not None and rc != 0:
        from analyzer import diagnose_crash
        from runner import read_jsonl_tail_text
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
        meta["run_token"] = token
        meta["exit_code"] = rc
        meta["stopped_at"] = utcnow_iso()
        meta["pid"] = None
        if diag:
            meta["last_diagnosis"] = diag
        if diag.get("exception"):
            return False, f"Echec immediat : {diag['exception']}: {diag.get('message', '')[:120]}"
        return False, f"Arret immediat (code {rc}) — voir la console"

    meta["run_token"] = token
    meta["pid"] = pid
    meta["status"] = "running"
    meta["started_at"] = utcnow_iso()
    meta["stopped_at"] = None
    meta["exit_code"] = None
    meta["last_diagnosis"] = None
    meta["detached"] = False
    log_activity("play", f"« {meta.get('name')} » demarre ({runtime})" +
                 (f" — {len(clean_answers)} reponse(s) auto" if clean_answers else ""), sid)
    return True, f"Demarre (PID {pid}, {runtime})"

def stop_script(meta: dict) -> tuple[bool, str]:
    lp = get_live(meta["id"])
    if lp and lp.is_alive():
        lp.stop()
        if get_live(meta["id"]) is lp:
            unregister(meta["id"])
        meta["status"] = "stopped"
        meta["stopped_at"] = utcnow_iso()
        meta["pid"] = None
        return True, "Arrete"
    pid = meta.get("pid")
    if pid and is_pid_alive(pid):
        try:
            import signal as _sig
            os.kill(int(pid), _sig.SIGTERM)
        except Exception:
            pass
        meta["status"] = "stopped"
        meta["pid"] = None
        return True, "Ancien processus stoppe"
    meta["status"] = "stopped"
    meta["pid"] = None
    return True, "Deja arrete"

# --- webhooks : stockage ---

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
            "per_hour": list(reversed(per_hour))}

def archive_support() -> dict:
    import shutil as _sh
    try:
        import rarfile  # noqa: F401
        rar_lib = True
    except ImportError:
        rar_lib = False
    rar_tool = bool(_sh.which("unrar") or _sh.which("bsdtar") or _sh.which("unar"))
    try:
        import py7zr  # noqa: F401
        z7 = True
    except ImportError:
        z7 = False
    return {"zip": True, "tar": True,
            "rar": rar_lib and rar_tool, "7z": z7}

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
            error = "Le code secret doit contenir au moins 6 caracteres."
        elif code != confirm:
            error = "Les deux codes ne correspondent pas."
        else:
            set_code(code)
            session.permanent = True
            session["authenticated"] = True
            log_activity("lock", "Console securisee avec un code secret")
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

@app.route("/api/runtimes")
@login_required
def api_runtimes():
    return jsonify({"ok": True, "runtimes": all_runtimes_status(),
                    "archives": archive_support()})

def script_summary(m: dict) -> dict:
    refresh_status(m)
    lp = get_live(m["id"])
    runs = m.get("runs") or []
    ok_runs = sum(1 for r in runs if r.get("reason") == "success")
    return {
        "id": m["id"], "name": m.get("name", m["id"]),
        "kind": m.get("kind", "file"),
        "filename": m.get("filename"), "entry": entry_rel(m),
        "runtime": effective_runtime(m), "runtime_mode": m.get("runtime", "auto"),
        "status": m.get("status", "stopped"),
        "detached": bool(m.get("detached")), "pid": m.get("pid"),
        "args": m.get("args", ""), "created_at": m.get("created_at"),
        "started_at": m.get("started_at"), "stopped_at": m.get("stopped_at"),
        "exit_code": m.get("exit_code"), "duration_s": m.get("duration_s"),
        "has_requirements": bool((m.get("manifests") or {}).get("requirements.txt"))
                            or (script_dir(m["id"]) / "requirements.txt").exists(),
        "has_package_json": bool((m.get("manifests") or {}).get("package.json")),
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
        "runtimes": all_runtimes_status(),
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

# ------------------------------------------------- creation / upload ---

TEMPLATES = {
    "py_blank": ("Python — Vierge", "main.py", 'print("Hello !")\n'),
    "py_menu": ("Python — Menu interactif", "main.py",
                '''print("=" * 30)
print("  MON ASSISTANT")
print("=" * 30)
print("1 - Dire bonjour")
print("2 - Calculer un double")
print("3 - Quitter")

choix = input("Votre choix (1-3) : ")

if choix == "1":
    prenom = input("Votre prenom : ")
    print(f"Bonjour {prenom} !")
elif choix == "2":
    n = input("Un nombre : ")
    print(f"Le double de {n} = {int(n) * 2}")
else:
    print("Au revoir.")
'''),
    "py_webhook": ("Python — Bot webhook", "main.py",
                   '''"""Recoit les donnees du webhook via WEBHOOK_PAYLOAD."""
import json
import os
import time
from datetime import datetime

print("Bot demarre, en attente de signaux...", flush=True)

payload_raw = os.environ.get("WEBHOOK_PAYLOAD")
if payload_raw:
    print(f"Declenche par webhook : {os.environ.get('WEBHOOK_TIME')}", flush=True)
    try:
        data = json.loads(payload_raw)
        print(f"Signal : {data}", flush=True)
    except json.JSONDecodeError:
        print(f"Brut : {payload_raw[:500]}", flush=True)

while True:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] En vie", flush=True)
    time.sleep(60)
'''),
    "py_loop": ("Python — Tache en boucle", "main.py",
                '''"""Se repete toutes les X secondes."""
import time
from datetime import datetime

while True:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Execution...", flush=True)
    # Votre code ici
    time.sleep(30)
'''),
    "js_blank": ("Node.js — Vierge", "main.js",
                 '''console.log("Hello !");

const prenom = await question("Votre prenom : ");
console.log(`Bonjour ${prenom} !`);
'''),
    "sh_blank": ("Shell — Vierge", "main.sh",
                 '''#!/usr/bin/env bash
echo "Hello !"
read -p "Votre prenom : " prenom
echo "Bonjour $prenom !"
'''),
}

def _clean_name(raw: str, fallback: str) -> str:
    name = re.sub(r"[^\w\s\-àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ.]", "",
                  (raw or "").strip())[:60]
    return name or fallback

def _parse_env_text(raw: str) -> dict:
    env = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip():
            env[k.strip()[:100]] = v.strip()[:2000]
    return env

@app.route("/api/scripts/create", methods=["POST"])
@login_required
def api_create():
    data = request.get_json(force=True, silent=True) or {}
    tpl_key = str(data.get("template") or "py_blank")
    title, filename, code = TEMPLATES.get(tpl_key, TEMPLATES["py_blank"])
    name = _clean_name(data.get("name"), "Nouveau script")
    script_id = secrets.token_hex(6)
    sdir = script_dir(script_id)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / filename).write_text(code, encoding="utf-8")
    with META_LOCK:
        meta = load_scripts_meta()
        meta[script_id] = {"id": script_id, "name": name, "kind": "file",
                           "filename": filename, "runtime": "auto", "custom_cmd": "",
                           "args": "", "env": {}, "status": "stopped", "pid": None,
                           "created_at": utcnow_iso(), "updated_at": utcnow_iso(),
                           "started_at": None, "stopped_at": None,
                           "exit_code": None, "runs": []}
        save_scripts_meta(meta)
    log_activity("edit", f"« {name} » cree (modele : {title})", script_id)
    return jsonify({"ok": True, "id": script_id,
                    "analysis": analyze_file(filename, code)})

@app.route("/api/scripts/upload", methods=["POST"])
@login_required
def api_upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Aucun fichier recu"}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Nom de fichier vide"}), 400
    filename = secure_filename(f.filename)
    ext = Path(filename).suffix.lower()
    if ext not in SINGLE_FILE_EXTS:
        return jsonify({"ok": False, "error":
                        f"Extension non supportee ({ext or '?'}). Fichiers : "
                        + ", ".join(sorted(SINGLE_FILE_EXTS))
                        + ". Pour un projet complet, utilisez l'onglet Archive."}), 400
    try:
        content = f.read()
    except Exception as e:
        return jsonify({"ok": False, "error": f"Lecture impossible : {e}"}), 400
    if len(content) > MAX_FILE_MB * 1024 * 1024:
        return jsonify({"ok": False, "error": f"Fichier trop volumineux (max {MAX_FILE_MB} Mo)"}), 400
    try:
        code = content.decode("utf-8")
    except UnicodeDecodeError:
        return jsonify({"ok": False, "error": "Le fichier doit etre encode en UTF-8"}), 400
    if "\0" in code:
        return jsonify({"ok": False, "error": "Fichier binaire refuse"}), 400

    name = _clean_name(request.form.get("name"), Path(filename).stem)
    args = (request.form.get("args") or "").strip()[:500]
    env = _parse_env_text(request.form.get("env_text"))

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
        meta[script_id] = {"id": script_id, "name": name, "kind": "file",
                           "filename": filename, "runtime": "auto", "custom_cmd": "",
                           "args": args, "env": env, "status": "stopped",
                           "pid": None, "created_at": utcnow_iso(),
                           "updated_at": utcnow_iso(), "started_at": None,
                           "stopped_at": None, "exit_code": None, "runs": []}
        save_scripts_meta(meta)
    log_activity("upload", f"« {name} » importe ({len(code.splitlines())} lignes)", script_id)

    analysis = analyze_file(filename, code)
    messages = []
    if request.form.get("install_deps") == "on" and (sdir / "requirements.txt").exists():
        ok, msg = pip_install(script_id)
        messages.append(msg)
    return jsonify({"ok": True, "id": script_id, "name": name,
                    "analysis": analysis, "messages": messages})

@app.route("/api/scripts/upload-archive", methods=["POST"])
@login_required
def api_upload_archive():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Aucun fichier recu"}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Nom de fichier vide"}), 400
    filename = secure_filename(f.filename)
    kind = archive_kind(filename)
    if kind is None:
        return jsonify({"ok": False, "error":
                        "Format non supporte. Attendus : .zip, .tar, .tar.gz, .rar, .7z"}), 400
    script_id = secrets.token_hex(6)
    sdir = script_dir(script_id)
    sdir.mkdir(parents=True, exist_ok=True)
    # Conserve un suffixe complet (.tar.gz, pas .gz) pour la detection
    save_ext = {"zip": ".zip", "tar": ".tar.gz", "rar": ".rar", "7z": ".7z"}[kind]
    archive_path = sdir / f"upload{save_ext}"
    try:
        f.save(str(archive_path))
    except Exception as e:
        return jsonify({"ok": False, "error": f"Enregistrement impossible : {e}"}), 400
    try:
        if archive_path.stat().st_size > MAX_ARCHIVE_MB * 1024 * 1024:
            archive_path.unlink(missing_ok=True)
            return jsonify({"ok": False, "error":
                            f"Archive trop volumineuse (max {MAX_ARCHIVE_MB} Mo)"}), 400
        dest = sdir / "project"
        ok, msg, _ = extract_archive(archive_path, dest)
    finally:
        archive_path.unlink(missing_ok=True)
    if not ok:
        import shutil
        shutil.rmtree(sdir, ignore_errors=True)
        return jsonify({"ok": False, "error": msg}), 400

    scan = scan_project(dest)
    if not scan["entries"]:
        import shutil
        shutil.rmtree(sdir, ignore_errors=True)
        return jsonify({"ok": False,
                        "error": "Aucun script executable trouve (.py, .js, .sh) dans l'archive"}), 400
    name = _clean_name(request.form.get("name"), Path(filename).stem)
    suggested = scan["suggested_entry"]
    with META_LOCK:
        meta = load_scripts_meta()
        meta[script_id] = {"id": script_id, "name": name, "kind": "project",
                           "entry": suggested["path"] if suggested else None,
                           "runtime": "auto", "custom_cmd": "",
                           "manifests": scan["manifests"],
                           "args": "", "env": {}, "status": "stopped",
                           "pid": None, "created_at": utcnow_iso(),
                           "updated_at": utcnow_iso(), "started_at": None,
                           "stopped_at": None, "exit_code": None, "runs": []}
        save_scripts_meta(meta)
    log_activity("project", f"Projet « {name} » importe ({scan['total_files']} fichiers) — {msg}", script_id)
    analysis = analyze_entry(meta[script_id])
    return jsonify({"ok": True, "id": script_id, "name": name, "scan": scan,
                    "analysis": analysis, "message": msg})

# ------------------------------------------------- projets : fichiers ---

@app.route("/api/scripts/<script_id>/tree")
@login_required
def api_tree(script_id):
    with META_LOCK:
        m = load_scripts_meta().get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Introuvable"}), 404
    if m.get("kind") != "project":
        return jsonify({"ok": False, "error": "Pas un projet"}), 400
    scan = scan_project(project_root(m))
    with META_LOCK:
        meta = load_scripts_meta()
        if script_id in meta:
            meta[script_id]["manifests"] = scan["manifests"]
            save_scripts_meta(meta)
    return jsonify({"ok": True, "scan": scan, "entry": m.get("entry"),
                    "runtime": effective_runtime(m)})

@app.route("/api/scripts/<script_id>/entry", methods=["POST"])
@login_required
def api_set_entry(script_id):
    data = request.get_json(force=True, silent=True) or {}
    rel = (data.get("path") or "").strip()
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Introuvable"}), 404
        if m.get("kind") != "project":
            return jsonify({"ok": False, "error": "Pas un projet"}), 400
        if refresh_status(m).get("status") == "running":
            return jsonify({"ok": False, "error": "Arretez d'abord le projet"}), 400
        target = project_file(project_root(m), rel)
        if not target or not target.is_file():
            return jsonify({"ok": False, "error": "Fichier introuvable dans le projet"}), 400
        m["entry"] = rel
        m["updated_at"] = utcnow_iso()
        m["last_diagnosis"] = None
        save_scripts_meta(meta)
    analysis = analyze_entry(m)
    return jsonify({"ok": True, "entry": rel,
                    "runtime": effective_runtime(m), "analysis": analysis})

@app.route("/api/scripts/<script_id>/file")
@login_required
def api_file_read(script_id):
    rel = (request.args.get("path") or "").strip()
    with META_LOCK:
        m = load_scripts_meta().get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Introuvable"}), 404
    root = project_root(m)
    if m.get("kind") != "project":
        rel = m.get("filename") or ""
    target = project_file(root, rel)
    if not target or not target.is_file():
        return jsonify({"ok": False, "error": "Fichier introuvable"}), 404
    try:
        if target.stat().st_size > MAX_EDITOR_BYTES:
            return jsonify({"ok": False, "error": "Fichier trop volumineux pour l'editeur"}), 400
        raw = target.read_bytes()
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    if b"\0" in raw[:8192]:
        return jsonify({"ok": False, "error": "Fichier binaire — lecture refusee"}), 400
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return jsonify({"ok": False, "error": "Fichier non UTF-8"}), 400
    return jsonify({"ok": True, "path": rel, "content": content,
                    "runtime": detect_runtime(target.name)})

@app.route("/api/scripts/<script_id>/file", methods=["PUT"])
@login_required
def api_file_write(script_id):
    data = request.get_json(force=True, silent=True) or {}
    rel = (data.get("path") or "").strip()
    content = data.get("content")
    if not isinstance(content, str):
        return jsonify({"ok": False, "error": "Contenu invalide"}), 400
    if len(content.encode("utf-8")) > 2 * 1024 * 1024:
        return jsonify({"ok": False, "error": "Contenu trop volumineux (max 2 Mo)"}), 400
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Introuvable"}), 404
        root = project_root(m)
        if m.get("kind") != "project":
            rel = m.get("filename") or ""
        target = project_file(root, rel)
        if not target or not target.is_file():
            return jsonify({"ok": False, "error": "Fichier introuvable"}), 400
        rt = detect_runtime(target.name)
        if rt == "python":
            # Verifie le NOUVEAU contenu avant d'ecraser
            try:
                compile(content, target.name, "exec")
            except SyntaxError as e:
                return jsonify({"ok": False, "error":
                                f"Syntaxe invalide ligne {e.lineno} : {e.msg} — non enregistre"}), 400
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as e:
            return jsonify({"ok": False, "error": str(e)}), 500
        if rt in ("node", "bash"):
            # node --check / bash -n travaillent sur fichier : verifie apres ecriture
            ok_syn2, syn_msg2 = syntax_check(rt, str(target))
            if not ok_syn2:
                m["updated_at"] = utcnow_iso()
                save_scripts_meta(meta)
                return jsonify({"ok": True, "warning":
                                f"Enregistre, mais {syn_msg2[0:1].lower() + syn_msg2[1:]}"}), 200
        m["updated_at"] = utcnow_iso()
        m["last_diagnosis"] = None
        save_scripts_meta(meta)
    analysis = analyze_entry(m)
    return jsonify({"ok": True, "analysis": analysis})

# ------------------------------------------------- analyse ---

@app.route("/api/scripts/<script_id>/analyze")
@login_required
def api_analyze(script_id):
    with META_LOCK:
        m = load_scripts_meta().get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Introuvable"}), 404
    analysis = analyze_entry(m)
    if not analysis:
        return jsonify({"ok": False, "error": "Fichier d'entree illisible"}), 400
    rel = entry_rel(m) or ""
    return jsonify({"ok": True, "entry": rel,
                    "runtime": effective_runtime(m),
                    "analysis": analysis})

# ------------------------------------------------- detail / config ---

@app.route("/api/scripts/<script_id>", methods=["GET"])
@login_required
def api_script_detail(script_id):
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Introuvable"}), 404
        summ = script_summary(m)
        save_scripts_meta(meta)
    req_text = ""
    rp = requirements_path(m)
    if rp and rp.exists():
        try:
            req_text = rp.read_text(encoding="utf-8")[:50000]
        except OSError:
            pass
    lp_p = log_path(script_dir(script_id))
    out = {"ok": True, "script": {
        **summ,
        "env": m.get("env") or {},
        "custom_cmd": m.get("custom_cmd", ""),
        "code": entry_code(m),
        "requirements": req_text,
        "log_size": lp_p.stat().st_size if lp_p.exists() else 0,
        "runs": list(reversed(m.get("runs") or [])),
        "diagnosis": m.get("last_diagnosis"),
        "qa_history": m.get("qa_history") or [],
        "manifests": m.get("manifests") or {},
    }}
    if m.get("kind") == "project":
        scan = scan_project(project_root(m))
        out["script"]["tree"] = scan
    return jsonify(out)

@app.route("/api/scripts/<script_id>", methods=["PUT"])
@login_required
def api_script_update(script_id):
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Introuvable"}), 404
        data = request.get_json(force=True, silent=True) or {}
        if "name" in data and str(data["name"]).strip():
            m["name"] = _clean_name(data["name"], m.get("name", "Script"))
        if "args" in data:
            m["args"] = str(data["args"] or "")[:500]
        if "env" in data and isinstance(data["env"], dict):
            m["env"] = {str(k)[:100]: str(v)[:2000]
                        for k, v in data["env"].items() if str(k).strip()}
        if "runtime" in data:
            rt = str(data["runtime"] or "auto").strip().lower()
            if rt not in ("auto", "python", "node", "bash", "custom"):
                return jsonify({"ok": False, "error": "Runtime invalide"}), 400
            if refresh_status(m).get("status") == "running":
                return jsonify({"ok": False, "error":
                                "Arretez d'abord pour changer de runtime"}), 400
            m["runtime"] = rt
        if "custom_cmd" in data:
            m["custom_cmd"] = str(data["custom_cmd"] or "")[:500]
        if "requirements" in data and isinstance(data["requirements"], str):
            rp = requirements_path(m, create=True)
            if rp is None:
                return jsonify({"ok": False, "error":
                                "Projet sans emplacement requirements definissable"}), 400
            if data["requirements"].strip():
                rp.write_text(data["requirements"][:50000], encoding="utf-8")
            elif rp.exists():
                rp.unlink()
            scan = scan_project(project_root(m)) if m.get("kind") == "project" else None
            if scan is not None:
                m["manifests"] = scan["manifests"]
        m["updated_at"] = utcnow_iso()
        save_scripts_meta(meta)
    analysis = analyze_entry(m)
    return jsonify({"ok": True, "analysis": analysis,
                    "runtime": effective_runtime(m)})

@app.route("/api/scripts/<script_id>", methods=["DELETE"])
@login_required
def api_script_delete(script_id):
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Introuvable"}), 404
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
    log_activity("trash", f"« {m.get('name')} » supprime")
    return jsonify({"ok": True})

def requirements_path(m: dict, create: bool = False) -> Path | None:
    root = project_root(m)
    manifests = m.get("manifests") or {}
    if manifests.get("requirements.txt"):
        p = project_file(root, manifests["requirements.txt"])
        if p:
            return p
    fallback = root / "requirements.txt"
    if fallback.exists() or create:
        return fallback
    return None

def package_json_dir(m: dict) -> Path | None:
    root = project_root(m)
    manifests = m.get("manifests") or {}
    if manifests.get("package.json"):
        p = project_file(root, manifests["package.json"])
        if p:
            return p.parent
    return None

# ------------------------------------------------- execution ---

@app.route("/api/scripts/<script_id>/run", methods=["POST"])
@login_required
def api_script_run(script_id):
    data = request.get_json(force=True, silent=True) or {}
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Introuvable"}), 404
        # Changements de derniere minute (modal de lancement)
        if m.get("kind") == "project" and data.get("entry"):
            target = project_file(project_root(m), str(data["entry"]))
            if not target or not target.is_file():
                return jsonify({"ok": False, "error": "Point d'entree invalide"}), 400
            m["entry"] = str(data["entry"])
        if data.get("runtime") in ("auto", "python", "node", "bash", "custom"):
            m["runtime"] = data["runtime"]
        if "custom_cmd" in data:
            m["custom_cmd"] = str(data["custom_cmd"] or "")[:500]
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
            return jsonify({"ok": False, "error": "Introuvable"}), 404
        was = refresh_status(m).get("status") == "running"
        ok, msg = stop_script(m)
        save_scripts_meta(meta)
    if was:
        log_activity("stop", f"« {m.get('name')} » arrete manuellement", script_id)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/scripts/<script_id>/restart", methods=["POST"])
@login_required
def api_script_restart(script_id):
    data = request.get_json(force=True, silent=True) or {}
    with META_LOCK:
        meta = load_scripts_meta()
        m = meta.get(script_id)
        if not m:
            return jsonify({"ok": False, "error": "Introuvable"}), 404
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
        return jsonify({"ok": False, "error": "Reponse trop longue"}), 400
    lp = get_live(script_id)
    if not lp or not lp.is_alive():
        with META_LOCK:
            m = load_scripts_meta().get(script_id)
        if m and refresh_status(m).get("detached"):
            return jsonify({"ok": False, "error":
                            "Script detache (serveur redemarre) — relancez-le pour interagir"}), 400
        return jsonify({"ok": False, "error": "Le script n'est plus en cours"}), 400
    ok, msg = lp.send_input(text)
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
            return jsonify({"ok": False, "error": "Introuvable"}), 404
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
            from analyzer import extract_options
            options = extract_options(prompt, lp._recent_out[-12:]) if waiting else []
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

# ------------------------------------------------- dependances ---

def _pip_emit(script_id: str, text: str, level="info"):
    lp = get_live(script_id)
    if lp and lp.is_alive():
        lp.emit("sys", text, level)
    else:
        jp = jsonl_path(script_dir(script_id))
        try:
            from runner import last_seq as _ls
            obj = {"seq": _ls(jp), "t": utcnow_iso(), "type": "sys",
                   "level": level, "text": text[:5000]}
            with open(jp, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        except OSError:
            pass

def _run_pip(cmd: list, script_id: str, label: str) -> tuple[bool, str]:
    _pip_emit(script_id, label)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if proc.returncode != 0 and "externally-managed-environment" in out:
            _pip_emit(script_id, "Environnement Python verrouille — nouvel essai force…")
            cmd = cmd[:4] + ["--break-system-packages"] + cmd[4:]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if out:
            _pip_emit(script_id, out[-4000:], "info")
        if proc.returncode == 0:
            _pip_emit(script_id, "Installation reussie", "success")
            return True, "Installation reussie"
        _pip_emit(script_id, f"pip a echoue (code {proc.returncode})", "error")
        return False, f"pip a echoue (code {proc.returncode}) — voir la console"
    except subprocess.TimeoutExpired:
        _pip_emit(script_id, "pip : delai depasse (10 min)", "error")
        return False, "Delai pip depasse (10 min)"
    except Exception as e:
        return False, f"Erreur pip : {e}"

def pip_install(script_id: str, package: str | None = None) -> tuple[bool, str]:
    with META_LOCK:
        m = load_scripts_meta().get(script_id)
    if not m:
        return False, "Introuvable"
    if package:
        return _run_pip([sys.executable, "-m", "pip", "install", package],
                        script_id, f"Installation de « {package} » (pip)…")
    rp = requirements_path(m)
    if not rp or not rp.exists():
        return False, "Aucun requirements.txt"
    return _run_pip([sys.executable, "-m", "pip", "install", "-r", str(rp)],
                    script_id, "Installation des dependances (requirements.txt)…")

def npm_install_pkgs(script_id: str, package: str | None = None) -> tuple[bool, str]:
    with META_LOCK:
        m = load_scripts_meta().get(script_id)
    if not m:
        return False, "Introuvable"
    nb = npm_bin()
    if not nb:
        return False, "npm introuvable sur le serveur"
    cwd = package_json_dir(m) or project_root(m)
    cmd = [nb, "install"] + ([package] if package else []) + ["--no-audit", "--no-fund"]
    _pip_emit(script_id, f"Installation {'de « ' + package + ' »' if package else 'des dependances'} (npm)…")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(cwd))
        out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if out:
            _pip_emit(script_id, out[-4000:], "info")
        if proc.returncode == 0:
            _pip_emit(script_id, "Installation npm reussie", "success")
            return True, "Installation npm reussie"
        _pip_emit(script_id, f"npm a echoue (code {proc.returncode})", "error")
        return False, f"npm a echoue (code {proc.returncode}) — voir la console"
    except subprocess.TimeoutExpired:
        return False, "Delai npm depasse (10 min)"
    except Exception as e:
        return False, f"Erreur npm : {e}"

@app.route("/api/scripts/<script_id>/install-deps", methods=["POST"])
@login_required
def api_script_install(script_id):
    ok, msg = pip_install(script_id)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/scripts/<script_id>/npm-install", methods=["POST"])
@login_required
def api_script_npm_install(script_id):
    ok, msg = npm_install_pkgs(script_id)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/scripts/<script_id>/fix-install", methods=["POST"])
@login_required
def api_fix_install(script_id):
    data = request.get_json(force=True, silent=True) or {}
    manager = str(data.get("manager") or "pip").strip().lower()
    if manager not in ("pip", "npm"):
        return jsonify({"ok": False, "error": "Gestionnaire invalide"}), 400
    if manager == "pip":
        pkg = re.sub(r"[^A-Za-z0-9_\-.\[\]]", "", str(data.get("package", "")))[:80]
    else:
        pkg = re.sub(r"[^A-Za-z0-9_\-./@^~]", "", str(data.get("package", "")))[:120]
    if not pkg:
        return jsonify({"ok": False, "error": "Paquet invalide"}), 400
    if manager == "pip":
        ok, msg = pip_install(script_id, pkg)
        if ok:
            with META_LOCK:
                m = load_scripts_meta().get(script_id)
            if m:
                rp = requirements_path(m, create=True)
                if rp:
                    try:
                        existing = rp.read_text(encoding="utf-8") if rp.exists() else ""
                        if pkg.lower() not in existing.lower():
                            with open(rp, "a", encoding="utf-8") as f:
                                if existing and not existing.endswith("\n"):
                                    f.write("\n")
                                f.write(pkg + "\n")
                    except OSError:
                        pass
    else:
        ok, msg = npm_install_pkgs(script_id, pkg)
    return jsonify({"ok": ok, "message": msg})

# ------------------------------------------------- logs ---

@app.route("/api/scripts/<script_id>/logs/clear", methods=["POST"])
@login_required
def api_script_logs_clear(script_id):
    with META_LOCK:
        m = load_scripts_meta().get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Introuvable"}), 404
    try:
        jp = jsonl_path(script_dir(script_id))
        if jp.exists():
            jp.unlink()
        log_path(script_dir(script_id)).write_text(
            f"[{utcnow_iso()}] Console effacee\n", encoding="utf-8")
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
        return jsonify({"ok": False, "error": "Introuvable"}), 404
    lp_p = log_path(script_dir(script_id))
    if not lp_p.exists():
        return jsonify({"ok": False, "error": "Aucun log"}), 404
    return send_file(lp_p, as_attachment=True,
                     download_name=f"{m.get('name', 'script')}-console.log")

# ------------------------------------------------- webhook entrant ---

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
                    action_msg = f"relance : {msg}"
                    entry["triggered_script"] = {"id": m["id"], "name": m.get("name"),
                                                 "action": "restart", "ok": ok, "message": msg}
                elif already:
                    action_msg = "deja en cours (relance desactivee)"
                    entry["triggered_script"] = {"id": m["id"], "name": m.get("name"),
                                                 "action": "skipped", "ok": True, "message": action_msg}
                else:
                    ok, msg = start_script(m, answers=replay, extra_env=extra)
                    action_msg = f"execution : {msg}"
                    entry["triggered_script"] = {"id": m["id"], "name": m.get("name"),
                                                 "action": "run", "ok": ok, "message": msg}
                save_scripts_meta(meta)
            else:
                entry["triggered_script"] = {"error": "Script lie introuvable"}
    entry["duration_ms"] = round((time.time() - started) * 1000, 1)
    append_webhook_log(entry)
    log_activity("webhook", f"Webhook {request.method} recu ({entry['body_size']} octets)" +
                 (f" — {action_msg}" if action_msg else ""))
    return jsonify({"ok": True, "event_id": entry["id"],
                    "triggered": entry["triggered_script"],
                    "message": action_msg or "Webhook recu et journalise"}), 200

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
                return jsonify({"ok": False, "error": "Script lie introuvable"}), 400
        cfg["linked_script_id"] = lid or None
    for key in ("auto_run", "restart_if_running", "pass_payload"):
        if key in data:
            cfg[key] = bool(data[key])
    if cfg.get("auto_run") and not cfg.get("linked_script_id"):
        return jsonify({"ok": False, "error":
                        "Choisissez un script avant d'activer l'execution auto"}), 400
    save_webhook_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/webhooks/regenerate", methods=["POST"])
@login_required
def api_webhook_regenerate():
    cfg = load_webhook_config()
    cfg["token"] = secrets.token_urlsafe(24)
    cfg["created_at"] = utcnow_iso()
    save_webhook_config(cfg)
    log_activity("refresh", "URL webhook regeneree")
    return jsonify({"ok": True, "url": url_for("webhook_receiver", token=cfg["token"], _external=True)})

@app.route("/api/webhooks/test", methods=["POST"])
@login_required
def api_webhook_test():
    import requests as rq
    cfg = load_webhook_config()
    url = url_for("webhook_receiver", token=cfg["token"], _external=True)
    try:
        r = rq.post(url, json={"test": True,
                               "message": "Appel de test depuis le tableau de bord",
                               "time": utcnow_iso()}, timeout=15)
        return jsonify({"ok": r.ok, "status": r.status_code,
                        "response": r.text[:2000]})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Envoi impossible : {e}"}), 502

# --- parametres ---

@app.route("/api/change-code", methods=["POST"])
@login_required
def api_change_code():
    if ENV_SECRET_CODE:
        return jsonify({"ok": False, "error":
                        "SECRET_CODE est defini par variable d'environnement — modifiez-le sur Render"}), 400
    data = request.get_json(force=True, silent=True) or {}
    current = (data.get("current") or "").strip()
    new = (data.get("new") or "").strip()
    if not verify_code(current):
        return jsonify({"ok": False, "error": "Code actuel incorrect"}), 403
    if len(new) < 6:
        return jsonify({"ok": False, "error":
                        "Le nouveau code doit contenir au moins 6 caracteres"}), 400
    set_code(new)
    log_activity("lock", "Code secret modifie")
    return jsonify({"ok": True, "message": "Code secret mis a jour"})

# ------------------------------------------------------- erreurs ---

@app.errorhandler(413)
def too_large(_e):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "Fichier trop volumineux"}), 413
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
