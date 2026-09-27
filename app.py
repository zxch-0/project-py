"""
PyRunner — Console privée pour exécuter des scripts Python sur Render.
- Upload + exécution persistante (survit à la fermeture du navigateur)
- Accès protégé par code secret (défini à la première visite)
- Moniteur de webhook inbound avec historique détaillé
"""
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Flask, jsonify, redirect, render_template, request,
    Response, send_file, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

# ---------------------------------------------------------------- config ---

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
SCRIPTS_ROOT = DATA_DIR / "scripts"

AUTH_FILE = DATA_DIR / "auth.json"
APP_SECRET_FILE = DATA_DIR / "app_secret.key"
SCRIPTS_META_FILE = DATA_DIR / "scripts.json"
WEBHOOK_CONFIG_FILE = DATA_DIR / "webhook.json"
WEBHOOK_LOGS_FILE = DATA_DIR / "webhook_logs.jsonl"

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "16"))
MAX_LOG_TAIL_BYTES = 200 * 1024
MAX_WEBHOOK_LOGS = 500
MAX_WEBHOOK_BODY = 20 * 1024  # 20 Ko max stocké par appel

DATA_DIR.mkdir(parents=True, exist_ok=True)
SCRIPTS_ROOT.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

# Clé de session persistante (sinon déconnexion à chaque redéploiement)
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
app.permanent_session_lifetime = 60 * 60 * 24 * 30  # 30 jours

ENV_SECRET_CODE = os.environ.get("SECRET_CODE", "").strip()  # optionnel : force le code

# ------------------------------------------------------------- helpers ---

def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

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
        "created_at": utcnow_iso(),
        "updated_at": utcnow_iso(),
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

def script_log_path(meta: dict) -> Path:
    return script_dir(meta["id"]) / "output.log"

def script_req_path(meta: dict) -> Path:
    return script_dir(meta["id"]) / "requirements.txt"

def is_pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError, PermissionError):
        return False

def refresh_status(meta: dict) -> dict:
    """Recalcule le statut réel (running / stopped / error) depuis le PID."""
    pid = meta.get("pid")
    if pid and is_pid_alive(pid):
        meta["status"] = "running"
    else:
        if meta.get("status") == "running":
            # Le process est mort sans qu'on l'arrête -> marquer stopped
            # (exit_code inconnu car pas de wait ; on note juste l'heure)
            meta["status"] = "stopped"
            meta["stopped_at"] = meta.get("stopped_at") or utcnow_iso()
            meta["pid"] = None
    return meta

def append_log(meta: dict, text: str) -> None:
    try:
        with open(script_log_path(meta), "a", encoding="utf-8") as f:
            f.write(f"\n[{utcnow_iso()}] {text}\n")
    except OSError:
        pass

def stop_process(meta: dict, timeout: float = 8.0) -> bool:
    pid = meta.get("pid")
    if not pid or not is_pid_alive(pid):
        meta["pid"] = None
        if meta.get("status") == "running":
            meta["status"] = "stopped"
        return True
    try:
        # Tuer tout le groupe (le script + ses enfants)
        try:
            os.killpg(int(pid), signal.SIGTERM)
        except Exception:
            os.kill(int(pid), signal.SIGTERM)
        deadline = time.time() + timeout
        while time.time() < deadline and is_pid_alive(pid):
            time.sleep(0.2)
        if is_pid_alive(pid):
            try:
                os.killpg(int(pid), signal.SIGKILL)
            except Exception:
                os.kill(int(pid), signal.SIGKILL)
            time.sleep(0.3)
        meta["pid"] = None
        meta["status"] = "stopped"
        meta["stopped_at"] = utcnow_iso()
        return True
    except Exception as e:
        append_log(meta, f"⚠️ Erreur à l'arrêt du process {pid} : {e}")
        return False

