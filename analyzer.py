"""
zach-runner — Moteur d'analyse statique et diagnostic.

- Scanne le code source : questions (input/read/prompt), menus a choix,
  imports, risques. Supporte Python (AST), Node.js et Shell (regex).
- Diagnostique les crashs (tracebacks Python, erreurs Node) avec des
  suggestions en francais.
"""
import ast
import re
import sys
from pathlib import Path

try:
    STDLIB = set(sys.stdlib_module_names)
except AttributeError:  # Python < 3.10 (ne devrait pas arriver)
    STDLIB = set()

# Import Python -> paquet pip (quand le nom differe)
PIP_MAP = {
    "PIL": "Pillow", "cv2": "opencv-python", "sklearn": "scikit-learn",
    "skimage": "scikit-image", "bs4": "beautifulsoup4", "yaml": "PyYAML",
    "dotenv": "python-dotenv", "telegram": "python-telegram-bot",
    "discord": "discord.py", "serial": "pyserial", "usb": "pyusb",
    "Crypto": "pycryptodome", "OpenSSL": "pyOpenSSL", "jwt": "PyJWT",
    "socks": "PySocks", "attr": "attrs", "dns": "dnspython",
    "paramiko": "paramiko", "docx": "python-docx", "pptx": "python-pptx",
    "selenium": "selenium", "pyautogui": "pyautogui", "ccxt": "ccxt",
    "ta": "ta", "yfinance": "yfinance", "tweepy": "tweepy", "praw": "praw",
    "psycopg2": "psycopg2-binary", "MySQLdb": "mysqlclient",
    "dateutil": "python-dateutil", "lxml": "lxml",
}

# Modules natifs Node.js (jamais a installer via npm)
NODE_BUILTINS = {
    "fs", "path", "os", "http", "https", "url", "util", "events",
    "stream", "crypto", "child_process", "cluster", "dgram", "dns",
    "net", "tls", "zlib", "readline", "repl", "vm", "worker_threads",
    "perf_hooks", "async_hooks", "buffer", "querystring", "string_decoder",
    "timers", "tty", "v8", "process", "assert", "constants", "module",
    "punycode", "sys", "domain", "freelist",
}

SENSITIVE_RE = re.compile(
    r"(pass\s*word|mot\s*de\s*passe|\bmdp\b|secret|token|api[\s_\-]*key|"
    r"clé[\s_\-]*|clef|private|pwd\b|code\s*pin)", re.IGNORECASE)

MENU_LINE_RE = re.compile(
    r"^\s*(?:[\-\*>\>]?\s*)(\d{1,2}|[a-zA-Z])\s*[.)\-:>]\s*(.+?)\s*$")

YES_NO_RE = re.compile(
    r"[\[\(]\s*(o|y|yes|oui)\s*[/,|]\s*(n|no|non)\s*[\]\)]", re.IGNORECASE)


