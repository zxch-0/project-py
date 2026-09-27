"""
PyRunner — Exécution interactive des scripts.
- stdin en pipe : le site détecte quand le script attend une réponse (via /proc wchan)
  et l'utilisateur répond depuis la console web, ou les réponses pré-remplies
  sont envoyées automatiquement une par une.
- Logs structurés persistés (output.jsonl : seq, heure, type, niveau, texte)
  + miroir brut (output.log).
"""
import codecs
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from analyzer import SENSITIVE_RE, diagnose_crash, extract_options

PIPE_WCHANS = {"pipe_read", "pipe_wait"}
JSONL_MAX_BYTES = 2 * 1024 * 1024   # rotation au-delà
JSONL_KEEP_BYTES = 1 * 1024 * 1024
MAX_PENDING = 2000

LEVEL_ERR_RE = re.compile(r"(Traceback|Error|Exception|FAILED|failed|ERREUR|❌|⛔|Fatal|fatal)", re.IGNORECASE)
LEVEL_WARN_RE = re.compile(r"(Warning|WARN|⚠|dépréci|deprecat)", re.IGNORECASE)
LEVEL_OK_RE = re.compile(r"(✅|SUCCESS|succès|success|démarré|terminé|OK\b)", re.IGNORECASE)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def detect_level(text: str, stream: str) -> str:
    if stream == "err":
        return "error"
    if LEVEL_ERR_RE.search(text):
        return "error"
    if LEVEL_WARN_RE.search(text):
        return "warn"
    if LEVEL_OK_RE.search(text):
        return "success"
    return "info"


def jsonl_path(script_dir: Path) -> Path:
    return script_dir / "output.jsonl"


def log_path(script_dir: Path) -> Path:
    return script_dir / "output.log"


def read_jsonl_since(path: Path, since: int, limit: int = 600) -> tuple[list, int]:
    """Lit les lignes de seq >= since. Retourne (lignes, next_seq)."""
    if not path.exists():
        return [], since
    lines, max_seq = [], since
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                sq = int(obj.get("seq", -1))
                if sq >= since:
                    lines.append(obj)
                    if len(lines) >= limit:
                        # on continue juste pour next_seq ? non : on coupe
                        pass
                if sq >= max_seq:
                    max_seq = sq + 1
                if len(lines) >= limit and sq >= since:
                    # assez de lignes, mais il faut le vrai next_seq :
                    # on compte le reste rapidement
                    rest = sum(1 for _ in f)
                    # seq approx : max trouvé + reste (les seq sont contiguës)
                    max_seq = max(max_seq, sq + 1 + rest)
                    break
    except OSError:
        return [], since
    return lines, max_seq


def read_jsonl_tail_text(path: Path, max_chars: int = 12000) -> str:
    """Texte brut des dernières lignes (pour diagnostic)."""
    if not path.exists():
        return ""
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > 200 * 1024:
                f.seek(size - 200 * 1024)
                f.readline()
            chunk = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    out = []
    for raw in chunk.splitlines():
        try:
            o = json.loads(raw)
            if o.get("type") in ("out", "err"):
                out.append(o.get("text", ""))
        except Exception:
            continue
    text = "\n".join(out)
    return text[-max_chars:]


def last_seq(path: Path) -> int:
    """next_seq courant (nb de lignes)."""
    if not path.exists():
        return 0
    try:
        size = path.stat().st_size
        if size == 0:
            return 0
        with open(path, "rb") as f:
            # lire la dernière ligne non vide
            f.seek(max(0, size - 8192))
            tail = f.read().decode("utf-8", errors="replace").splitlines()
        for raw in reversed(tail):
            try:
                return int(json.loads(raw).get("seq", 0)) + 1
            except Exception:
                continue
    except OSError:
        pass
    return 0


