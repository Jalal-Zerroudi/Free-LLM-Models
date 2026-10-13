"""Logique métier de l'API : import CSV, refresh séquentiel, classement et chat.

Réutilise les connecteurs de `core` ; seule cette couche appelle les fournisseurs.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import core
import db

FREE = 'verified_zero_price'
RANK_KEYS: dict[str, Callable[[dict], tuple]] = {
    'free': lambda m: (m.get('free_status') != FREE,),
    'intelligence': lambda m: (m.get('intelligence_score') is None, -(m.get('intelligence_score') or 0)),
    'coding': lambda m: (m.get('coding_score') is None, -(m.get('coding_score') or 0)),
    'latency': lambda m: (m.get('latency_seconds') is None, m.get('latency_seconds') or 0),
}
DEFAULT_RANKING = ('free', 'intelligence', 'latency')
HTTP_FOR_STATUS = {'RATE_LIMIT': 429, 'TIMEOUT': 504, 'ACCESS_DENIED': 403,
                   'PAYMENT_REQUIRED': 402}

_refresh_lock = threading.Lock()
_refresh_thread: threading.Thread | None = None


class ServiceError(Exception):
    def __init__(self, http_status: int, detail: Any):
        super().__init__(str(detail))
        self.http_status = http_status
        self.detail = detail


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, default)))
    except ValueError:
        return default


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, '', 'None') else None
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _float(value)
    return int(number) if number is not None else None


def is_configured(provider: str) -> bool:
    host, key = core.config(provider)
    return bool(key) or (provider == 'ollama' and not host.startswith('https://ollama.com'))


# ------------------------------------------------------- réglages fournisseurs

NAME_PATTERN = re.compile(r'^[a-z0-9][a-z0-9_-]{1,31}$')


def provider_view(name: str) -> dict:
    """Vue sans secret : seule une version masquée de la clé est exposée."""
    host, key = core.config(name)
    return {'name': name, 'kind': 'builtin' if name in core.PROVIDERS else core.CUSTOM_KIND,
            'host': host, 'configured': is_configured(name), 'key_set': bool(key),
            'key_masked': core.mask(key), 'key_source': core.key_source(name)}


def _clean_host(host: str) -> str:
    host = host.strip().rstrip('/')
    if not re.match(r'^https?://[^\s/]+', host):
        raise ServiceError(422, 'Host must start with http:// or https://')
    return host


def add_provider(name: str, host: str, api_key: str) -> dict:
    name = name.strip().lower()
    if not NAME_PATTERN.fullmatch(name):
        raise ServiceError(422, 'Name must match ^[a-z0-9][a-z0-9_-]{1,31}$')
    if name in core.provider_names():
        raise ServiceError(409, f'Provider already exists: {name}')
    db.upsert_provider_setting(name, core.CUSTOM_KIND, _clean_host(host), api_key.strip() or None)
    return provider_view(name)


def update_provider(name: str, host: str | None, api_key: str | None) -> dict:
    """None = inchangé ; chaîne vide = effacer la valeur en base (retour à .env)."""
    if name not in core.provider_names():
        raise ServiceError(404, f'Unknown provider: {name}')
    custom = name not in core.PROVIDERS
    changes: dict[str, Any] = {}
    if host is not None:
        if not host.strip() and custom:
            raise ServiceError(422, 'A custom provider needs a host')
        changes['host'] = _clean_host(host) if host.strip() else None
    if api_key is not None:
        changes['api_key'] = api_key.strip() or None
    db.upsert_provider_setting(name, core.CUSTOM_KIND if custom else 'builtin', **changes)
    return provider_view(name)


def delete_provider(name: str) -> None:
    if name in core.PROVIDERS:
        raise ServiceError(400, 'Built-in providers cannot be deleted; clear the key instead')
    if name not in core.provider_names():
        raise ServiceError(404, f'Unknown provider: {name}')
    db.delete_provider_setting(name, with_models=True)


# ---------------------------------------------------------------- import CSV

def import_csv_if_empty(results_dir: Path = core.OUTPUT) -> int:
    """Charge les catalogues JSON et CSV existants si la base est vide. Retourne le nombre de modèles."""
    db.init_db()
    if not db.is_empty():
        return 0
    for provider in core.PROVIDERS:
        catalog = results_dir / f'models_{provider}.json'
        if catalog.exists():
            seen = _mtime(catalog)
            try:
                items = json.loads(catalog.read_text(encoding='utf-8'))
            except ValueError:
                items = []
            for item in items:
                if isinstance(item, dict) and item.get('model_id'):
                    db.upsert_model(provider, item['model_id'], item.get('free_status') or 'unverified',
                                    _float(item.get('intelligence_score')),
                                    _float(item.get('coding_score')), seen)

        results = results_dir / f'results_{provider}.csv'
        if not results.exists():
            continue
        tested_at = _mtime(results)
        for row in core.read_csv(results):
            model_id = row.get('model')
            if not model_id:
                continue
            db.upsert_model(provider, model_id, row.get('free_status') or 'unverified',
                            _float(row.get('intelligence_score')), _float(row.get('coding_score')),
                            tested_at)
            if row.get('status') == 'SKIPPED_UNVERIFIED':
                continue  # jamais testé : pas de statut à enregistrer
            db.save_result(provider, model_id, row.get('status') or 'ERROR', _int(row.get('http_code')),
                           _float(row.get('elapsed_seconds')), core.redact(row.get('error') or ''),
                           tested_at, 'csv_import')
    return len(db.list_models())


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec='seconds')


# ------------------------------------------------------------------ refresh

def start_refresh(provider: str | None, allow_unverified: bool) -> int:
    global _refresh_thread
    if not _refresh_lock.acquire(blocking=False):
        running = db.latest_refresh()
        raise ServiceError(409, {'message': 'A refresh is already running',
                                 'refresh_id': running['id'] if running else None})
    try:
        refresh_id = db.create_refresh(provider, allow_unverified)
        _refresh_thread = threading.Thread(target=_run_refresh, name=f'refresh-{refresh_id}',
                                           args=(refresh_id, provider, allow_unverified), daemon=True)
        _refresh_thread.start()
    except BaseException:
        _refresh_lock.release()
        raise
    return refresh_id


def wait_for_refresh(timeout: float | None = None) -> None:
    if _refresh_thread is not None:
        _refresh_thread.join(timeout)


def _run_refresh(refresh_id: int, provider: str | None, allow_unverified: bool) -> None:
    errors: list[dict] = []
    try:
        to_test: list[core.Model] = []
        for name in [provider] if provider else list(core.provider_names()):
            if not is_configured(name):
                errors.append({'provider': name, 'error': 'Not configured (missing API key)'})
                continue
            db.update_refresh(refresh_id, current_model=f'{name}: discovery')
            try:
                models = core.discover(name)
            except Exception as exc:  # erreurs réseau/HTTP : conserver les données existantes
                errors.append({'provider': name, 'error': core.redact(str(exc))[:500]})
                continue
            seen_at = db.now()
            for m in models:
                db.upsert_model(name, m.model_id, m.free_status, m.intelligence_score,
                                m.coding_score, seen_at)
            to_test.extend(m for m in models if core.eligible_for_test(m, allow_unverified))
            db.update_refresh(refresh_id, total=len(to_test), errors=errors)

        for index, model in enumerate(to_test):
            db.update_refresh(refresh_id, current_model=f'{model.provider}/{model.model_id}')
            result = core.test_model(model)  # bloquant jusqu'à réponse, erreur ou timeout
            db.save_result(model.provider, model.model_id, result.status, result.http_code,
                           result.elapsed_seconds, core.redact(result.error), db.now(),
                           'refresh', refresh_id)
            db.update_refresh(refresh_id, completed=index + 1)
        db.update_refresh(refresh_id, state='done', current_model=None,
                          finished_at=db.now(), errors=errors)
    except Exception as exc:
        errors.append({'provider': provider, 'error': core.redact(str(exc))[:500]})
        db.update_refresh(refresh_id, state='failed', current_model=None,
                          finished_at=db.now(), errors=errors)
    finally:
        _refresh_lock.release()


# ------------------------------------------------------------------ ranking

def ranking_order() -> list[str]:
    raw = os.getenv('MODEL_RANKING', ','.join(DEFAULT_RANKING))
    order = [k.strip().lower() for k in raw.split(',') if k.strip().lower() in RANK_KEYS]
    return order or list(DEFAULT_RANKING)


def rank(models: list[dict]) -> list[dict]:
    order = ranking_order()
    return sorted(models, key=lambda m: tuple(v for k in order for v in RANK_KEYS[k](m)))


def candidates(allow_unverified: bool, provider: str | None = None) -> list[dict]:
    rows = db.list_models(provider=provider, status='PASS')
    if not allow_unverified:
        rows = [m for m in rows if m['free_status'] == FREE]
    return rank(rows)


# --------------------------------------------------------------------- chat

def _clean_history(history: list[dict] | None) -> list[dict]:
    """Garde les messages user/assistant non vides, limités aux CHAT_HISTORY_MAX derniers."""
    cleaned = [{'role': m['role'], 'content': m['content']} for m in history or []
               if m.get('role') in ('user', 'assistant') and str(m.get('content') or '').strip()]
    return cleaned[-_int_env('CHAT_HISTORY_MAX', 20):]


def _attempt(model: dict, prompt: str, history: list[dict]) -> core.ChatReply:
    reply = core.send_chat(model['provider'], model['model_id'], prompt,
                           max_tokens=_int_env('CHAT_MAX_TOKENS', 1024), history=history)
    reply.error = core.redact(reply.error)
    # Historique seulement : un échec ponctuel ne retire pas le statut PASS.
    db.save_result(model['provider'], model['model_id'], reply.status, reply.http_code,
                   reply.elapsed_seconds, reply.error, db.now(), 'chat', update_latest=False)
    return reply


def _answer(model: dict, reply: core.ChatReply) -> dict:
    return {'provider': model['provider'], 'model': model['model_id'], 'answer': reply.text,
            'latency_seconds': reply.elapsed_seconds, 'free_status': model['free_status'],
            'model_status': model['status']}


def chat(provider: str, model_id: str, prompt: str, allow_unverified: bool,
         history: list[dict] | None = None) -> dict:
    model = db.get_model(provider, model_id)
    if model is None:
        raise ServiceError(404, 'Model not found in database')
    if model['status'] != 'PASS':
        raise ServiceError(409, f"Model status is {model['status']}, not PASS")
    if model['free_status'] != FREE and not allow_unverified:
        raise ServiceError(403, 'Model is not verified free; set allow_unverified=true to accept possible costs')
    reply = _attempt(model, prompt, _clean_history(history))
    if reply.status != 'OK':
        raise ServiceError(HTTP_FOR_STATUS.get(reply.status, 502),
                           {'status': reply.status, 'http_code': reply.http_code, 'error': reply.error})
    return _answer(model, reply)


def chat_auto(prompt: str, allow_unverified: bool, provider: str | None = None,
              history: list[dict] | None = None) -> dict:
    ranked = candidates(allow_unverified, provider)
    if not ranked:
        raise ServiceError(404, 'No eligible PASS model available')
    history = _clean_history(history)
    attempts = []
    for model in ranked[:_int_env('CHAT_AUTO_MAX_ATTEMPTS', 3)]:
        reply = _attempt(model, prompt, history)
        attempts.append({'provider': model['provider'], 'model': model['model_id'],
                         'status': reply.status, 'http_code': reply.http_code,
                         'latency_seconds': reply.elapsed_seconds, 'error': reply.error})
        if reply.status == 'OK':
            return {**_answer(model, reply), 'attempts': attempts}
    raise ServiceError(502, {'message': 'All candidate models failed', 'attempts': attempts})
