"""
zach-runner — Runtimes : detection, resolution, verification syntaxique.

Runtimes supportes :
  - python : scripts .py (interprete systeme, mode unbuffered)
  - node   : scripts .js/.mjs/.cjs (binaire systeme ou vendored ./vendor)
  - bash   : scripts .sh/.bash
  - custom : commande personnalisee tapee par l'utilisateur (php, ruby, deno...)

Le mode custom rend le site compatible avec absolument tout : si un
binaire existe sur le serveur, on peut l'executer (sans shell=True).
"""
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENDOR_DIR = BASE_DIR / "vendor"
VENDOR_NODE = VENDOR_DIR / "node" / "bin" / "node"
VENDOR_NPM = VENDOR_DIR / "node" / "bin" / "npm"

RUNTIME_LABELS = {
    "python": "Python",
    "node": "Node.js",
    "bash": "Shell",
    "custom": "Personnalise",
}

EXT_RUNTIME = {
    ".py": "python",
    ".js": "node", ".mjs": "node", ".cjs": "node",
    ".sh": "bash", ".bash": "bash",
}

SINGLE_FILE_EXTS = set(EXT_RUNTIME.keys())


def detect_runtime(filename: str) -> str | None:
    """Devine le runtime depuis l'extension. None si inconnu."""
    if not filename:
        return None
    return EXT_RUNTIME.get(Path(filename).suffix.lower())


def node_bin() -> str | None:
    """Chemin du binaire node (vendored prioritaire, sinon systeme)."""
    if VENDOR_NODE.exists() and os.access(VENDOR_NODE, os.X_OK):
        return str(VENDOR_NODE)
    return shutil.which("node")


def npm_bin() -> str | None:
    if VENDOR_NPM.exists() and os.access(VENDOR_NPM, os.X_OK):
        return str(VENDOR_NPM)
    return shutil.which("npm")


def bash_bin() -> str | None:
    return shutil.which("bash") or shutil.which("sh")


def runtime_available(runtime: str) -> tuple[bool, str]:
    """(disponible, version ou message)."""
    if runtime == "python":
        v = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        return True, f"Python {v}"
    if runtime == "node":
        nb = node_bin()
        if not nb:
            return False, "Node.js introuvable (ni systeme, ni vendor)"
        try:
            out = subprocess.run([nb, "--version"], capture_output=True,
                                 text=True, timeout=10)
            ver = (out.stdout or "").strip() or "?"
            src = "vendored" if str(nb).startswith(str(VENDOR_DIR)) else "systeme"
            return True, f"Node.js {ver} ({src})"
        except Exception as e:
            return False, f"Node.js illisible : {e}"
    if runtime == "bash":
        bb = bash_bin()
        if not bb:
            return False, "bash/sh introuvable"
        return True, f"Shell ({Path(bb).name})"
    if runtime == "custom":
        return True, "Commande personnalisee"
    return False, f"Runtime inconnu : {runtime}"


def all_runtimes_status() -> dict:
    return {r: {"ok": ok, "info": info}
            for r, (ok, info) in
            ((r, runtime_available(r)) for r in ("python", "node", "bash"))}


def build_argv(runtime: str, script_path: str, user_args: str = "",
               custom_cmd: str = "") -> tuple[list | None, str | None]:
    """Construit la ligne de commande. Retourne (argv, None) ou (None, erreur)."""
    extra: list = []
    if (user_args or "").strip():
        try:
            extra = shlex.split(user_args)
        except ValueError:
            extra = user_args.split()

    if runtime == "python":
        return [sys.executable, "-u", script_path] + extra, None

    if runtime == "node":
        nb = node_bin()
        if not nb:
            return None, "Node.js n'est pas installe sur ce serveur"
        return [nb, script_path] + extra, None

    if runtime == "bash":
        bb = bash_bin()
        if not bb:
            return None, "bash/sh introuvable sur ce serveur"
        return [bb, script_path] + extra, None

    if runtime == "custom":
        cmd = (custom_cmd or "").strip()
        if not cmd:
            return None, "Aucune commande personnalisee definie"
        try:
            parts = shlex.split(cmd)
        except ValueError as e:
            return None, f"Commande invalide : {e}"
        if not parts:
            return None, "Commande vide"
        if "{file}" in cmd:
            parts = [p.replace("{file}", script_path) for p in parts]
        else:
            parts = parts + [script_path]
        exe = parts[0]
        if "/" in exe:
            if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
                return None, f"Executable introuvable : {exe}"
        elif not shutil.which(exe):
            return None, f"Commande introuvable sur le serveur : {exe}"
        return parts + extra, None

    return None, f"Runtime inconnu : {runtime}"


def syntax_check(runtime: str, path: str,
                 custom_cmd: str = "") -> tuple[bool, str]:
    """Verification statique avant lancement. (ok, message)."""
    p = Path(path)
    if not p.exists():
        return False, f"Fichier introuvable : {p.name}"
    if runtime == "python":
        try:
            code = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return False, f"Lecture impossible : {e}"
        try:
            compile(code, p.name, "exec")
        except SyntaxError as e:
            return False, f"Erreur de syntaxe ligne {e.lineno} : {e.msg}"
        return True, "Syntaxe Python valide"

    if runtime == "node":
        nb = node_bin()
        if not nb:
            return False, "Node.js introuvable — verification impossible"
        try:
            proc = subprocess.run([nb, "--check", str(p)], capture_output=True,
                                  text=True, timeout=30)
        except Exception as e:
            return False, f"Verification node impossible : {e}"
        if proc.returncode != 0:
            err = ((proc.stderr or proc.stdout) or "").strip().splitlines()
            return False, "Erreur de syntaxe JS : " + " / ".join(err[:3])[:300]
        return True, "Syntaxe JS valide"

    if runtime == "bash":
        bb = bash_bin()
        if not bb:
            return False, "bash introuvable — verification impossible"
        try:
            proc = subprocess.run([bb, "-n", str(p)], capture_output=True,
                                  text=True, timeout=30)
        except Exception as e:
            return False, f"Verification shell impossible : {e}"
        if proc.returncode != 0:
            err = ((proc.stderr or proc.stdout) or "").strip().splitlines()
            return False, "Erreur de syntaxe shell : " + " / ".join(err[:3])[:300]
        return True, "Syntaxe shell valide"

    if runtime == "custom":
        return True, "Pas de verification statique (commande personnalisee)"

    return False, f"Runtime inconnu : {runtime}"
