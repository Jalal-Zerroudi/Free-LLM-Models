"""CLI unifiée : providers, discover, test, top."""
from __future__ import annotations

import argparse
from core import config, provider_names, read_csv, top_results, OUTPUT
from runner import run_many


def main() -> None:
    providers = provider_names()
    parser = argparse.ArgumentParser(description='Free LLM Models CLI')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('providers', help='Voir les fournisseurs configurés')
    discover = sub.add_parser('discover', help='Découvrir les modèles sans génération')
    discover.add_argument('--provider', choices=providers)
    test = sub.add_parser('test', help='Tester séquentiellement les modèles')
    test.add_argument('--provider', choices=providers)
    test.add_argument('--allow-unverified', action='store_true',
                      help='Tester les modèles dont la gratuité est inconnue (coût possible)')
    top = sub.add_parser('top', help='Classer les tests PASS enregistrés')
    top.add_argument('--metric', choices=('speed', 'intelligence'), default='speed')
    top.add_argument('--provider', choices=providers)
    top.add_argument('--limit', type=int, default=10)
    sub.add_parser('db-init', help='Créer la base SQLite et importer les CSV existants')
    args = parser.parse_args()

    if args.command == 'db-init':
        import db
        import service
        imported = service.import_csv_if_empty()
        if imported:
            print(f'{imported} modèles importés dans {db.db_path()}')
        else:
            print(f'Base déjà initialisée : {db.db_path()} (aucun import)')
    elif args.command == 'providers':
        for p in providers:
            host, key = config(p)
            print(f'{p:11} configured={bool(key)} host={host}')
    elif args.command in ('discover', 'test'):
        names = [args.provider] if args.provider else list(providers)
        run_many(names, allow_unverified=getattr(args, 'allow_unverified', False),
                 discover_only=args.command == 'discover')
    elif args.command == 'top':
        path = OUTPUT / ('results_' + args.provider + '.csv'
                         if args.provider else 'results_all.csv')
        rows = top_results(read_csv(path), args.metric)
        if not rows:
            print('Pas de résultat PASS correspondant. Lancer cli.py test.')
            return
        for i, r in enumerate(rows[:max(0, args.limit)], 1):
            field = 'elapsed_seconds' if args.metric == 'speed' else 'intelligence_score'
            print(f"{i}. [{r['provider']}] {r['model']} : {r[field]}")
        if args.metric == 'intelligence':
            print('Attention : seuls les modèles associés à un benchmark OpenRouter sont classés.')
        else:
            print('Attention : vitesse observée sur une seule requête par modèle.')


if __name__ == '__main__':
    main()