def _const_str(node):
    """Extrait une chaine constante d'un noeud AST (gere + et f-strings)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            else:
                parts.append("…")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        l, r = _const_str(node.left), _const_str(node.right)
        if l is not None and r is not None:
            return l + r
    return None


def extract_options(prompt: str, context_texts: list) -> list:
    """Detecte les choix proposes : [1/2], (1-3), menus '1. xxx', o/n..."""
    blob = "\n".join(context_texts + [prompt or ""])
    options: list = []

    def add(opts):
        for o in opts:
            o = str(o).strip().strip("'\"")
            if o and o not in options and len(o) <= 20 and len(options) < 12:
                options.append(o)

    # 0. Lignes de menu "1. Demarrer" / "2 - Quitter" / "a) aide"
    for line in blob.splitlines():
        m = MENU_LINE_RE.match(line)
        if m and len(line.strip()) < 120:
            add([m.group(1)])

    # 1. Crochets : [1/2], (y/n), [oui/non]
    for m in re.finditer(r"[\[\(]\s*([^\]\)\(\)\n]{1,40})\s*[\]\)]", blob):
        inner = m.group(1)
        if re.search(r"[/,|]", inner):
            parts = re.split(r"\s*[/,|]\s*", inner)
            if 2 <= len(parts) <= 8 and all(len(p) <= 12 for p in parts):
                add(parts)

    # 2. Plages : (1-3), 1-4, entre 1 et 5
    for m in re.finditer(r"(?:^|[^\d])(\d)\s*(?:-|–|à|a|to|\.\.\.)\s*(\d)(?:$|[^\d])", blob):
        a, b = int(m.group(1)), int(m.group(2))
        if 0 <= a < b <= 12:
            add([str(i) for i in range(a, b + 1)])
    for m in re.finditer(r"entre\s+(\d)\s+et\s+(\d)", blob, re.IGNORECASE):
        a, b = int(m.group(1)), int(m.group(2))
        if 0 <= a < b <= 12:
            add([str(i) for i in range(a, b + 1)])

    # 3. "1 ou 2", "1, 2 ou 3"
    for m in re.finditer(r"(\d(?:\s*,\s*\d)*)\s+ou\s+(\d)", blob, re.IGNORECASE):
        add(re.split(r"\s*,\s*", m.group(1)) + [m.group(2)])

    # 4. oui/non explicite
    m = YES_NO_RE.search(blob)
    if m:
        add([m.group(1).lower(), m.group(2).lower()])
    elif re.search(r"\(?(oui|yes)\)?\s*/\s*\(?(non|no)\)?", blob, re.IGNORECASE):
        add(["oui", "non"])

    return options


def _empty_report(language: str, code: str) -> dict:
    return {
        "ok": True, "language": language, "eco": None,
        "syntax_error": None, "lines": len(code.splitlines()),
        "inputs": [], "imports": {"stdlib": [], "thirdparty": [], "relative": []},
        "suggested_requirements": [], "features": {}, "risks": [],
        "summary": "", "prints": 0,
    }


def _finalize_inputs(raw_inputs: list, context_fn) -> list:
    """Enrichit les questions brutes : label, contexte, options, sensibilite."""
    inputs = []
    for i, ic in enumerate(raw_inputs):
        ctx = context_fn(i, ic)
        prompt = ic.get("prompt", "")
        label = prompt
        if not label.strip() and ctx:
            label = ctx[-1]
        options = extract_options(prompt, ctx)
        sensitive = bool(ic.get("sensitive")) or bool(
            SENSITIVE_RE.search(prompt + "\n" + "\n".join(ctx)))
        inputs.append({
            "index": i + 1, "line": ic.get("line", 0),
            "prompt": prompt, "label": label,
            "context": ctx, "options": options, "sensitive": sensitive,
        })
    return inputs


def _summarize(report: dict, third: list, feats: dict) -> str:
    bits = []
    inputs = report["inputs"]
    if inputs:
        n = len(inputs)
        bits.append(f"{n} question{'s' if n > 1 else ''} a remplir")
        n_menu = sum(1 for x in inputs if x["options"])
        if n_menu:
            bits.append(f"{n_menu} menu{'s' if n_menu > 1 else ''} a choix detecte{'s' if n_menu > 1 else ''}")
    else:
        bits.append("Aucune question detectee — demarrage direct")
    if third:
        shown = ", ".join(third[:4]) + ("…" if len(third) > 4 else "")
        bits.append(f"{len(third)} dependance{'s' if len(third) > 1 else ''} externe{'s' if len(third) > 1 else ''} ({shown})")
    if feats.get("webhook_payload"):
        bits.append("Lit les donnees du webhook (WEBHOOK_PAYLOAD)")
    if feats.get("loop"):
        bits.append("Tourne en continu")
    return " — ".join(bits)


# ------------------------------------------------------- Python (AST) ---

def analyze_python(code: str, local_modules: set | None = None) -> dict:
    report = _empty_report("python", code)
    local_modules = local_modules or set()
    report["eco"] = "pip"
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        report["ok"] = False
        report["syntax_error"] = {
            "line": e.lineno, "col": e.offset, "msg": e.msg,
            "text": (e.text or "").strip()[:200],
        }
        report["summary"] = f"Erreur de syntaxe ligne {e.lineno} : {e.msg}"
        return report

    raw_inputs, print_calls = [], []
    third, std, rel = set(), set(), set()
    feats = {"network": False, "files": False, "env": False,
             "webhook_payload": "WEBHOOK_PAYLOAD" in code,
             "loop": False, "sleep": False, "subprocess": False, "args": False}
    risks = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = ""
            if isinstance(node.func, ast.Name):
                fname = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fname = node.func.attr
            if fname == "input":
                prompt = _const_str(node.args[0]) if node.args else ""
                raw_inputs.append({"line": getattr(node, "lineno", 0),
                                   "prompt": prompt or ""})
            elif fname == "print":
                for a in node.args:
                    s = _const_str(a)
                    if s:
                        print_calls.append({"line": getattr(node, "lineno", 0),
                                            "text": s})
                        break
            elif fname == "getpass":
                prompt = _const_str(node.args[0]) if node.args else "Password: "
                raw_inputs.append({"line": getattr(node, "lineno", 0),
                                   "prompt": prompt or "Password: ",
                                   "sensitive": True})
            elif fname == "readline":
                raw_inputs.append({"line": getattr(node, "lineno", 0),
                                   "prompt": "", "via": "stdin"})
            elif fname in ("system", "popen", "run", "call", "check_output"):
                feats["subprocess"] = True
            elif fname == "open":
                feats["files"] = True
            elif fname == "sleep":
                feats["sleep"] = True
            elif fname == "getenv":
                feats["env"] = True
        elif isinstance(node, ast.Import):
            for a in node.names:
                top = (a.name or "").split(".")[0]
                if top in local_modules:
                    rel.add(top)
                else:
                    (std if top in STDLIB else third).add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                rel.add("." * node.level + (node.module or ""))
            elif node.module:
                top = node.module.split(".")[0]
                if top in local_modules:
                    rel.add(top)
                else:
                    (std if top in STDLIB else third).add(top)
        elif isinstance(node, ast.While):
            test = node.test
            if (isinstance(test, ast.Constant) and test.value is True):
                feats["loop"] = True

    if re.search(r"os\.environ|os\.getenv|getenv\(", code):
        feats["env"] = True
    if "sys.argv" in code or "argparse" in code:
        feats["args"] = True
    if re.search(r"\b(requests|httpx|urllib|socket|websocket|aiohttp)\b", code):
        feats["network"] = True
    if re.search(r"\b(eval|exec)\s*\(", code):
        risks.append({"level": "warn",
                      "text": "Utilise eval()/exec() — verifiez la provenance du code."})
    if feats["subprocess"]:
        risks.append({"level": "info",
                      "text": "Execute des commandes systeme — fonctionne sur le serveur."})
    if feats["loop"]:
        risks.append({"level": "info",
                      "text": "Contient une boucle infinie (while True) — tourne jusqu'a Stop."})
    if re.search(r"/dev/tty|getpass", code):
        risks.append({"level": "warn",
                      "text": "getpass/tty detecte : sans terminal, preferez input() classique."})

    print_calls.sort(key=lambda p: p["line"])
    raw_inputs.sort(key=lambda p: p["line"])
    prev = [None]

    def context_fn(i, ic):
        lo = prev[0] if prev[0] is not None else ic["line"] - 14
        ctx = [p["text"] for p in print_calls
               if lo < p["line"] < ic["line"]
               and ic["line"] - p["line"] <= 14][-6:]
        prev[0] = ic["line"]
        return ctx

    report["inputs"] = _finalize_inputs(raw_inputs, context_fn)
    report["prints"] = len(print_calls)
    report["imports"] = {"stdlib": sorted(std), "thirdparty": sorted(third),
                         "relative": sorted(rel)}
    report["suggested_requirements"] = [PIP_MAP.get(m, m) for m in sorted(third)]
    report["features"] = feats
    report["risks"] = risks
    report["summary"] = _summarize(report, sorted(third), feats)
    return report


# ------------------------------------------------- Node.js (regex) ---

_JS_Q_RE = re.compile(
    r"(?:\.question|prompt|ask|input)\(\s*['\"`](.*?)['\"`]", re.DOTALL)
_JS_Q_NOLIT_RE = re.compile(r"\.question\(\s*(?![\"\'])")
_JS_REQ_RE = re.compile(r"require\(\s*['\"`]([^'\"`]+)['\"`]\s*\)")
_JS_IMP_RE = re.compile(r"(?:from\s+['\"`]([^'\"`]+)['\"`]|import\(\s*['\"`]([^'\"`]+)['\"`]\s*\))")
_JS_LOG_RE = re.compile(r"console\.(?:log|info|warn|error)\(\s*['\"`](.*?)['\"`]", re.DOTALL)


def analyze_node(code: str) -> dict:
    report = _empty_report("node", code)
    report["eco"] = "npm"
    lines = code.splitlines()
    raw_inputs, print_calls = [], []
    for m in _JS_Q_RE.finditer(code):
        prompt = m.group(1).strip().replace("\\n", "\n")[:300]
        line = code.count("\n", 0, m.start()) + 1
        raw_inputs.append({"line": line, "prompt": prompt})
    for m in _JS_Q_NOLIT_RE.finditer(code):
        line = code.count("\n", 0, m.start()) + 1
        if not any(abs(r["line"] - line) < 2 for r in raw_inputs):
            raw_inputs.append({"line": line, "prompt": ""})
    for m in _JS_LOG_RE.finditer(code):
        line = code.count("\n", 0, m.start()) + 1
        print_calls.append({"line": line,
                            "text": m.group(1).replace("\\n", "\n")[:500]})
    raw_inputs.sort(key=lambda r: r["line"])
    print_calls.sort(key=lambda p: p["line"])
    prev = [None]

    def context_fn(i, ic):
        lo = prev[0] if prev[0] is not None else ic["line"] - 14
        ctx = [p["text"] for p in print_calls
               if lo < p["line"] < ic["line"]
               and ic["line"] - p["line"] <= 14][-6:]
        prev[0] = ic["line"]
        return ctx

    third, std, rel = set(), set(), set()
    for m in _JS_REQ_RE.finditer(code):
        mod = m.group(1).strip()
        _classify_js_mod(mod, third, std, rel)
    for m in _JS_IMP_RE.finditer(code):
        mod = (m.group(1) or m.group(2) or "").strip()
        _classify_js_mod(mod, third, std, rel)

    feats = {
        "network": bool(re.search(r"\b(fetch|axios|http|https|ws|WebSocket)\b", code)),
        "files": bool(re.search(r"\bfs\b|readFile|writeFile", code)),
        "env": "process.env" in code,
        "webhook_payload": "WEBHOOK_PAYLOAD" in code,
        "loop": bool(re.search(r"while\s*\(\s*true\s*\)|setInterval|createServer|\.listen\(", code)),
        "sleep": "setTimeout" in code,
        "subprocess": bool(re.search(r"child_process|exec\(|spawn\(", code)),
        "args": "process.argv" in code,
    }
    risks = []
    if feats["subprocess"]:
        risks.append({"level": "info",
                      "text": "Execute des commandes systeme — fonctionne sur le serveur."})
    if feats["loop"]:
        risks.append({"level": "info",
                      "text": "Processus longue duree detecte (serveur, intervalle ou boucle)."})
    if re.search(r"\beval\s*\(", code):
        risks.append({"level": "warn",
                      "text": "Utilise eval() — verifiez la provenance du code."})

    report["inputs"] = _finalize_inputs(raw_inputs, context_fn)
    report["prints"] = len(print_calls)
    report["imports"] = {"stdlib": sorted(std), "thirdparty": sorted(third),
                         "relative": sorted(rel)}
    report["suggested_requirements"] = sorted(third)
    report["features"] = feats
    report["risks"] = risks
    report["summary"] = _summarize(report, sorted(third), feats)
    return report


def _classify_js_mod(mod: str, third: set, std: set, rel: set):
    if not mod:
        return
    if mod.startswith(".") or mod.startswith("/"):
        rel.add(mod)
        return
    base = mod.split("/")[0]
    if base.startswith("@") and "/" in mod:  # scope npm
        base = "/".join(mod.split("/")[:2])
    pkg = mod[5:] if mod.startswith("node:") else base
    if (mod.startswith("node:") or pkg in NODE_BUILTINS):
        std.add(pkg)
    else:
        third.add(base)


# ------------------------------------------------- Shell (regex) ---

_SH_READP_RE = re.compile(
    r"read\s+(?:-[a-zA-Z]+\s+)*-p\s*(?:\"([^\"]*)\"|'([^']*)'|(\S+))")
_SH_READ_RE = re.compile(r"^\s*read\s+(?!-)([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE)
_SH_ECHO_RE = re.compile(r"^\s*echo\s+(?:-e\s+)?[\"']?(.*?)[\"']?\s*$", re.MULTILINE)
_SH_SELECT_RE = re.compile(r"select\s+\w+\s+in\s+([^;]+);")


def analyze_shell(code: str) -> dict:
    report = _empty_report("shell", code)
    report["eco"] = None
    raw_inputs, print_calls = [], []
    for m in _SH_READP_RE.finditer(code):
        prompt = (m.group(1) or m.group(2) or m.group(3) or "").strip()[:300]
        line = code.count("\n", 0, m.start()) + 1
        raw_inputs.append({"line": line, "prompt": prompt})
    for m in _SH_READ_RE.finditer(code):
        line = code.count("\n", 0, m.start()) + 1
        if not any(abs(r["line"] - line) < 2 for r in raw_inputs):
            raw_inputs.append({"line": line, "prompt": ""})
    for m in _SH_SELECT_RE.finditer(code):
        line = code.count("\n", 0, m.start()) + 1
        items = [w.strip().strip("'\"") for w in m.group(1).split()
                 if w.strip()][:12]
        ps3 = re.search(r"PS3\s*=\s*[\"']?(.*?)[\"']?\s*$", code, re.MULTILINE)
        raw_inputs.append({"line": line,
                           "prompt": (ps3.group(1).strip() if ps3 else "Choix : ")[:100],
                           "select_options": items})
    for m in _SH_ECHO_RE.finditer(code):
        line = code.count("\n", 0, m.start()) + 1
        txt = m.group(1).strip()
        if txt:
            print_calls.append({"line": line, "text": txt[:500]})
    raw_inputs.sort(key=lambda r: r["line"])
    print_calls.sort(key=lambda p: p["line"])
    prev = [None]

    def context_fn(i, ic):
        lo = prev[0] if prev[0] is not None else ic["line"] - 14
        ctx = [p["text"] for p in print_calls
               if lo < p["line"] < ic["line"]
               and ic["line"] - p["line"] <= 14][-6:]
        prev[0] = ic["line"]
        return ctx

    inputs = _finalize_inputs(raw_inputs, context_fn)
    # Options des menus select (prioritaires sur l'extraction auto)
    for inp, raw in zip(inputs, raw_inputs):
        if raw.get("select_options") and not inp["options"]:
            inp["options"] = raw["select_options"][:12]

    feats = {
        "network": bool(re.search(r"\b(curl|wget|ssh|scp)\b", code)),
        "files": bool(re.search(r"cat\s|cp\s|mv\s|rm\s|mkdir|>|>>", code)),
        "env": bool(re.search(r"\$\{?\w+\}?", code)),
        "webhook_payload": "WEBHOOK_PAYLOAD" in code,
        "loop": bool(re.search(r"while\s+true|while\s*:|for\s+\w+\s+in", code)),
        "sleep": bool(re.search(r"\bsleep\b", code)),
        "subprocess": False, "args": bool(re.search(r"\$[1-9$@#]", code)),
    }
    risks = []
    if re.search(r"rm\s+-rf?\s+/\s|rm\s+-rf?\s+\*", code):
        risks.append({"level": "warn",
                      "text": "Commande de suppression dangereuse detectee — relisez le script."})
    if feats["loop"]:
        risks.append({"level": "info",
                      "text": "Boucle detectee — tourne jusqu'a Stop."})

    report["inputs"] = inputs
    report["prints"] = len(print_calls)
    report["features"] = feats
    report["risks"] = risks
    report["summary"] = _summarize(report, [], feats)
    return report


# ------------------------------------------------- dispatch ---

def analyze_generic(code: str) -> dict:
    report = _empty_report("unknown", code)
    report["summary"] = ("Type de fichier non analyse statiquement — "
                         "les questions seront gerees en direct dans la console.")
    return report


def analyze_source(code: str) -> dict:
    """Compatibilite : analyse Python (defaut historique)."""
    return analyze_python(code)


def analyze_file(filename: str, code: str, local_modules: set | None = None) -> dict:
    """Analyse selon l'extension du fichier."""
    ext = Path(filename or "").suffix.lower()
    if ext == ".py":
        return analyze_python(code, local_modules)
    if ext in (".js", ".mjs", ".cjs"):
        return analyze_node(code)
    if ext in (".sh", ".bash"):
        return analyze_shell(code)
    return analyze_generic(code)


