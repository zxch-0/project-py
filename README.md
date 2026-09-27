# zach-runner — Executez vos scripts et projets depuis un site prive (Render)

Console web **privee** pour executer des scripts et projets complets sur un serveur :

- **Upload de fichiers** (`.py`, `.js`, `.mjs`, `.cjs`, `.sh`) ou de **projets complets**
  (`.zip`, `.tar.gz`, `.rar`, `.7z`, multi-fichiers avec dependances)
- **Analyse intelligente** : questions detectees, menus a choix, librairies manquantes, risques
- **Reponses guidees** : formulaire avant lancement (boutons de choix cliquables),
  puis console interactive pour repondre en direct
- **Execution persistante** : le run continue meme si vous fermez la fenetre
- **Console structuree** : horodatage, niveaux, recherche, filtres, telechargement
- **Diagnostic de crash** : cause en francais + action corrective (installation pip/npm)
- **Runtimes** : Python, Node.js, Shell, ou commande personnalisee (php, ruby, deno...)
- **Webhook entrant** journalise (date, IP, contenu) + declenchement auto d'un script

## Deploiement sur Render (Blueprint)

1. Poussez ce dossier sur GitHub (depot prive recommande).
2. Sur [dashboard.render.com](https://dashboard.render.com) : **New → Blueprint**, choisissez le depot.
3. Render detecte `render.yaml` : cliquez **Apply**.
4. Attendez 2 a 5 minutes (le build installe aussi Node.js), ouvrez l'URL fournie.

Le plan `starter` est volontaire : **disque persistant** (fichiers et logs conserves)
et **pas de mise en veille** (le plan gratuit interromprait vos scripts apres environ 15 minutes
d'inactivite).

### Variable optionnelle

| Variable | Effet |
|---|---|

## Utilisation

### Lancer un fichier ou un projet

1. **Nouveau script** : onglet **Fichier** (un script seul), **Archive projet** (`.zip`, `.tar.gz`,
   `.rar`, `.7z`) ou **Creer** (depuis un modele Python, Node.js ou Shell).
2. Le site **analyse** le code : questions (`input`, `read -p`, `question`), menus
   (`1 - Demarrer / 2 - Quitter`, `[o/n]`, `(1-3)`...), dependances, avertissements.
3. Pour un projet : choisissez le **point d'entree** parmi les candidats detectes, verifiez le
   **runtime** (auto-detecte, modifiable), installez les dependances (`requirements.txt` via pip,
   `package.json` via npm).
4. **Remplissez les reponses** : elles sont envoyees automatiquement au fil des questions.
   S'il reste une question, la console affiche une zone de reponse (avec boutons de choix).
5. Au **redemarrage** (manuel ou via webhook), vos dernieres reponses sont **rejouees**.

### Console et diagnostic

- Chaque ligne est **horodatee** et coloree selon son niveau (info, succes, alerte, erreur).
- **Recherche**, **filtre par niveau**, pause, suivi du direct, telechargement des logs.
- En cas d'echec : banniere de **diagnostic** (exception, fichier, ligne, explication)
  avec action corrective (ex : installer le paquet manquant puis relancer).
- La **syntaxe est verifiee avant chaque lancement** (Python, Node.js, Shell).

### Webhook

1. Onglet **Webhooks** : copiez votre **URL privee** (`/webhook/<token>`).
2. Chaque appel est journalise : **date/heure, IP, methode, query, headers, corps JSON/texte**,
   avec recherche, filtre par methode et graphique 24h.
3. Optionnel : **declencher un script a chaque appel**. Les donnees arrivent via la variable
   `WEBHOOK_PAYLOAD` et vos reponses memorisees sont rejouees :

```python
import os, json
data = json.loads(os.environ.get("WEBHOOK_PAYLOAD", "{}"))
print("Signal recu :", data)
```

Test rapide :

```bash
curl -X POST "https://VOTRE-APP.onrender.com/webhook/VOTRE-TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"signal":"BUY","prix":123.4}'
```

## Lancer en local

```bash
pip install -r requirements.txt
python app.py
# → http://localhost:5000
```

Lancer les tests :

```bash
python -m pytest tests/ -q
```

## Structure

```
├── app.py                 # serveur Flask (API, webhooks)
├── analyzer.py            # analyse statique (Python/Node/Shell) + diagnostic
├── runner.py              # execution interactive (stdin, logs structures)
├── runtimes.py            # detection et resolution des runtimes
├── projects.py            # extraction et analyse d'archives
├── render_build.sh        # build Render (pip + Node.js vendored)
├── render.yaml            # Blueprint Render (service + disque persistant)
├── requirements.txt
├── templates/             # dashboard
├── static/                # style.css, app.js
├── tests/                 # suite de tests (pytest)
├── exemple_interactif.py  # demo : menus + questions (a uploader pour tester)
├── exemple_bot.py         # modele de bot webhook en continu
└── data/                  # stockage persistant (disque Render, ignore par Git)
```

## Notes

- Apres un **redeploiement Render**, relancez vos scripts (ou declenchez-les via webhook).
- Un seul worker gunicorn : voulu, pour un pilotage fiable des processus.
- Formats d'archive : `.zip`, `.tar.gz` et `.7z` (pur Python) toujours supportes ;
  `.rar` fonctionne des qu'un outil `unrar`/`unar`/`bsdtar` est present — le build
  Render l'installe automatiquement (vendored, sans compilation).
- Gardez votre **URL webhook** et l'URL du site pour vous (acces direct, sans compte).
