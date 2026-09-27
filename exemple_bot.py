"""
Exemple de script à uploader dans PyRunner.
- Tourne en boucle (persitant même si tu fermes le navigateur)
- Lit le payload du webhook via WEBHOOK_PAYLOAD si déclenché par un appel
Usage : upload ce fichier .py dans l'onglet "Mes scripts", puis Démarrer.
"""
import json
import os
import time
from datetime import datetime

print("🤖 Bot démarré !", flush=True)

# Si le script a été lancé par un appel webhook, le contenu est ici :
payload_raw = os.environ.get("WEBHOOK_PAYLOAD")
if payload_raw:
    print(f"🔔 Déclenché par webhook (event {os.environ.get('WEBHOOK_EVENT_ID')})", flush=True)
    try:
        data = json.loads(payload_raw)
        print(f"📦 Données reçues : {data}", flush=True)
        # 👉 Mets ici ta logique : ordre de trading, notif Discord, etc.
        # if data.get("signal") == "BUY": ...
    except json.JSONDecodeError:
        print(f"📦 Payload brut : {payload_raw[:500]}", flush=True)

# Boucle principale (exemple : heartbeat toutes les 30s)
i = 0
while True:
    i += 1
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 💓 toujours en vie (tick {i})", flush=True)
    time.sleep(30)
