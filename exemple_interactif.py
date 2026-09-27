"""
Exemple interactif à uploader dans PyRunner.
Le site détecte automatiquement : le menu (1, 2, 3), les questions,
et propose des boutons cliquables pour les choix. ✨
"""
import time

print("=" * 34)
print("  🤖 SUPER-BOT — que veux-tu faire ?")
print("=" * 34)
print("1 - Dire bonjour")
print("2 - Calculer un double")
print("3 - Configurer le webhook")

choix = input("Ton choix (1-3) : ")

if choix == "1":
    prenom = input("Ton prénom : ")
    print(f"Salut {prenom} ! 👋 Ravi de te voir.")
elif choix == "2":
    n = input("Donne-moi un nombre : ")
    try:
        print(f"Le double de {n} = {int(n) * 2} 🎯")
    except ValueError:
        print("⚠️ Ça ne ressemble pas à un nombre !")
elif choix == "3":
    url = input("URL webhook : ")
    print(f"✅ Webhook configuré : {url}")
    print("Envoi d'un signal de test...")
    time.sleep(1)
    print("📡 Signal envoyé avec succès !")
else:
    print(f"⚠️ Choix « {choix} » inconnu, utilise 1, 2 ou 3.")

print("À bientôt ! 👋")
