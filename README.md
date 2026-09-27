# PyRunner 🐍 — Exécute tes scripts Python depuis un site privé (Render)

Console web **privée** qui permet de :

- ⬆️ **Uploader n'importe quel script `.py` depuis ton ordinateur** et l'exécuter sur le serveur
- ▶️ Le laisser **tourner même si tu fermes la fenêtre** (exécution détachée côté serveur + logs persistants)
- 🔐 Protéger l'accès par un **code secret que TU définis à la première visite** (le premier arrivé verrouille le site)
- 🔔 Recevoir et **journaliser les appels webhook** (date/heure, IP, méthode, contenu…) + déclencher un script à chaque appel

## 🚀 Déploiement sur Render (Blueprint)

1. **Push ce dossier sur GitHub** (repo privé conseillé).
2. Sur [dashboard.render.com](https://dashboard.render.com) → **New → Blueprint** → choisis ton repo.
3. Render détecte `render.yaml` → clique **Apply**.
4. Attends ~2-3 min → ouvre l'URL fournie (`https://pyrunner-xxxx.onrender.com`).
5. 🎉 **Première visite : définis ton code secret.** Le site est alors verrouillé, toi seul y accèdes.

> 💡 Le plan `starter` dans `render.yaml` est volontaire :
> - **Disque persistant** (scripts, logs, code secret conservés après redéploiement)
> - **Pas de mise en veille** (le plan gratuit coupe tes scripts après ~15 min d'inactivité)
>
> Pour tester en gratuit : passe `plan: free` et **retire le bloc `disk:`** (mais tout sera effacé à chaque redéploiement et les scripts s'arrêteront en veille).

### Variable optionnelle

| Variable | Effet |
|---|---|
| `SECRET_CODE` | Force le code d'accès (au lieu de la config à la 1re visite). À définir dans Render → Environment. |

## 🖥️ Utilisation

### Exécuter un script

1. Onglet **📜 Mes scripts** → choisis ton `.py` (ou glisse-dépose-le sur la page).
2. (Optionnel) arguments, variables d'environnement `CLE=valeur`, `requirements.txt`.
3. Coche **« Démarrer immédiatement »** → **Uploader & exécuter 🚀**.
4. Ferme la fenêtre : **ça continue de tourner** ✔. Reviens pour voir les **logs en direct**, **stop / restart**, modifier le code.

### Webhook

1. Onglet **🔔 Webhook** → copie ton **URL privée** (`https://…/webhook/<token>`).
2. Colle-la dans ton service externe (TradingView, bot, etc.).
3. Chaque appel apparaît dans l'historique : **🕒 date/heure, 🌐 IP, méthode, query, headers, corps JSON/texte**.
4. (Optionnel) **Déclencher un script à chaque appel** : choisis le script + active le déclenchement auto. Le contenu de l'appel est injecté via `WEBHOOK_PAYLOAD` :

```python
import os, json
data = json.loads(os.environ.get("WEBHOOK_PAYLOAD", "{}"))
print("Signal reçu :", data)
```

Voir `exemple_bot.py` pour un modèle complet.

### Tester le webhook

```bash
curl -X POST "https://TON-APP.onrender.com/webhook/TON-TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"signal":"BUY","prix":123.4}'
```

## 💻 Lancer en local (optionnel)

```bash
pip install -r requirements.txt
python app.py
# → http://localhost:5000
```

## 🗂️ Structure

```
├── app.py              # serveur Flask (auth, scripts, webhook)
├── templates/          # setup, login, dashboard
├── static/             # style.css, app.js
├── render.yaml         # Blueprint Render (web service + disque)
├── requirements.txt
├── exemple_bot.py      # modèle de script à uploader
└── data/               # stockage persistant (disque Render, ignoré par Git)
    ├── auth.json       # hash du code secret
    ├── scripts/        # un dossier par script : main.py, output.log, requirements.txt
    ├── webhook.json    # token + config
    └── webhook_logs.jsonl
```

## ⚠️ Notes

- Si Render **redémarre** le serveur, les scripts ne redémarrent pas seuls : pense à les relancer (ou utilise le webhook en déclenchement auto pour le faire à distance).
- Un seul worker gunicorn : voulu, pour garder la gestion des process simple et fiable.
- Garde ton **URL webhook** et ton **code secret** pour toi : ce sont tes deux clés d'accès.