def start_process(meta: dict, extra_env: dict | None = None) -> tuple[bool, str]:
    # Si déjà en cours, ne pas dupliquer
    refresh_status(meta)
    if meta.get("status") == "running" and meta.get("pid"):
        return False, "Script déjà en cours d'exécution"

    main_path = script_main_path(meta)
    if not main_path.exists():
        meta["status"] = "error"
        return False, f"Fichier introuvable : {main_path.name}"

    # Arrêter l'ancien PID résiduel éventuel
    if meta.get("pid"):
        stop_process(meta)

    env = os.environ.copy()
    # Variables configurées par l'utilisateur
    user_env = meta.get("env") or {}
    if isinstance(user_env, dict):
        for k, v in user_env.items():
            if k:
                env[str(k)] = str(v)
    if extra_env:
        for k, v in extra_env.items():
            env[str(k)] = str(v)
    env["PYRUNNER_SCRIPT_ID"] = meta["id"]
    env["PYRUNNER_SCRIPT_NAME"] = meta.get("name", meta["id"])
    env["PYTHONUNBUFFERED"] = "1"

    args = [sys.executable, "-u", str(main_path)]
    raw_args = (meta.get("args") or "").strip()
    if raw_args:
        import shlex
        try:
            args += shlex.split(raw_args)
        except ValueError:
            args += raw_args.split()

    log_path = script_log_path(meta)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        log_file = open(log_path, "a", encoding="utf-8")
    except OSError as e:
        meta["status"] = "error"
        return False, f"Impossible d'ouvrir le log : {e}"

    try:
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"\n{'='*60}\n[{utcnow_iso()}] ▶ Démarrage : {' '.join(args)}\n{'='*60}\n")
    except OSError:
        pass

    try:
        proc = subprocess.Popen(
            args,
            cwd=str(script_dir(meta["id"])),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # détaché : survit à la requête HTTP
        )
        # Ne pas fermer log_file ici via with : le process enfant en hérite.
        # On le ferme côté parent après le fork.
        try:
            log_file.close()
        except Exception:
            pass
        # Petite vérification : crash immédiat ?
        time.sleep(0.4)
        rc = proc.poll()
        if rc is not None and rc != 0:
            meta["status"] = "error"
            meta["exit_code"] = rc
            meta["pid"] = None
            meta["stopped_at"] = utcnow_iso()
            append_log(meta, f"❌ Le script s'est arrêté immédiatement (code {rc}). Voir log ci-dessus.")
            return False, f"Le script a crashé au démarrage (code {rc}) — voir les logs"
        meta["pid"] = proc.pid
        meta["status"] = "running"
        meta["started_at"] = utcnow_iso()
        meta["stopped_at"] = None
        meta["exit_code"] = None
        meta["last_error"] = None
        return True, f"Démarré (PID {proc.pid})"
    except Exception as e:
        try:
            log_file.close()
        except Exception:
            pass
        meta["status"] = "error"
        meta["last_error"] = str(e)
        append_log(meta, f"❌ Échec démarrage : {e}\n{traceback.format_exc()}")
        return False, f"Échec démarrage : {e}"

# --- webhook store ---

def load_webhook_config() -> dict:
    cfg = _read_json(WEBHOOK_CONFIG_FILE, None)
    if isinstance(cfg, dict) and cfg.get("token"):
        return cfg
    cfg = {
        "token": secrets.token_urlsafe(24),
        "created_at": utcnow_iso(),
        "linked_script_id": None,
        "auto_run": False,       # exécuter le script lié à chaque appel ?
        "restart_if_running": True,
        "pass_payload": True,    # injecter WEBHOOK_PAYLOAD / WEBHOOK_EVENT
    }
    _write_json(WEBHOOK_CONFIG_FILE, cfg)
    return cfg

def save_webhook_config(cfg: dict) -> None:
    _write_json(WEBHOOK_CONFIG_FILE, cfg)

