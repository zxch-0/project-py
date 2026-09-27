"""
Exemple de script a uploader dans zach-runner.
- Tourne en boucle (persitant meme si vous fermez le navigateur)
- Lit le payload du webhook via WEBHOOK_PAYLOAD si declenche par un appel
"""
import json
import os
import time
from datetime import datetime

print("Bot demarre.", flush=True)

payload_raw = os.environ.get("WEBHOOK_PAYLOAD")
if payload_raw:
    print(f"Declenche par webhook (event {os.environ.get('WEBHOOK_EVENT_ID')})", flush=True)
    try:
        data = json.loads(payload_raw)
        print(f"Donnees recues : {data}", flush=True)
        # Votre logique ici :
        # if data.get("signal") == "BUY": ...
    except json.JSONDecodeError:
        print(f"Payload brut : {payload_raw[:500]}", flush=True)

i = 0
while True:
    i += 1
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Tick {i} — toujours en vie.", flush=True)
    time.sleep(30)
