# Free-LLM-Models — CLI + API REST

Projet Python multi-fournisseurs pour **découvrir** et **tester séquentiellement** les
modèles conversationnels visibles dans les API de Groq, Gemini, Cerebras,
SambaNova, Mistral, Cohere, OpenRouter et Ollama.

## Prérequis

- Python **3.10 ou version ultérieure** : le code utilise notamment les annotations `X | None` et les génériques natifs comme `list[str]` ;
- `pip` pour installer les dépendances de `requirements.txt` ;
- une clé API uniquement pour chaque fournisseur distant que vous souhaitez interroger. Ollama peut fonctionner sans clé lorsque son host est configuré sur une instance locale `http://localhost...`.

Vérifiez la version avant de créer l'environnement virtuel :

```text
python --version
```

Sous Windows, `py --version` peut être utilisé à la place. Python 3.9 ou antérieur échouera dès l'analyse de certains fichiers.

## Démarrage rapide (PowerShell / Windows)

```powershell
cd D:\My-App\Free-LLM-Models
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Modifier .env avec vos propres clés API (ne pas partager le fichier).
python cli.py providers
python cli.py discover
python test_openrouter.py
python cli.py top --metric intelligence
```

## Démarrage rapide (macOS / Linux)

```bash
git clone https://github.com/Jalal-Zerroudi/Free-LLM-Models.git
cd Free-LLM-Models
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Modifier .env avec vos propres clés API et ne jamais le commiter.
python cli.py providers
python cli.py discover
python test_openrouter.py
python cli.py top --metric intelligence
```

Pour lancer l'interface locale :

```bash
uvicorn api:app --host 127.0.0.1 --port 8000
```

Ouvrir ensuite http://127.0.0.1:8000/. La commande `deactivate` permet de quitter l'environnement virtuel.

## Tous les scripts

- `test_openrouter.py`, `test_ollama.py`, `test_groq.py`, `test_gemini.py`
- `test_cerebras.py`, `test_sambanova.py`, `test_mistral.py`, `test_cohere.py`
- `test_all_providers.py` : orchestration des huit fournisseurs
- `cli.py` : interface en ligne de commande (`providers`, `discover`, `test`, `top`)
- `api.py` : API REST FastAPI locale (SQLite, refresh, chat)
- `core.py` : connecteurs, filtres, tests, appel de chat et classement
- `runner.py` : orchestration et sauvegarde CSV
- `db.py` : stockage SQLite (modèles, historique des tests, refresh)
- `service.py` : import CSV, refresh séquentiel, classement, chat avec repli

## Règle de sécurité des coûts

**Une API qui liste un modèle ne prouve pas que ce modèle est gratuit.**
Par défaut, seuls les modèles OpenRouter dont le tarif de texte est explicitement
à zéro sont testés. Les modèles d'autres fournisseurs sont découverts et enregistrés
mais marqués `SKIPPED_UNVERIFIED` pour éviter des dépenses involontaires.

Pour **autoriser explicitement** les tests des modèles sans preuve de gratuité :

```powershell
python test_ollama.py --allow-unverified
python test_groq.py --allow-unverified
python test_all_providers.py --allow-unverified
```

> ATTENTION : `--allow-unverified` peut consommer des crédits et provoquer des frais
> sur des comptes facturés. Pour un compte Ollama Free, les appels aux modèles
> payants devraient être refusés, mais le projet ne peut pas vérifier le contrat
> de votre compte par le seul catalogue.

Aucun délai fixe (`sleep`) n'est ajouté entre les requêtes. Chaque appel est bloquant
jusqu'à la réponse complète ou une erreur. Le timeout réseau est 15 s pour la connexion,
et 180 s pour l'inactivité du serveur : **ce n'est pas** une durée maximale absolue.
HTTP 403 et HTTP 429 ne bloquent pas les tests suivants. Les quotas du fournisseur
restent applicables.

## CLI

```powershell
python cli.py providers
python cli.py discover --provider gemini
python cli.py test --provider openrouter
python cli.py test --provider ollama --allow-unverified
python cli.py test --allow-unverified
python cli.py top --metric speed
python cli.py top --metric intelligence --provider openrouter
```

## Page web

```powershell
uvicorn api:app --host 127.0.0.1 --port 8000
```

Ouvrir http://127.0.0.1:8000/ :

- **Modèles** : tous les modèles enregistrés, filtrables par fournisseur, statut, gratuité vérifiée et nom.
- **Fournisseurs** : statistiques, host, clé API **masquée** (ex. `gsk...oHcu`) et origine de la clé
  (`base` = saisie dans la page, `.env`, ou aucune). Boutons pour modifier la clé ou le host,
  effacer la clé saisie (retour à la valeur de `.env`) et tester le fournisseur (refresh avec progression).
