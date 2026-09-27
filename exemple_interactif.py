"""
Exemple interactif a uploader dans zach-runner.
Le site detecte automatiquement : le menu (1, 2, 3), les questions,
et propose des boutons cliquables pour les choix.
"""
import time

print("=" * 34)
print("  SUPER-BOT — que voulez-vous faire ?")
print("=" * 34)
print("1 - Dire bonjour")
print("2 - Calculer un double")
print("3 - Tester une notif Discord")

choix = input("Votre choix (1-3) : ")

if choix == "1":
    prenom = input("Votre prenom : ")
    print(f"Bonjour {prenom}.")
elif choix == "2":
    n = input("Donnez un nombre : ")
    try:
        print(f"Le double de {n} = {int(n) * 2}")
    except ValueError:
        print("Valeur non numerique.")
elif choix == "3":
    url = input("URL du webhook Discord : ")
    print(f"URL enregistree : {url}")
    print("Envoi d'un message de test...")
    time.sleep(1)
    print("Message envoye avec succes.")
else:
    print(f"Choix « {choix} » inconnu, utilisez 1, 2 ou 3.")

print("Au revoir.")