# ------------------------------------------------- diagnostic crash ---

EXC_RE = re.compile(
    r"^([\w.]*?(?:Error|Exception|Warning)|KeyboardInterrupt|SystemExit|StopIteration)(?::\s*(.*))?$")
FILE_RE = re.compile(r'File "([^"]+)", line (\d+)')
MOD_RE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")
NODE_EXC_RE = re.compile(r"^(\w*Error)(?:\s*\[([\w_]+)\])?\s*:\s*(.*)$")
NODE_AT_RE = re.compile(r"at\s+(?:.*\()?([^()\s]+):(\d+):(\d+)\)?")
NODE_MOD_RE = re.compile(r"Cannot find (?:module|package) ['\"]([^'\"]+)['\"]")


def _diagnose_python(lines: list, exit_code) -> dict | None:
    exc, exc_msg, exc_idx = None, "", -1
    for i in range(len(lines) - 1, -1, -1):
        m = EXC_RE.match(lines[i].strip())
        if m:
            exc, exc_msg, exc_idx = m.group(1), (m.group(2) or "").strip()[:400], i
            break
    tb_start = -1
    for i in range(len(lines) - 1, -1, -1):
        if "Traceback (most recent call last)" in lines[i]:
            tb_start = i
            break
    if not exc:
        return None
    location = None
    for i in range(exc_idx - 1, max(exc_idx - 12, -1), -1):
        m = FILE_RE.search(lines[i])
        if m:
            location = {"file": m.group(1).split("/")[-1], "line": int(m.group(2))}
            break
    block = "\n".join(lines[tb_start:exc_idx + 1]) if tb_start >= 0 else ""
    short = exc.split(".")[-1]
    hint, action = "", None
    if short == "ModuleNotFoundError":
        m = MOD_RE.search(exc_msg)
        mod = m.group(1) if m else "?"
        pkg = pip_pkg(mod) if m else None
        hint = f"La librairie Python « {mod} » n'est pas installee sur le serveur."
        if pkg:
            action = {"type": "install", "manager": "pip", "package": pkg,
                      "label": f"Installer {pkg} (pip)"}
    elif short == "ImportError":
        hint = "Un import a echoue (librairie manquante ou nom mal orthographie)."
        m = MOD_RE.search(exc_msg)
        if m:
            pkg = pip_pkg(m.group(1))
            action = {"type": "install", "manager": "pip", "package": pkg,
                      "label": f"Installer {pkg} (pip)"}
    elif short == "FileNotFoundError":
        hint = ("Un fichier est introuvable. Rappel : le script tourne dans son dossier "
                "sur le serveur — les chemins absolus de votre PC n'existent pas la-bas.")
    elif short in ("SyntaxError", "IndentationError", "TabError"):
        hint = "Erreur de syntaxe Python. Corrigez-la dans l'editeur du site."
        action = {"type": "edit", "label": "Ouvrir l'editeur"}
    elif short == "NameError":
        hint = "Variable ou fonction inconnue — faute de frappe ou import oublie."
    elif short == "KeyError":
        hint = ("Cle de dictionnaire inexistante. Si les donnees viennent d'un webhook, "
                "verifiez le contenu recu dans l'onglet Webhooks.")
    elif short == "ValueError":
        hint = "Valeur inattendue (ex : int('abc'), mauvais format de donnee)."
    elif short == "TypeError":
        hint = "Operation entre types incompatibles."
    elif short == "AttributeError":
        hint = "Attribut ou methode inexistant sur cet objet."
    elif short == "IndexError":
        hint = "Index de liste hors limites."
    elif short == "EOFError":
        hint = ("Le script a demande une reponse (input()) mais n'en a recu aucune. "
                "Relancez-le et repondez dans la console du site.")
    elif short == "KeyboardInterrupt":
        hint = "Interrompu manuellement."
    elif "Connection" in short or "Timeout" in short or "URLError" in short:
        hint = "Probleme reseau : le serveur distant ne repond pas ou l'URL est incorrecte."
    elif short == "PermissionError":
        hint = "Permission refusee (ecriture hors du dossier autorise ?)."
    else:
        hint = "Erreur Python. La derniere ligne du bloc ci-dessous indique la cause."
    if location:
        hint += f" Emplacement : {location['file']} ligne {location['line']}."
    return {"found": True, "runtime": "python", "exception": short,
            "message": exc_msg[:400], "location": location,
            "block": block[-3000:], "hint": hint, "action": action}


