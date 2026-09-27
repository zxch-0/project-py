"""
PyRunner — Moteur d'analyse statique & diagnostic.
- Scanne le code source : questions input(), menus (1,2...), imports, risques.
- Diagnostique les crashs (tracebacks) avec suggestions en français.
"""
import ast
import re
import sys

try:
    STDLIB = set(sys.stdlib_module_names)
except AttributeError:  # Python < 3.10 (ne devrait pas arriver)
    STDLIB = set()

# Import -> paquet pip (quand le nom diffère)
PIP_MAP = {
    "PIL": "Pillow", "cv2": "opencv-python", "sklearn": "scikit-learn",
    "skimage": "scikit-image", "bs4": "beautifulsoup4", "yaml": "PyYAML",
    "dotenv": "python-dotenv", "telegram": "python-telegram-bot",
    "discord": "discord.py", "serial": "pyserial", "usb": "pyusb",
    "Crypto": "pycryptodome", "OpenSSL": "pyOpenSSL", "jwt": "PyJWT",
    "socks": "PySocks", "gi": "PyGObject", "wx": "wxPython",
    "psycopg2": "psycopg2-binary", "MySQLdb": "mysqlclient",
    "lxml": "lxml", "dateutil": "python-dateutil", "attr": "attrs",
    "dns": "dnspython", "Crypto": "pycryptodome", "paramiko": "paramiko",
    "docx": "python-docx", "pptx": "python-pptx", "selenium": "selenium",
    "pyautogui": "pyautogui", "ccxt": "ccxt", "ta": "ta",
    "yfinance": "yfinance", "tweepy": "tweepy", "praw": "praw",
}

SENSITIVE_RE = re.compile(
    r"(pass\s*word|mot\s*de\s*passe|\bmdp\b|secret|token|api[\s_\-]*key|"
    r"clé[\s_\-]*|clef|private|pwd\b|code\s*pin)", re.IGNORECASE)

MENU_LINE_RE = re.compile(
    r"^\s*(?:[\-\*\•\>]?\s*)(\d{1,2}|[a-zA-Z])\s*[.)\-:：»>]\s*(.+?)\s*$")

YES_NO_RE = re.compile(
    r"[\[\(]\s*(o|y|yes|oui)\s*[/,|]\s*(n|no|non)\s*[\]\)]", re.IGNORECASE)


def _const_str(node):
    """Extrait une chaîne constante d'un nœud AST (gère + et f-strings simples)."""
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
    """Détecte les choix proposés : [1/2], (1-3), menus '1. xxx', o/n..."""
    blob = "\n".join(context_texts + [prompt or ""])
    options: list = []

    def add(opts):
        for o in opts:
            o = str(o).strip()
            if o and o not in options and len(o) <= 20 and len(options) < 12:
                options.append(o)

    # 0. Lignes de menu "1. Démarrer" / "2 - Quitter" / "a) aide"
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

    # 2. Plages : (1-3), 1-4, entre 1 et 5, de 1 à 4
    for m in re.finditer(r"(?:^|[^\d])(\d)\s*(?:-|–|à|a|to|…|\.\.\.)\s*(\d)(?:$|[^\d])", blob):
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
    if YES_NO_RE.search(blob):
        m = YES_NO_RE.search(blob)
        add([m.group(1).lower(), m.group(2).lower()])
    elif re.search(r"\(?(oui|yes)\)?\s*/\s*\(?(non|no)\)?", blob, re.IGNORECASE):
        add(["oui", "non"])

    return options