def append_webhook_log(entry: dict) -> None:
    try:
        WEBHOOK_LOGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(WEBHOOK_LOGS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # Rotation simple : garder les N derniers
    try:
        with open(WEBHOOK_LOGS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_WEBHOOK_LOGS:
            with open(WEBHOOK_LOGS_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_WEBHOOK_LOGS:])
    except OSError:
        pass

def read_webhook_logs(limit: int = 100) -> list[dict]:
    if not WEBHOOK_LOGS_FILE.exists():
        return []
    try:
        with open(WEBHOOK_LOGS_FILE, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
    except OSError:
        return []
    out = []
    for line in lines[-limit:][::-1]:  # plus récents d'abord
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out

def webhook_stats() -> dict:
    logs = read_webhook_logs(MAX_WEBHOOK_LOGS)
    now = time.time()
    last_24h = 0
    for e in logs:
        try:
            ts = datetime.fromisoformat(e.get("time", "")).timestamp()
            if now - ts < 86400:
                last_24h += 1
        except Exception:
            continue
    return {
        "total": len(logs),
        "last_24h": last_24h,
        "last_call": logs[0] if logs else None,
    }

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
    return jsonify({"ok": True, "time": utcnow_iso(), "setup_done": is_setup_done()})

@app.route("/api/status")
@login_required
def api_status():
    meta = load_scripts_meta()
    scripts = []
    running = 0
    for sid, m in meta.items():
        refresh_status(m)
        if m.get("status") == "running":
            running += 1
        scripts.append({
            "id": sid,
            "name": m.get("name", sid),
            "filename": m.get("filename"),
            "status": m.get("status", "stopped"),
            "pid": m.get("pid"),
            "args": m.get("args", ""),
            "created_at": m.get("created_at"),
            "started_at": m.get("started_at"),
            "stopped_at": m.get("stopped_at"),
            "exit_code": m.get("exit_code"),
            "has_requirements": script_req_path(m).exists(),
        })
    save_scripts_meta(meta)
    scripts.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    cfg = load_webhook_config()
    return jsonify({
        "ok": True,
        "scripts": scripts,
        "counts": {"total": len(scripts), "running": running},
        "webhook": {
            "url": url_for("webhook_receiver", token=cfg["token"], _external=True),
            "linked_script_id": cfg.get("linked_script_id"),
            "auto_run": cfg.get("auto_run"),
            "stats": webhook_stats(),
        },
    })

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
        content.decode("utf-8")
    except UnicodeDecodeError:
        return jsonify({"ok": False, "error": "Le fichier doit être encodé en UTF-8"}), 400

    name = (request.form.get("name") or "").strip() or Path(filename).stem
    name = re.sub(r"[^\w\s\-àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]", "", name).strip()[:60] or Path(filename).stem
    args = (request.form.get("args") or "").strip()[:500]
    # Env vars : format "CLE=valeur" une par ligne
    env_raw = request.form.get("env_text") or ""
    env = {}
    for line in env_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k:
            env[k] = v.strip()

    script_id = secrets.token_hex(6)
    sdir = script_dir(script_id)
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / filename).write_bytes(content)

    # requirements.txt optionnel (2e fichier)
    req = request.files.get("requirements")
    if req and req.filename:
        try:
            req_content = req.read().decode("utf-8", errors="replace")[:50000]
            (sdir / "requirements.txt").write_text(req_content, encoding="utf-8")
        except Exception:
            pass
    # requirements collé en texte
    req_text = (request.form.get("requirements_text") or "").strip()
    if req_text and not (sdir / "requirements.txt").exists():
        (sdir / "requirements.txt").write_text(req_text[:50000], encoding="utf-8")

    meta = load_scripts_meta()
    meta[script_id] = {
        "id": script_id,
        "name": name,
        "filename": filename,
        "args": args,
        "env": env,
        "status": "stopped",
        "pid": None,
        "created_at": utcnow_iso(),
        "updated_at": utcnow_iso(),
        "started_at": None,
        "stopped_at": None,
        "exit_code": None,
    }
    save_scripts_meta(meta)

    # Option : installer les dépendances + démarrer immédiatement
    autostart = request.form.get("autostart") == "on"
    install_deps = request.form.get("install_deps") == "on"
    messages = [f"Script « {name} » importé"]
    if install_deps and (sdir / "requirements.txt").exists():
        ok, msg = pip_install(script_id)
        messages.append(("✅ " if ok else "⚠️ ") + msg)
    if autostart:
        ok, msg = start_process(meta[script_id])
        save_scripts_meta(meta)
        messages.append(("✅ " if ok else "❌ ") + msg)
    else:
        save_scripts_meta(meta)
    return jsonify({"ok": True, "id": script_id, "messages": messages})

@app.route("/api/scripts/<script_id>", methods=["GET"])
@login_required
def api_script_detail(script_id):
    meta = load_scripts_meta()
    m = meta.get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Script introuvable"}), 404
    refresh_status(m)
    save_scripts_meta(meta)
    main_path = script_main_path(m)
    try:
        code = main_path.read_text(encoding="utf-8") if main_path.exists() else ""
    except OSError:
        code = ""
    req_path = script_req_path(m)
    try:
        requirements = req_path.read_text(encoding="utf-8") if req_path.exists() else ""
    except OSError:
        requirements = ""
    log_path = script_log_path(m)
    log_size = log_path.stat().st_size if log_path.exists() else 0
    return jsonify({"ok": True, "script": {
        **{k: m.get(k) for k in ("id", "name", "filename", "args", "env", "status", "pid",
            "created_at", "updated_at", "started_at", "stopped_at", "exit_code")},
        "code_preview": code[:20000],
        "code_truncated": len(code) > 20000,
        "requirements": requirements,
        "log_size": log_size,
    }})

@app.route("/api/scripts/<script_id>", methods=["PUT"])
@login_required
def api_script_update(script_id):
    meta = load_scripts_meta()
    m = meta.get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Script introuvable"}), 404
    data = request.get_json(force=True, silent=True) or {}
    if "name" in data and str(data["name"]).strip():
        m["name"] = re.sub(r"[^\w\s\-àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ]", "",
                            str(data["name"]).strip())[:60]
    if "args" in data:
        m["args"] = str(data["args"] or "")[:500]
    if "env" in data and isinstance(data["env"], dict):
        m["env"] = {str(k)[:100]: str(v)[:2000] for k, v in data["env"].items() if str(k).strip()}
    if "code" in data and isinstance(data["code"], str):
        if len(data["code"]) > 2 * 1024 * 1024:
            return jsonify({"ok": False, "error": "Code trop volumineux (max 2 Mo)"}), 400
        script_main_path(m).write_text(data["code"], encoding="utf-8")
    if "requirements" in data and isinstance(data["requirements"], str):
        rp = script_req_path(m)
        if data["requirements"].strip():
            rp.write_text(data["requirements"][:50000], encoding="utf-8")
        elif rp.exists():
            rp.unlink()
    m["updated_at"] = utcnow_iso()
    save_scripts_meta(meta)
    return jsonify({"ok": True})

@app.route("/api/scripts/<script_id>", methods=["DELETE"])
@login_required
def api_script_delete(script_id):
    meta = load_scripts_meta()
    m = meta.get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Script introuvable"}), 404
    stop_process(m)
    import shutil
    try:
        shutil.rmtree(script_dir(script_id), ignore_errors=True)
    except Exception:
        pass
    meta.pop(script_id, None)
    save_scripts_meta(meta)
    # Délier du webhook si besoin
    cfg = load_webhook_config()
    if cfg.get("linked_script_id") == script_id:
        cfg["linked_script_id"] = None
        cfg["auto_run"] = False
        save_webhook_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/scripts/<script_id>/run", methods=["POST"])
@login_required
def api_script_run(script_id):
    meta = load_scripts_meta()
    m = meta.get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Script introuvable"}), 404
    ok, msg = start_process(m)
    save_scripts_meta(meta)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/scripts/<script_id>/stop", methods=["POST"])
@login_required
def api_script_stop(script_id):
    meta = load_scripts_meta()
    m = meta.get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Script introuvable"}), 404
    was_running = refresh_status(m).get("status") == "running"
    stop_process(m)
    if was_running:
        append_log(m, "⏹ Arrêté par l'utilisateur")
    save_scripts_meta(meta)
    return jsonify({"ok": True, "message": "Arrêté" if was_running else "Déjà arrêté"})

@app.route("/api/scripts/<script_id>/restart", methods=["POST"])
@login_required
def api_script_restart(script_id):
    meta = load_scripts_meta()
    m = meta.get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Script introuvable"}), 404
    stop_process(m)
    time.sleep(0.5)
    ok, msg = start_process(m)
    save_scripts_meta(meta)
    return jsonify({"ok": ok, "message": msg})

def pip_install(script_id: str) -> tuple[bool, str]:
    meta = load_scripts_meta()
    m = meta.get(script_id)
    if not m:
        return False, "Script introuvable"
    rp = script_req_path(m)
    if not rp.exists():
        return False, "Aucun requirements.txt"
    append_log(m, f"📦 Installation des dépendances ({rp.name})…")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(rp)],
            capture_output=True, text=True, timeout=300,
        )
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        append_log(m, out[-8000:])
        if proc.returncode == 0:
            append_log(m, "✅ Dépendances installées")
            return True, "Dépendances installées"
        append_log(m, f"❌ pip a échoué (code {proc.returncode})")
        return False, f"pip a échoué (code {proc.returncode}) — voir logs"
    except subprocess.TimeoutExpired:
        append_log(m, "❌ pip : timeout (5 min)")
        return False, "Timeout pip (5 min)"
    except Exception as e:
        append_log(m, f"❌ pip : {e}")
        return False, f"Erreur pip : {e}"