def pip_pkg(mod: str) -> str:
    return PIP_MAP.get(mod.split(".")[0], mod.split(".")[0])


def _diagnose_node(lines: list, exit_code) -> dict | None:
    exc, exc_code, exc_msg, exc_idx = None, "", "", -1
    for i, line in enumerate(lines):
        m = NODE_EXC_RE.match(line.strip())
        if m and not line.strip().startswith("at "):
            exc, exc_code, exc_msg, exc_idx = m.group(1), m.group(2) or "", (m.group(3) or "").strip()[:400], i
            break
    if not exc:
        return None
    location = None
    for i in range(exc_idx + 1, min(exc_idx + 10, len(lines))):
        m = NODE_AT_RE.search(lines[i])
        if m and "node:internal" not in m.group(1):
            location = {"file": m.group(1).split("/")[-1], "line": int(m.group(2))}
            break
    block = "\n".join(lines[exc_idx:exc_idx + 8])
    hint, action = "", None
    m = NODE_MOD_RE.search(exc_msg)
    if m or exc_code in ("ERR_MODULE_NOT_FOUND", "MODULE_NOT_FOUND"):
        pkg = (m.group(1) if m else "").split("/")[0]
        if pkg.startswith("@") and m:
            pkg = "/".join(m.group(1).split("/")[:2])
        hint = f"Le paquet Node « {m.group(1) if m else '?'} » n'est pas installe."
        if pkg and not pkg.startswith("."):
            action = {"type": "install", "manager": "npm", "package": pkg,
                      "label": f"Installer {pkg} (npm)"}
    elif exc == "SyntaxError":
        hint = "Erreur de syntaxe JavaScript. Corrigez-la dans l'editeur du site."
        action = {"type": "edit", "label": "Ouvrir l'editeur"}
    elif exc == "ReferenceError":
        hint = "Variable ou fonction inconnue — faute de frappe ou import oublie."
    elif exc == "TypeError":
        hint = "Operation sur un type incompatible (souvent undefined ou null)."
    elif exc == "RangeError":
        hint = "Valeur hors limites (recursion infinie possible : stack overflow)."
    else:
        hint = "Erreur Node.js. La premiere ligne du bloc indique la cause."
    if location:
        hint += f" Emplacement : {location['file']} ligne {location['line']}."
    return {"found": True, "runtime": "node", "exception": exc,
            "message": exc_msg[:400], "location": location,
            "block": block[-3000:], "hint": hint, "action": action}


def diagnose_crash(log_text: str, exit_code=None) -> dict | None:
    """Analyse un log de crash -> diagnostic + action corrective eventuelle."""
    if not log_text or not log_text.strip():
        if exit_code not in (None, 0):
            return {"found": True, "exception": f"Code de sortie {exit_code}",
                    "message": "", "location": None, "block": "",
                    "hint": ("Arret sans message. Le script a pu appeler exit() avec un code "
                             "d'erreur, ou etre tue (memoire ?). Verifiez les dernieres lignes.")}
        return None
    lines = [l.rstrip("\n") for l in log_text.splitlines() if l.strip()]
    if not lines:
        return None
    if any("Traceback (most recent call last)" in l for l in lines):
        return _diagnose_python(lines, exit_code)
    node = _diagnose_node(lines, exit_code)
    if node:
        return node
    py = _diagnose_python(lines, exit_code)
    if py:
        return py
    if exit_code not in (None, 0):
        tail = "\n".join(lines[-6:])
        return {"found": True, "exception": f"Arret inattendu (code {exit_code})",
                "message": "", "location": None, "block": tail[-2000:],
                "hint": "Pas de trace d'erreur reconnue. Verifiez les dernieres lignes du log."}
    return None