def analyze_source(code: str) -> dict:
    """Analyse complète d'un script. Retourne un rapport structuré."""
    report = {
        "ok": True, "syntax_error": None, "lines": len(code.splitlines()),
        "inputs": [], "imports": {"stdlib": [], "thirdparty": [], "relative": []},
        "suggested_requirements": [], "features": {}, "risks": [],
        "summary": "", "prints": 0,
    }
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        report["ok"] = False
        report["syntax_error"] = {
            "line": e.lineno, "col": e.offset, "msg": e.msg,
            "text": (e.text or "").strip()[:200],
        }
        report["summary"] = f"⛔ Erreur de syntaxe ligne {e.lineno} : {e.msg}"
        return report

    input_calls, print_calls = [], []
    imports_third, imports_std, imports_rel = set(), set(), set()
    feats = {"network": False, "files": False, "env": False,
             "webhook_payload": False, "loop": False, "sleep": False,
             "subprocess": False, "args": False}
    risks = []
    webhook_const = "WEBHOOK_PAYLOAD" in code
    if webhook_const:
        feats["webhook_payload"] = True

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = ""
            if isinstance(node.func, ast.Name):
                fname = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fname = node.func.attr
            if fname == "input":
                prompt = _const_str(node.args[0]) if node.args else ""
                input_calls.append({"line": getattr(node, "lineno", 0),
                                    "prompt": prompt or ""})
            elif fname == "print":
                for a in node.args:
                    s = _const_str(a)
                    if s:
                        print_calls.append({"line": getattr(node, "lineno", 0),
                                            "text": s})
                        break
            elif fname in ("getpass",):
                prompt = _const_str(node.args[0]) if node.args else "Password: "
                input_calls.append({"line": getattr(node, "lineno", 0),
                                    "prompt": prompt or "Password: ",
                                    "sensitive": True})
            elif fname == "readline":
                input_calls.append({"line": getattr(node, "lineno", 0),
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
                (imports_std if top in STDLIB else imports_third).add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                imports_rel.add("." * node.level + (node.module or ""))
            elif node.module:
                top = node.module.split(".")[0]
                (imports_std if top in STDLIB else imports_third).add(top)
        elif isinstance(node, ast.While):
            if isinstance(node.test, ast.Constant) and node.test.value is True:
                feats["loop"] = True
            elif isinstance(node.test, ast.NameConstant) and node.test.value is True:
                feats["loop"] = True
        elif isinstance(node, ast.Subscript):
            pass

    # os.environ / sys.argv détection textuelle simple
    if re.search(r"os\.environ|os\.getenv|getenv\(", code):
        feats["env"] = True
    if "sys.argv" in code or "argparse" in code:
        feats["args"] = True
    if re.search(r"\b(requests|httpx|urllib|socket|websocket|aiohttp)\b", code):
        feats["network"] = True
    if re.search(r"\b(eval|exec)\s*\(", code):
        risks.append({"level": "warn", "text": "⚠️ Utilise eval()/exec() — vérifie la provenance du code."})
    if feats["subprocess"]:
        risks.append({"level": "info", "text": "💡 Exécute des commandes système (subprocess/os) — fonctionnera sur le serveur Render."})
    if feats["loop"]:
        risks.append({"level": "info", "text": "🔁 Contient une boucle infinie (while True) — tourne en continu jusqu'à Stop."})
    if re.search(r"/dev/tty|getpass", code):
        risks.append({"level": "warn", "text": "⚠️ getpass/tty détecté : sans terminal, préfère input() classique."})

    # Contexte menu pour chaque input : les prints juste avant
    print_calls.sort(key=lambda p: p["line"])
    input_calls.sort(key=lambda p: p["line"])
    inputs = []
    prev_input_line = None
    for i, ic in enumerate(input_calls):
        # Contexte = prints affichés depuis la question précédente (le menu
        # juste au-dessus), pour ne pas rattacher un vieux menu lointain.
        lo = prev_input_line if prev_input_line is not None else ic["line"] - 14
        ctx = [p["text"] for p in print_calls
               if lo < p["line"] < ic["line"]
               and ic["line"] - p["line"] <= 14][-6:]
        prev_input_line = ic["line"]
        prompt = ic.get("prompt", "")
        # Si pas de prompt, chercher un print juste avant (= question posée via print)
        label = prompt
        if not label.strip() and ctx:
            label = ctx[-1]
        options = extract_options(prompt, ctx)
        sensitive = bool(ic.get("sensitive")) or bool(SENSITIVE_RE.search(prompt + "\n" + "\n".join(ctx)))
        inputs.append({
            "index": i + 1, "line": ic["line"],
            "prompt": prompt, "label": label,
            "context": ctx, "options": options, "sensitive": sensitive,
        })

    report["inputs"] = inputs
    report["prints"] = len(print_calls)
    report["imports"] = {"stdlib": sorted(imports_std),
                         "thirdparty": sorted(imports_third),
                         "relative": sorted(imports_rel)}
    report["suggested_requirements"] = [PIP_MAP.get(m, m) for m in sorted(imports_third)]
    report["features"] = feats
    report["risks"] = risks

    # Résumé intelligent
    bits = []
    if inputs:
        bits.append(f"💬 {len(inputs)} question{'s' if len(inputs) > 1 else ''} à remplir")
        n_menu = sum(1 for x in inputs if x["options"])
        if n_menu:
            bits.append(f"📋 {n_menu} menu{'s' if n_menu > 1 else ''} à choix détecté{'s' if n_menu > 1 else ''}")
    else:
        bits.append("🚀 Aucune question détectée — démarrage direct")
    if imports_third:
        bits.append(f"📦 {len(imports_third)} librairie{'s' if len(imports_third) > 1 else ''} externe{'s' if len(imports_third) > 1 else ''} ({', '.join(sorted(imports_third)[:4])}{'…' if len(imports_third) > 4 else ''})")
    if feats["webhook_payload"]:
        bits.append("🔔 Lit les données du webhook (WEBHOOK_PAYLOAD)")
    if feats["loop"]:
        bits.append("🔁 Tourne en continu")
    report["summary"] = " · ".join(bits)
    return report


# ------------------------------------------------- diagnostic crash ---

EXC_RE = re.compile(r"^([\w.]*?(?:Error|Exception|Warning)|KeyboardInterrupt|SystemExit|StopIteration)(?::\s*(.*))?$")
FILE_RE = re.compile(r'File "([^"]+)", line (\d+)')
MOD_RE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")


def diagnose_crash(log_text: str, exit_code=None) -> dict | None:
    """Analyse un log de crash -> diagnostic FR + action corrective éventuelle."""
    if not log_text or not log_text.strip():
        return {"found": False, "exception": f"Code de sortie {exit_code}",
                "hint": "Le script s'est arrêté sans message. Vérifie les logs complets."}
    lines = [l.rstrip("\n") for l in log_text.splitlines() if l.strip()]
    if not lines:
        return None

    # Dernière ligne d'exception
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

    location = None
    if exc_idx > 0:
        for i in range(exc_idx - 1, max(exc_idx - 12, -1), -1):
            m = FILE_RE.search(lines[i])
            if m:
                location = {"file": m.group(1).split("/")[-1], "line": int(m.group(2))}
                break

    block = "\n".join(lines[tb_start:exc_idx + 1]) if tb_start >= 0 and exc_idx >= 0 else ""

    if not exc:
        # Pas de traceback : crash silencieux ?
        if exit_code not in (None, 0):
            return {"found": True, "exception": f"Arrêt inattendu (code {exit_code})",
                    "message": "", "location": None, "block": block[-2000:],
                    "hint": "Pas de traceback : le script a pu appeler sys.exit() avec un code d'erreur, ou être tué (mémoire ?). Vérifie les dernières lignes du log."}
        return None

    short = exc.split(".")[-1]
    hint, action = "", None
    if short == "ModuleNotFoundError":
        m = MOD_RE.search(exc_msg)
        pkg = PIP_MAP.get(m.group(1).split(".")[0], m.group(1).split(".")[0]) if m else None
        mod = m.group(1) if m else "?"
        hint = f"La librairie « {mod} » n'est pas installée sur le serveur."
        if pkg:
            action = {"type": "install", "package": pkg,
                      "label": f"📦 Installer {pkg}"}
    elif short == "ImportError":
        hint = "Un import a échoué. Souvent une librairie manquante ou un nom mal orthographié."
        m = MOD_RE.search(exc_msg)
        if m:
            pkg = PIP_MAP.get(m.group(1).split(".")[0], m.group(1).split(".")[0])
            action = {"type": "install", "package": pkg, "label": f"📦 Installer {pkg}"}
    elif short == "FileNotFoundError":
        hint = ("Un fichier est introuvable. Rappel : le script tourne dans son propre dossier "
                "sur le serveur — les chemins de ton PC (C:\\, /Users/…) n'existent pas là-bas. "
                "Utilise des chemins relatifs ou uploade les fichiers nécessaires.")
    elif short in ("SyntaxError", "IndentationError", "TabError"):
        hint = "Erreur de syntaxe Python. Corrige-la directement dans l'éditeur du site."
        action = {"type": "edit", "label": "📝 Ouvrir l'éditeur"}
    elif short == "NameError":
        hint = "Variable ou fonction inconnue — souvent une faute de frappe ou un oubli d'import."
    elif short == "KeyError":
        hint = "Clé de dictionnaire inexistante. Si ça vient d'un webhook, vérifie le contenu reçu dans l'onglet Webhook."
    elif short in ("ValueError", "TypeError", "AttributeError", "IndexError"):
        hint = {"ValueError": "Valeur inattendue (ex : int('abc'), mauvais format de donnée).",
                "TypeError": "Opération entre types incompatibles.",
                "AttributeError": "Attribut/méthode inexistant sur cet objet.",
                "IndexError": "Index de liste hors limites."}[short]
    elif short == "EOFError":
        hint = ("Le script a demandé une réponse (input()) mais n'en a reçu aucune — "
                "l'entrée a été fermée. Relance-le et réponds à ses questions dans la console du site.")
    elif short == "KeyboardInterrupt":
        hint = "Interrompu manuellement (équivalent Ctrl+C)."
    elif "Connection" in short or "Timeout" in short or "URLError" in short:
        hint = "Problème réseau : le serveur distant ne répond pas ou l'URL est incorrecte."
    elif short == "PermissionError":
        hint = "Permission refusée (écriture hors du dossier autorisé ?)."
    else:
        hint = "Erreur Python. Lis le bloc ci-dessous : la dernière ligne indique la cause."

    if location:
        hint += f" 📍 {location['file']} ligne {location['line']}."
    return {"found": True, "exception": short, "message": exc_msg[:400],
            "location": location, "block": block[-3000:], "hint": hint, "action": action}