@app.route("/api/scripts/<script_id>/install-deps", methods=["POST"])
@login_required
def api_script_install(script_id):
    ok, msg = pip_install(script_id)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/scripts/<script_id>/logs")
@login_required
def api_script_logs(script_id):
    meta = load_scripts_meta()
    m = meta.get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Script introuvable"}), 404
    tail_lines = max(1, min(int(request.args.get("tail", 300)), 2000))
    log_path = script_log_path(m)
    if not log_path.exists():
        return jsonify({"ok": True, "logs": "(aucun log pour le moment)", "status": refresh_status(m).get("status")})
    try:
        size = log_path.stat().st_size
        with open(log_path, "rb") as f:
            if size > MAX_LOG_TAIL_BYTES:
                f.seek(size - MAX_LOG_TAIL_BYTES)
                f.readline()  # jeter la 1re ligne partielle
            content = f.read().decode("utf-8", errors="replace")
        lines = content.splitlines()
        return jsonify({"ok": True, "logs": "\n".join(lines[-tail_lines:]),
                        "status": refresh_status(m).get("status"),
                        "truncated": len(lines) > tail_lines or size > MAX_LOG_TAIL_BYTES})
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/scripts/<script_id>/logs/clear", methods=["POST"])
@login_required
def api_script_logs_clear(script_id):
    meta = load_scripts_meta()
    m = meta.get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Script introuvable"}), 404
    try:
        script_log_path(m).write_text(f"[{utcnow_iso()}] 🧹 Logs effacés\n", encoding="utf-8")
    except OSError as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})

