"""Orchestrateur CLI des tests séquentiels (aucun sleep fixe)."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from core import (OUTPUT, PROVIDERS, Model, ProviderError, Result, config,
                  discover, eligible_for_test, read_csv, test_model, top_results,
                  write_csv)


def save_catalog(provider: str, models: list[Model]) -> None:
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / f'models_{provider}.json').write_text(
        json.dumps([asdict(m) for m in models], ensure_ascii=False, indent=2),
        encoding='utf-8')


def run_provider(provider: str, allow_unverified: bool = False,
                 discover_only: bool = False) -> list[Result]:
    host, key = config(provider)
    if not key and not (provider == 'ollama' and not host.startswith('https://ollama.com')):
        print(f'[{provider}] SKIPPED: clé API absente', flush=True)
        return []

    print(f'\n[{provider.upper()}] Découverte des modèles...', flush=True)
    try:
        models = discover(provider)
    except (ProviderError, OSError, ValueError) as exc:
        print(f'[{provider}] Erreur découverte : {exc}', flush=True)
        return []
    except Exception as exc:
        # requests exceptions for auth/network errors
        print(f'[{provider}] Erreur réseau/découverte : {exc}', flush=True)
        return []

    save_catalog(provider, models)
    print(f'[{provider}] Modèles de chat découverts : {len(models)}', flush=True)
    print(f'[{provider}] La présence dans le catalogue ne prouve pas la gratuité.', flush=True)
    if discover_only:
        for m in models:
            print(f'  - {m.model_id} [{m.free_status}]')
        return []

    output_file = OUTPUT / f'results_{provider}.csv'
    results: list[Result] = []
    for index, model in enumerate(models, 1):
        print(f'[{provider} {index}/{len(models)}] {model.model_id}', flush=True)
        if not eligible_for_test(model, allow_unverified):
            result = Result(provider=provider, model=model.model_id,
                            free_status=model.free_status,
                            status='SKIPPED_UNVERIFIED',
                            intelligence_score=model.intelligence_score,
                            coding_score=model.coding_score,
                            error='Gratuité non vérifiable via API. Utiliser --allow-unverified si autorisé.')
        else:
            print('Waiting for LLM response...', flush=True)
            result = test_model(model)  # bloquant jusqu'à réponse/erreur/timeout
        results.append(result)
        write_csv(output_file, results)
        print(f"  Status: {result.status} | Answer: {result.answer[:120] or '-'}"
              f" | Time: {result.elapsed_seconds:.2f}s", flush=True)
        if result.error:
            print(f'  Error: {result.error}', flush=True)

    stats = Counter(r.status for r in results)
    print(f'\n{provider.upper()}: total={len(results)}, '
          + ', '.join(f'{k}={v}' for k, v in sorted(stats.items())), flush=True)
    passed = [r for r in results if r.status == 'PASS']
    if passed:
        fastest = min(passed, key=lambda r: r.elapsed_seconds)
        print(f'  TOP 1 vitesse observée (1 mesure): {fastest.model} '
              f'({fastest.elapsed_seconds:.3f}s)', flush=True)
        ranked = [r for r in passed if r.intelligence_score is not None]
        if ranked:
            best = max(ranked, key=lambda r: r.intelligence_score)
            print(f'  TOP 1 intelligence (benchmarks OpenRouter): '
                  f'{best.model} ({best.intelligence_score})', flush=True)
    print(f'  CSV: {output_file}', flush=True)
    return results


def run_many(providers: list[str], allow_unverified: bool = False,
             discover_only: bool = False) -> list[Result]:
    all_results = []
    for provider in providers:
        all_results.extend(run_provider(provider, allow_unverified, discover_only))
        if not discover_only:
            write_csv(OUTPUT / 'results_all.csv', all_results)
    if not discover_only:
        print(f'\nTOTAL: {len(all_results)} entrées enregistrées '
              f'dans {OUTPUT / "results_all.csv"}')
    return all_results


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Discovery & test séquentiel de modèles LLM')
    p.add_argument('--provider', choices=PROVIDERS, help='Un fournisseur seulement')
    p.add_argument('--list', action='store_true', help='Découvrir sans appels de génération')
    p.add_argument('--allow-unverified', action='store_true',
                   help='Autoriser le test de modèles sans garantie de gratuité (facturation possible)')
    return p


def main(forced_provider: str | None = None) -> None:
    args = parser().parse_args()
    if forced_provider is not None and args.provider and args.provider != forced_provider:
        parser().error(f'Ce fichier teste uniquement {forced_provider}')
    names = [forced_provider or args.provider] if (forced_provider or args.provider) else list(PROVIDERS)
    if args.allow_unverified:
        print('AVERTISSEMENT : les modèles non vérifiés peuvent consommer des crédits PAYANTS.')
    else:
        print('Mode sûr : tests des modèles à prix 0 explicitement vérifié seulement (OpenRouter).')
        print('Pour tenter également les autres : --allow-unverified (risque de coût).')
    run_many(names, allow_unverified=args.allow_unverified, discover_only=args.list)


if __name__ == '__main__':
    main()
