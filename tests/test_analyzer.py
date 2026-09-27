"""Tests unitaires — analyzer.py"""
from analyzer import (
    analyze_file, analyze_node, analyze_python, analyze_shell,
    diagnose_crash, extract_options,
)


def test_python_menu_options():
    code = (
        'print("1 - Demarrer")\n'
        'print("2 - Quitter")\n'
        'choix = input("Votre choix (1-2) : ")\n'
        'nom = input("Votre nom : ")\n'
    )
    a = analyze_python(code)
    assert a["ok"] is True
    assert len(a["inputs"]) == 2
    assert a["inputs"][0]["options"] == ["1", "2"]
    # Le menu lointain ne doit pas contaminer la 2e question
    assert a["inputs"][1]["options"] == []


def test_python_brackets_and_range():
    assert extract_options("Continuer ? [o/n]", []) == ["o", "n"]
    assert extract_options("Choix (1-3) :", []) == ["1", "2", "3"]
    assert extract_options("1 ou 2 ?", []) == ["1", "2"]


def test_python_sensitive():
    a = analyze_python('mdp = input("Mot de passe : ")\n')
    assert a["inputs"][0]["sensitive"] is True


def test_python_imports_and_syntax_error():
    a = analyze_python("import os\nimport requests\nimport pandas\n")
    assert a["suggested_requirements"] == ["pandas", "requests"]
    assert "os" in a["imports"]["stdlib"]
    bad = analyze_python('print("oups"\n')
    assert bad["ok"] is False
    assert bad["syntax_error"]["line"] == 1


def test_python_loop_and_risks():
    a = analyze_python("while True:\n    print('x')\n")
    assert a["features"]["loop"] is True
    assert any("boucle" in r["text"] for r in a["risks"])


def test_node_scan():
    code = (
        "const fs = require('fs');\n"
        "const axios = require('axios');\n"
        "import x from './local.js';\n"
        'const nom = await rl.question("Votre nom : ");\n'
        'const ok = await rl.question("Continuer ? (o/n) ");\n'
    )
    a = analyze_node(code)
    assert a["ok"] is True
    assert a["eco"] == "npm"
    assert len(a["inputs"]) == 2
    assert a["inputs"][1]["options"] == ["o", "n"]
    assert a["suggested_requirements"] == ["axios"]
    assert "fs" in a["imports"]["stdlib"]
    assert "./local.js" in a["imports"]["relative"]


def test_shell_scan():
    code = (
        'echo "1 - Demarrer"\n'
        'echo "2 - Quitter"\n'
        'read -p "Votre choix : " choix\n'
        'read -p "Mot de passe : " -s mdp\n'
    )
    a = analyze_shell(code)
    assert a["ok"] is True
    assert len(a["inputs"]) == 2
    assert a["inputs"][0]["options"] == ["1", "2"]
    assert a["inputs"][1]["sensitive"] is True


def test_shell_select():
    code = 'select c in alpha beta gamma; do echo "$c"; break; done\n'
    a = analyze_shell(code)
    assert len(a["inputs"]) == 1
    assert a["inputs"][0]["options"] == ["alpha", "beta", "gamma"]


def test_dispatch():
    assert analyze_file("a.py", "x = 1\n")["language"] == "python"
    assert analyze_file("a.js", "x = 1\n")["language"] == "node"
    assert analyze_file("a.sh", "echo x\n")["language"] == "shell"
    assert analyze_file("a.txt", "x")["language"] == "unknown"


def test_diagnose_python_module():
    log = ('Traceback (most recent call last):\n'
           '  File "/x/bot.py", line 5, in <module>\n'
           '    import requests\n'
           "ModuleNotFoundError: No module named 'requests'\n")
    d = diagnose_crash(log, 1)
    assert d["found"] is True
    assert d["exception"] == "ModuleNotFoundError"
    assert d["action"]["manager"] == "pip"
    assert d["action"]["package"] == "requests"
    assert d["location"] == {"file": "bot.py", "line": 5}


def test_diagnose_node_module():
    log = ("Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'axios'\n"
           "    at foo (file:///srv/app/main.js:3:15)\n")
    d = diagnose_crash(log, 1)
    assert d is not None and d["found"] is True
    assert d["runtime"] == "node"
    assert d["action"]["manager"] == "npm"
    assert d["action"]["package"] == "axios"


def test_diagnose_generic_exit():
    d = diagnose_crash("", 3)
    assert d["found"] is True
    assert "3" in d["exception"]
    assert diagnose_crash("", 0) is None