@app.route("/api/scripts/<script_id>/logs/download")
@login_required
def api_script_logs_download(script_id):
    meta = load_scripts_meta()
    m = meta.get(script_id)
    if not m:
        return jsonify({"ok": False, "error": "Script introuvable"}), 404
    log_path = script_log_path(m)
    if not log_path.exists():
        return jsonify({"ok": False, "error": "Aucun log"}), 404
    return send_file(log_path, as_attachment=True,
                     download_name=f"{m.get('name','script')}-output.log")

# --- webhook : réception publique (protégée par token secret dans l'URL) ---

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
        "id": secrets.token_hex(4),
        "time": utcnow_iso(),
        "method": request.method,
        "path": request.path,
        "ip": ip,
        "user_agent": request.headers.get("User-Agent", "")[:300],
        "content_type": request.headers.get("Content-Type", "")[:200],
        "query": dict(request.args),
        "headers": {k: v[:500] for k, v in request.headers.items()
                    if k.lower() not in ("cookie", "authorization")},
        "body": body_json if body_json is not None else body_text[:5000],
        "body_is_json": body_json is not None,
        "body_size": len(raw_body),
        "triggered_script": None,
    }

    # Déclenchement automatique du script lié ?
    action_msg = None
    if cfg.get("auto_run") and cfg.get("linked_script_id"):
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
            if already and cfg.get("restart_if_running", True):
                stop_process(m)
                time.sleep(0.5)
                ok, msg = start_process(m, extra_env=extra)
                action_msg = f"restart: {msg}"
                entry["triggered_script"] = {"id": m["id"], "name": m.get("name"),
                                             "action": "restart", "ok": ok, "message": msg}
            elif already:
                action_msg = "script déjà en cours (relance désactivée)"
                entry["triggered_script"] = {"id": m["id"], "name": m.get("name"),
                                             "action": "skipped", "ok": True,
                                             "message": action_msg}
                append_log(m, f"🔔 Webhook reçu ({request.method}) — script déjà en cours, payload ignoré "
                              f"(event {entry['id']})")
            else:
                ok, msg = start_process(m, extra_env=extra)
                action_msg = f"run: {msg}"
                entry["triggered_script"] = {"id": m["id"], "name": m.get("name"),
                                             "action": "run", "ok": ok, "message": msg}
            save_scripts_meta(meta)
        else:
            entry["triggered_script"] = {"error": "Script lié introuvable"}

    entry["duration_ms"] = round((time.time() - started) * 1000, 1)
    append_webhook_log(entry)
    return jsonify({"ok": True, "event_id": entry["id"],
                    "triggered": entry["triggered_script"],
                    "message": action_msg or "Webhook reçu et journalisé"}), 200