- **Ajouter un fournisseur compatible OpenAI** (nom, host, clé) : il doit exposer `/models` et
  `/chat/completions`. Ses modèles sont toujours `unverified` : les tester exige de cocher
  « Accepter un coût possible ». Un fournisseur ajouté peut être supprimé (ses modèles aussi,
  l'historique des tests est conservé) ; les 8 fournisseurs intégrés ne peuvent pas l'être.

- **Chat** : choisir un modèle `PASS` (classé par rang) ou « Auto » (meilleur modèle, avec repli),
  puis discuter en plusieurs messages ; le modèle reçoit la conversation. Sous chaque réponse :
  modèle utilisé, latence, gratuité et essais ratés. La conversation reste dans le navigateur
  (bouton « Nouvelle conversation »). Les modèles non vérifiés exigent la case « Accepter un coût possible ».

Priorité des réglages : valeur saisie dans la page (SQLite) > `.env` > host par défaut.
La CLI (`cli.py`, `test_*.py`) utilise les mêmes réglages.

Routes associées : `GET/POST /api/v1/settings/providers`,
`PUT/DELETE /api/v1/settings/providers/{name}`. La clé complète n'est jamais renvoyée.

> SÉCURITÉ : les clés saisies dans la page sont stockées **en clair** dans `results/models.db`
> (ignoré par Git). La page n'a pas de mot de passe : lancez toujours le serveur avec
> `--host 127.0.0.1` et ne l'exposez jamais sur le réseau. Le serveur refuse les noms d'hôte
> autres que `127.0.0.1`/`localhost` et les écritures non JSON.

## API REST (SQLite)

```powershell
pip install -r requirements.txt
python cli.py db-init          # crée results/models.db et importe les CSV existants
uvicorn api:app --host 127.0.0.1 --port 8000
```

Documentation interactive : http://127.0.0.1:8000/docs

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/api/v1/health` | État du service |
| GET | `/api/v1/models` | Modèles stockés au statut `PASS` |
| GET | `/api/v1/models/all` | Tous les modèles découverts et leur statut |
| GET | `/api/v1/providers` | Fournisseurs et statistiques par statut |
| GET | `/api/v1/providers/{provider}/models?status=PASS` | Filtre par fournisseur / statut |
| POST | `/api/v1/refresh` | Redécouverte + tests séquentiels (tâche de fond) |
| GET | `/api/v1/refresh/status`, `/api/v1/refresh/{id}` | Progression du refresh |
| POST | `/api/v1/chat` | Question à un modèle `PASS` précis |
| POST | `/api/v1/chat/auto` | Meilleur modèle `PASS` classé, avec repli |
| GET | `/api/v1/top` | Modèle le mieux classé |

Règles :

- Les GET lisent **uniquement** SQLite ; seuls `refresh`, `chat` et `chat/auto` appellent les fournisseurs.
- `status` (disponibilité observée) et `free_status` (gratuité vérifiée) sont indépendants :
  **`PASS` ne veut pas dire gratuit**.
- Par défaut, refresh, chat et `top` se limitent aux modèles `verified_zero_price`.
  Ajouter `"allow_unverified": true` (ou `?allow_unverified=true` pour `top`) accepte un
  risque de facturation.
- Un refresh conserve les résultats précédents : modèles disparus gardés, erreurs de
  découverte n'effacent rien, modèles non testés gardent leur dernier statut. Chaque test
  est ajouté à la table `test_runs` (historique). Un seul refresh à la fois (HTTP 409 sinon).
- Un échec de chat est historisé mais ne retire pas le statut `PASS`.
- Erreurs fournisseur : 429 (quota), 504 (timeout), 403/402 (accès/paiement), 502 (autre).
- Classement configurable dans `.env` : `MODEL_RANKING=free,intelligence,latency`
  (clés possibles : `free`, `intelligence`, `coding`, `latency`), `CHAT_AUTO_MAX_ATTEMPTS`,
  `CHAT_MAX_TOKENS`, `CHAT_HISTORY_MAX`, `LLM_DB_PATH`.
- `/chat` et `/chat/auto` acceptent un champ optionnel `history` :
  `[{"role": "user"|"assistant", "content": "..."}]` (les `CHAT_HISTORY_MAX` derniers sont envoyés).

Exemples (PowerShell) :

```powershell
curl.exe http://127.0.0.1:8000/api/v1/models
curl.exe "http://127.0.0.1:8000/api/v1/providers/groq/models?status=PASS"
curl.exe -X POST http://127.0.0.1:8000/api/v1/refresh -H "Content-Type: application/json" -d '{\"provider\":\"openrouter\"}'
curl.exe http://127.0.0.1:8000/api/v1/refresh/status
curl.exe -X POST http://127.0.0.1:8000/api/v1/chat -H "Content-Type: application/json" -d '{\"provider\":\"openrouter\",\"model\":\"MODEL_ID\",\"prompt\":\"Bonjour\"}'
curl.exe -X POST http://127.0.0.1:8000/api/v1/chat/auto -H "Content-Type: application/json" -d '{\"prompt\":\"Bonjour\"}'
curl.exe http://127.0.0.1:8000/api/v1/top
```

## Interprétation du classement

- `PASS` signifie que le modèle a répondu « 2 » à une question élémentaire.
  Cela **ne constitue pas** une mesure fiable d'intelligence.
- `TOP speed` = vitesse observée sur **un seul échantillon**, pas moyenne/stabilité.
- `TOP intelligence` = score de benchmark **OpenRouter uniquement**, si disponible ;
  les autres fournisseurs ne sont pas classés par intelligence.
- `free_status=verified_zero_price` chez OpenRouter signifie tarifs texte affichés à 0
  au moment de la découverte. Cela n'assure pas des quotas illimités.
- `free_status=unverified` = gratuité inconnue, même si la requête a répondu.
- Les modèles hors chat (musique, embeddings, sécurité, voix, etc.) sont filtrés par
  heuristiques : ce filtrage peut devoir être ajusté si des noms évoluent.

## Tester sans réseau

```powershell
python -m unittest discover -s tests -v
```

`tests/test_api.py` et `tests/test_settings.py` utilisent une base SQLite temporaire et des
fournisseurs simulés.

## Données locales, sauvegarde et réinitialisation

Le projet conserve plusieurs catégories de données locales :

| Emplacement | Contenu | Sensibilité |
|---|---|---|
| `.env` et variantes locales | Clés API configurées par variables d’environnement | Secrets en clair |
| `results/models.db` | Catalogue, historique des tests, refresh et réglages saisis dans la page | Peut contenir des clés API en clair |
| `results/models.db-wal`, `results/models.db-shm` | Fichiers temporaires du mode WAL SQLite | Peuvent contenir des transactions récentes |
| `results/*.csv`, `results/*.json` | Résultats et catalogues produits par la CLI | Métadonnées sur les modèles et erreurs expurgées |

Les messages du chat ne sont pas enregistrés par le serveur : ils restent dans le navigateur. Les métadonnées des essais et les erreurs expurgées sont néanmoins ajoutées à SQLite.

### Sauvegarder SQLite

La commande suivante utilise l’API de sauvegarde cohérente de SQLite et fonctionne même si la base utilise le mode WAL :

```bash
python -c "import sqlite3; source=sqlite3.connect('results/models.db'); backup=sqlite3.connect('results/models-backup.db'); source.backup(backup); backup.close(); source.close()"
```

Le fichier de sauvegarde peut contenir les clés saisies dans la page. Il doit rester hors de Git, être protégé comme un secret et ne pas être envoyé dans un ticket ou une archive publique.

### Réinitialiser l’état local

Arrêtez d’abord l’API, puis supprimez la base et ses fichiers annexes.

macOS / Linux :

```bash
rm -f results/models.db results/models.db-wal results/models.db-shm
```

PowerShell :

```powershell
Remove-Item results/models.db, results/models.db-wal, results/models.db-shm -ErrorAction SilentlyContinue
```

Au prochain démarrage, le schéma est recréé. Si des catalogues JSON ou résultats CSV locaux existent encore, ils peuvent être réimportés automatiquement dans une base vide. Supprimez-les également uniquement si vous souhaitez repartir sans aucun résultat local.

## Sources des API (docs officielles)

- Groq: https://console.groq.com/docs/api-reference
- Gemini: https://ai.google.dev/api/models
- Cerebras: https://inference-docs.cerebras.ai/
- SambaNova: https://docs.sambanova.ai/
- Mistral: https://docs.mistral.ai/api/endpoint/models
- Cohere: https://docs.cohere.com/v2/reference/list-models
- OpenRouter: https://openrouter.ai/docs/api/api-reference/models/get-models
- Ollama: https://docs.ollama.com/cloud

## Fichiers privés

Ne commitez **jamais** `.env` dans Git. Ne placez pas de clés API dans les scripts,
les requêtes du navigateur ni un dépôt public.

