# Politique de sécurité

Free-LLM-Models manipule des clés API, des prompts et des réponses provenant de plusieurs fournisseurs. Cette politique précise le périmètre de sécurité du projet, les pratiques attendues et la manière de signaler un problème sans exposer de secret.

## Versions prises en charge

Les correctifs de sécurité ciblent l'état actuel de la branche `main`. Les copies plus anciennes du dépôt peuvent ne pas recevoir de correctif rétroactif ; mettez à jour votre installation avant de vérifier qu'un problème est toujours présent.

## Signaler une vulnérabilité

Ne publiez jamais une clé API, un fichier `.env`, une base `results/models.db`, un prompt confidentiel ou une réponse contenant des données privées dans une issue publique.

Pour un signalement :

1. utilisez le mécanisme de signalement privé de GitHub lorsqu'il est disponible pour ce dépôt ;
2. indiquez le commit concerné, le scénario de reproduction minimal, l'impact observé et les mesures temporaires possibles ;
3. remplacez les clés, domaines privés, identifiants de modèle et données personnelles par des valeurs factices ;
4. précisez si le problème nécessite un fournisseur distant, un host personnalisé ou l'option `--allow-unverified`.

Si un secret réel a déjà été exposé, révoquez-le ou faites-le tourner immédiatement auprès du fournisseur. Le supprimer de Git ne suffit pas : il peut rester dans l'historique, les caches ou les journaux distants.

## Données sensibles locales

Les emplacements suivants doivent être traités comme sensibles :

- `.env` et ses variantes locales, qui contiennent les clés configurées par variables d'environnement ;
- `results/models.db`, qui peut contenir en clair les clés saisies depuis l'interface ;
- `results/models.db-wal` et `results/models.db-shm`, qui peuvent contenir des transactions récentes ;
- les sauvegardes manuelles de SQLite ;
- les fichiers JSON et CSV de `results/`, qui peuvent révéler des métadonnées sur les modèles et les erreurs rencontrées.

Ces fichiers ne doivent pas être joints à une issue ni ajoutés au dépôt. Vérifiez également les archives, captures d'écran et journaux avant de les partager.

## Modèle de confiance

L'application est conçue pour un usage local par un utilisateur de confiance :

- l'API et l'interface n'ont pas d'authentification ;
- le serveur doit rester lié à `127.0.0.1` ;
- les clés saisies dans l'interface sont stockées localement en clair ;
- les fournisseurs et proxies distants reçoivent les requêtes qui leur sont destinées ;
- un host personnalisé est une frontière de confiance choisie par l'utilisateur.

La validation d'une URL vérifie sa forme, pas l'identité ni la fiabilité du serveur. Un host configuré peut recevoir la clé correspondante, les prompts, l'historique transmis et les réponses. Utilisez HTTPS hors boucle locale, une clé dédiée et révocable, et des prompts sans données confidentielles pendant les premiers essais.

## Mesures de réduction des risques

- Conservez `.env` et `results/models.db*` hors de Git.
- Utilisez des clés distinctes, limitées et facilement révocables pour les tests.
- Ne lancez pas Uvicorn avec `--host 0.0.0.0` sans couche d'authentification et protection réseau externes.
- Vérifiez précisément le domaine avant d'enregistrer un host personnalisé.
- Réservez HTTP aux services locaux maîtrisés ; utilisez HTTPS pour les destinations distantes.
- N'activez `--allow-unverified` ou `allow_unverified` qu'après avoir accepté le risque de consommation de crédits.
- Ne placez pas de données personnelles, de secrets ou de documents confidentiels dans les prompts de test.
- Maintenez Python et les dépendances de `requirements.txt` à jour.

## Comportements intentionnels

Les comportements suivants ne constituent pas à eux seuls une vulnérabilité :

- des appels réseau après une commande `discover`, `test`, `refresh`, `chat` ou `chat/auto` explicitement lancée ;
- la transmission d'une clé au host que l'utilisateur a lui-même configuré ;
- le stockage local en clair d'une clé saisie dans l'interface, tel que documenté dans le README ;
- un refus, un quota ou une facturation provenant d'un fournisseur après autorisation de modèles non vérifiés ;
- l'absence d'authentification lorsque le service reste limité à la boucle locale conformément à la configuration recommandée.

Un contournement des validations, une exposition involontaire de secrets, un appel à un host différent de celui configuré ou une opération réseau déclenchée par une route annoncée comme locale doit en revanche être signalé.

## Vérifications avant publication d'un correctif

Pour toute modification touchant les clés, les hosts, le chat, les erreurs ou le stockage :

```bash
python -m unittest discover -s tests -v
```

Ajoutez un test de non-régression hors ligne lorsque cela est possible. Les scripts `test_<provider>.py` sont des essais réseau réels : ne les exécutez qu'avec des comptes de test et des clés dédiées.