class LiveProcess:
    """Un script en cours d'exécution, avec console interactive."""

    def __init__(self, script_id: str, sdir: Path, on_exit=None):
        self.script_id = script_id
        self.sdir = sdir
        self.on_exit = on_exit
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()
        self._stdin_lock = threading.Lock()
        self.seq = last_seq(jsonl_path(sdir))
        self.pending = ""          # texte après le dernier \n (prompt potentiel)
        self.pending_lock = threading.Lock()
        self._bufs = {"out": "", "err": ""}  # tampons partagés (pump + send_input)
        self._buf_lock = threading.Lock()
        self.last_output_ts = time.time()
        self.stdin_open = True
        self.started_at = utcnow_iso()
        self.start_ts = time.time()
        self.stop_requested = False
        self.auto_answers: list = []
        self.answers_sent = 0
        self.qa: list = []         # [{prompt, answer, auto}]
        self._feeder_stop = threading.Event()
        self._recent_out: list = []  # dernières lignes stdout (contexte menus)
        self.finished = threading.Event()

    # ---------------- logs ----------------

    def _rotate_if_needed(self, jp: Path):
        try:
            if jp.exists() and jp.stat().st_size > JSONL_MAX_BYTES:
                with open(jp, "rb") as f:
                    f.seek(-JSONL_KEEP_BYTES, os.SEEK_END)
                    f.readline()
                    data = f.read()
                with open(jp, "wb") as f:
                    f.write(data)
        except OSError:
            pass

    def emit(self, ltype: str, text: str, level: str = "info"):
        with self.lock:
            seq = self.seq
            self.seq += 1
            obj = {"seq": seq, "t": utcnow_iso(), "type": ltype,
                   "level": level, "text": text[:10000]}
            jp = jsonl_path(self.sdir)
            try:
                with open(jp, "a", encoding="utf-8") as f:
                    f.write(json.dumps(obj, ensure_ascii=False) + "\n")
                if seq % 50 == 0:
                    self._rotate_if_needed(jp)
            except OSError:
                pass
            # miroir brut lisible
            try:
                with open(log_path(self.sdir), "a", encoding="utf-8") as f:
                    if ltype == "in":
                        f.write(f"❯ {text}\n")
                    elif ltype == "sys":
                        f.write(f"[{obj['t']}] {text}\n")
                    else:
                        f.write(text + ("\n" if not text.endswith("\n") else ""))
            except OSError:
                pass
        return seq

    # ---------------- spawn ----------------

    def spawn(self, argv: list, env: dict, auto_answers: list | None = None):
        self.auto_answers = list(auto_answers or [])
        lp = log_path(self.sdir)
        try:
            with open(lp, "a", encoding="utf-8") as f:
                f.write(f"\n{'=' * 60}\n[{utcnow_iso()}] ▶ {' '.join(argv)}\n{'=' * 60}\n")
        except OSError:
            pass
        self.emit("sys", f"▶ Démarrage : {' '.join(argv)}", "info")
        if self.auto_answers:
            self.emit("sys", f"🤖 {len(self.auto_answers)} réponse(s) pré-remplie(s) — envoi automatique", "info")
        self.proc = subprocess.Popen(
            argv, cwd=str(self.sdir), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True, bufsize=0,
        )
        self.last_output_ts = time.time()
        threading.Thread(target=self._pump, args=(self.proc.stdout, "out"),
                         daemon=True, name=f"pump-{self.script_id}-out").start()
        threading.Thread(target=self._pump, args=(self.proc.stderr, "err"),
                         daemon=True, name=f"pump-{self.script_id}-err").start()
        threading.Thread(target=self._feeder, daemon=True,
                         name=f"feeder-{self.script_id}").start()
        threading.Thread(target=self._waiter, daemon=True,
                         name=f"waiter-{self.script_id}").start()
        return self.proc.pid

    def _pump(self, stream, stype: str):
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                chunk = stream.read(1024)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if not text:
                    continue
                self.last_output_ts = time.time()
                done = []
                with self._buf_lock:
                    buf = self._bufs[stype] + text
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        done.append(line.rstrip("\r"))
                    self._bufs[stype] = buf
                    if stype == "out":
                        with self.pending_lock:
                            self.pending = buf[-MAX_PENDING:]
                for line in done:
                    if stype == "out":
                        self.emit("out", line, detect_level(line, "out"))
                        self._recent_out.append(line)
                        self._recent_out = self._recent_out[-30:]
                    else:
                        self.emit("err", line, "error")
                # pas de newline : c'est potentiellement un prompt, on attend la suite
        except Exception:
            pass
        finally:
            tail = decoder.decode(b"", final=True)
            rest = ""
            with self._buf_lock:
                rest = (self._bufs[stype] + tail).strip()
                self._bufs[stype] = ""
                if stype == "out":
                    with self.pending_lock:
                        self.pending = ""
            if rest:
                if stype == "out":
                    self.emit("out", rest, detect_level(rest, "out"))
                else:
                    self.emit("err", rest, "error")
            try:
                stream.close()
            except Exception:
                pass

    # ---------------- détection attente input ----------------

    def _blocked_on_pipe(self) -> bool | None:
        """True si le process est bloqué en lecture pipe (input()), None si inconnu."""
        if not self.proc or self.proc.poll() is not None:
            return False
        pid = self.proc.pid
        task_dir = f"/proc/{pid}/task"
        try:
            if os.path.isdir(task_dir):
                for tid in os.listdir(task_dir):
                    try:
                        with open(f"{task_dir}/{tid}/wchan") as f:
                            if f.read().strip() in PIPE_WCHANS:
                                return True
                    except OSError:
                        continue
                return False
            with open(f"/proc/{pid}/wchan") as f:
                return f.read().strip() in PIPE_WCHANS
        except OSError:
            return None  # pas de /proc (non-Linux)

    def is_alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)

    def is_waiting(self) -> bool:
        if not self.is_alive() or not self.stdin_open:
            return False
        idle = time.time() - self.last_output_ts
        if idle < 0.8:
            return False
        pipe = self._blocked_on_pipe()
        if pipe is True:
            return True
        if pipe is False:
            return False
        # Fallback sans /proc : prompt non vide + inactivité longue
        with self.pending_lock:
            pend = self.pending.strip()
        return bool(pend) and idle > 2.5

    def current_prompt(self) -> str:
        with self.pending_lock:
            return self.pending.strip("\r\n")[-500:]

    def prompt_options(self) -> list:
        from analyzer import extract_options as _eo
        return _eo(self.current_prompt(), self._recent_out[-12:])

    # ---------------- stdin ----------------

    def send_input(self, text: str, auto: bool = False) -> tuple[bool, str]:
        if not self.is_alive():
            return False, "Le script n'est plus en cours"
        if not self.stdin_open:
            return False, "L'entrée du script est fermée"
        with self._buf_lock:
            self._bufs["out"] = ""
            with self.pending_lock:
                prompt = self.pending
                self.pending = ""
        prompt = prompt.strip("\r\n")
        # Figer le prompt en attente comme ligne de log (Q visible dans l'historique)
        if prompt:
            self.emit("out", prompt, "dim")
        try:
            with self._stdin_lock:
                self.proc.stdin.write((text + "\n").encode("utf-8", errors="replace"))
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as e:
            self.stdin_open = False
            return False, f"Impossible d'écrire dans le script : {e}"
        self.answers_sent += 1
        self._recent_out = []  # le contexte menu repart de zéro après chaque réponse
        self.qa.append({"prompt": prompt, "answer": text, "auto": auto,
                        "t": utcnow_iso()})
        self.emit("in", ("🤖 " if auto else "") + text, "info")
        self.last_output_ts = time.time()  # anti-rebond détection
        return True, "Réponse envoyée"

    def _feeder(self):
        """Envoie les réponses pré-remplies au fil des questions détectées."""
        deadline = time.time() + 30 * 60
        while self.auto_answers and time.time() < deadline:
            if self._feeder_stop.is_set() or not self.is_alive():
                return
            time.sleep(0.25)
            try:
                if self.is_waiting():
                    ans = self.auto_answers.pop(0)
                    self.send_input(ans, auto=True)
                    time.sleep(0.4)
            except Exception:
                continue
        if self.auto_answers and self.is_alive():
            self.emit("sys", f"ℹ️ {len(self.auto_answers)} réponse(s) pré-remplie(s) restante(s) ignorée(s) (plus de question détectée)", "warn")

    # ---------------- fin ----------------

    def _waiter(self):
        try:
            rc = self.proc.wait()
        except Exception:
            rc = None
        self.stdin_open = False
        self._feeder_stop.set()
        # laisser les pumps vider les tuyaux
        time.sleep(0.6)
        dur = time.time() - self.start_ts
        try:
            info = {"exit_code": rc, "duration_s": round(dur, 1),
                    "stopped_by_user": self.stop_requested,
                    "answers_sent": self.answers_sent, "qa": self.qa}
            if self.stop_requested:
                self.emit("sys", f"⏹ Arrêté (durée {dur:.0f}s)", "warn")
                info["reason"] = "stopped"
            elif rc == 0:
                self.emit("sys", f"✅ Terminé avec succès en {dur:.0f}s", "success")
                info["reason"] = "success"
            else:
                tail = read_jsonl_tail_text(jsonl_path(self.sdir))
                diag = diagnose_crash(tail, rc)
                info["reason"] = "crash"
                info["diagnosis"] = diag
                if diag and diag.get("exception"):
                    self.emit("sys", f"❌ Crash (code {rc}) — {diag['exception']}: {diag.get('message','')[:150]}", "error")
                else:
                    self.emit("sys", f"❌ Arrêté avec le code {rc}", "error")
            if self.on_exit:
                try:
                    self.on_exit(self.script_id, info)
                except Exception:
                    traceback.print_exc()
        finally:
            self.finished.set()

    def stop(self, timeout: float = 8.0):
        self.stop_requested = True
        self._feeder_stop.set()
        p = self.proc
        if not p or p.poll() is not None:
            return True
        try:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except Exception:
                p.terminate()
            try:
                p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except Exception:
                    p.kill()
                p.wait(timeout=5)
        except Exception:
            pass
        try:
            if p.stdin:
                p.stdin.close()
        except Exception:
            pass
        self.stdin_open = False
        return True


# ------------------------------------------------- registre global ---

LIVE: dict[str, LiveProcess] = {}
LIVE_LOCK = threading.Lock()


def get_live(script_id: str) -> LiveProcess | None:
    with LIVE_LOCK:
        return LIVE.get(script_id)


def register(lp: LiveProcess):
    with LIVE_LOCK:
        LIVE[lp.script_id] = lp


def unregister(script_id: str):
    with LIVE_LOCK:
        LIVE.pop(script_id, None)


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
