"""
Exemple de script a uploader dans zach-runner.
- Tourne en boucle (persiste meme si vous fermez le navigateur)
- Le site vous previent sur Discord au demarrage, a la fin et en cas d'echec
  (configurez votre webhook Discord dans l'onglet Discord).
"""
import time
from datetime import datetime

print("Bot demarre.", flush=True)

i = 0
while True:
    i += 1
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Tick {i} — toujours en vie.", flush=True)
    # Votre logique ici (bot Discord, surveillance, trading...)
    time.sleep(30)
