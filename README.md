# PyRunner 🐍 — Exécute tes scripts Python depuis un site privé (Render)

Console web **privée** qui permet de :

- ⬆️ **Uploader n'importe quel script `.py`** (ou le créer depuis un modèle) et l'exécuter sur le serveur
- 🧠 **Scanner intelligemment le script** : questions `input()`, menus `1, 2, 3`, librairies manquantes, risques
- 💬 **Remplir les réponses petit à petit** avant le lancement (boutons de choix cliquables), ou **répondre en direct** dans la console interactive
- ▶️ Laisser tourner **même si tu fermes la fenêtre** (exécution détachée + logs persistants)
- 📟 **Console parfaite** : horodatage, couleurs par niveau, recherche, filtres, téléchargement
- 🩺 **Diagnostic auto des crashs** : cause en français + bouton « Installer » si librairie manquante
- 🔐 Accès protégé par un **code secret défini à la première visite**
- 🔔 **Webhook inbound** journalisé (date/heure, IP, contenu…) + déclenchement auto d'un script

## 🚀 Déploiement sur Render (Blueprint)

1. **Push ce dossier sur GitHub** (repo privé conseillé).
2. Sur [dashboard.render.com](https://dashboard.render.com) → **New → Blueprint** → choisis ton repo.
3. Render détecte `render.yaml` → **Apply**.
4. Attends ~2-3 min → ouvre l'URL → 🎉 **définis ton code secret** (1re visite).

> 💡 Le plan `starter` est volontaire : **disque persistant** + **pas de mise en veille** (le plan
> gratuit couperait tes scripts après ~15 min d'inactivité).

### Variable optionnelle

| Variable | Effet |
|---|---|
| `SECRET_CODE` | Force le code d'accès (sinon défini à la 1re visite). |

## 🖥️ Utilisation

### Lancer un script interactif

1. **＋ Nouveau script** → upload ton `.py` (ex : `exemple_interactif.py` pour tester).
2. Le site le **scanne** 🧠 et affiche :
   - les **questions** détectées (`"URL webhook : "`, `"Ton choix (1-3) : "`…),
   - les **menus** avec boutons cliquables (`1`, `2`, `3`),
   - les **librairies** à installer, les avertissements éventuels.
3. Tu **remplis les réponses** → **Démarrer** : elles sont envoyées automatiquement au fil des questions.
4. S'il reste des questions (ou pour les scripts imprévisibles), la **console interactive** affiche
   `💬 Le script attend ta réponse` : tu réponds en direct, même avec des boutons de choix.
5. Au **redémarrage** (manuel ou via webhook), tes dernières réponses sont **rejouées automatiquement**.

### Console & diagnostic

- Chaque ligne est **horodatée**, colorée selon son niveau (info / succès / alerte / erreur).
- **Recherche**, **filtre par niveau**, pause, suivi du direct, téléchargement.
- En cas de crash : bannière **🩺 Diagnostic** (exception, fichier, ligne, explication FR) + action
  corrective (ex : `📦 Installer requests puis relancer`).
- La **syntaxe est vérifiée avant chaque lancement** : zéro démarrage voué à l'échec.

### Webhook

1. Onglet **🔔 Webhooks** → copie ton **URL privée**.
2. Chaque appel est journalisé : **🕒 date/heure, 🌐 IP, méthode, query, headers, corps JSON/texte**,
   avec recherche, filtre par méthode et graphique 24h.
3. (Optionnel) **Déclencher un script à chaque appel** : les données arrivent via `WEBHOOK_PAYLOAD`
   et tes réponses mémorisées sont rejouées.

```python
import os, json
data = json.loads(os.environ.get("WEBHOOK_PAYLOAD", "{}"))
print("Signal reçu :", data)
```

## 💻 Lancer en local

```bash
pip install -r requirements.txt
python app.py
# → http://localhost:5000
```

## 🗂️ Structure

```
├── app.py                 # serveur Flask (auth, API, webhooks)
├── analyzer.py            # scan statique (questions, menus, imports) + diagnostic crash
├── runner.py              # exécution interactive (stdin, détection d'attente, logs structurés)
├── templates/             # setup, login, dashboard (SPA)
├── static/                # style.css, app.js
├── render.yaml            # Blueprint Render (web service + disque persistant)
├── requirements.txt
├── exemple_interactif.py  # démo : menus + questions (à uploader pour tester)
├── exemple_bot.py         # modèle de bot webhook en continu
└── data/                  # stockage persistant (disque Render, ignoré par Git)
```

## ⚠️ Notes

- Après un **redéploiement Render**, relance tes scripts (ou déclenche-les via webhook).
- Un seul worker gunicorn : voulu, pour garder le pilotage des process fiable.
- Garde ton **URL webhook** et ton **code secret** pour toi.