@app.route("/api/webhooks/logs")
@login_required
def api_webhook_logs():
    limit = max(1, min(int(request.args.get("limit", 100)), MAX_WEBHOOK_LOGS))
    return jsonify({"ok": True, "logs": read_webhook_logs(limit), "stats": webhook_stats()})

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
        "created_at": cfg.get("created_at"),
    }})

@app.route("/api/webhooks/config", methods=["PUT"])
@login_required
def api_webhook_config_update():
    cfg = load_webhook_config()
    data = request.get_json(force=True, silent=True) or {}
    if "linked_script_id" in data:
        lid = data["linked_script_id"]
        if lid:
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
    return jsonify({"ok": True, "url": url_for("webhook_receiver", token=cfg["token"], _external=True)})

@app.route("/api/webhooks/test", methods=["POST"])
@login_required
def api_webhook_test():
    """Envoie un appel de test vers sa propre URL webhook."""
    import requests as rq
    cfg = load_webhook_config()
    url = url_for("webhook_receiver", token=cfg["token"], _external=True)
    # En local, _external peut donner une URL non joignable ; on simule alors en direct.
    try:
        r = rq.post(url, json={"test": True, "message": "Appel de test depuis le dashboard",
                               "time": utcnow_iso()}, timeout=15)
        return jsonify({"ok": r.ok, "status": r.status_code,
                        "response": r.text[:2000]})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Envoi impossible : {e}. "
                        "Astuce : en local, utilise plutôt : curl -X POST ..."}), 502

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
