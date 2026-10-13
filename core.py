"""Découverte et test séquentiel de modèles de conversation sur huit API.

Les endpoints /models ne garantissent PAS que les modèles sont gratuits.
Par défaut, seuls les modèles explicitement à prix zéro chez OpenRouter sont testés.
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')

import db  # noqa: E402  (après load_dotenv : LLM_DB_PATH peut venir de .env)

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'results'
QUESTION = 'What is 1 + 1? Reply with only the number.'
DEFAULT_HOSTS = {
    'groq': 'https://api.groq.com/openai/v1',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta',
    'cerebras': 'https://api.cerebras.ai/v1',
    'sambanova': 'https://api.sambanova.ai/v1',
    'mistral': 'https://api.mistral.ai/v1',
    'cohere': 'https://api.cohere.ai/v2',
    'openrouter': 'https://openrouter.ai/api/v1',
    'ollama': 'https://ollama.com',
}
PROVIDERS = tuple(DEFAULT_HOSTS)


@dataclass
class Model:
    provider: str
    model_id: str
    free_status: str = 'unverified'
    intelligence_score: float | None = None
    coding_score: float | None = None


@dataclass
class Result:
    provider: str
    model: str
    free_status: str
    status: str
    http_code: int | None = None
    answer: str = ''
    elapsed_seconds: float = 0.0
    intelligence_score: float | None = None
    coding_score: float | None = None
    error: str = ''


class ProviderError(Exception):
    pass


CUSTOM_KIND = 'openai_compatible'


def _setting(provider: str) -> dict:
    try:
        return db.get_provider_setting(provider) or {}
    except sqlite3.Error:
        return {}


def _env_name(provider: str) -> str:
    return provider.upper().replace('-', '_')


def provider_names() -> tuple[str, ...]:
    """Fournisseurs intégrés, puis fournisseurs compatibles OpenAI ajoutés en base."""
    try:
        custom = [s['name'] for s in db.list_provider_settings(CUSTOM_KIND)]
    except sqlite3.Error:
        custom = []
    return PROVIDERS + tuple(n for n in custom if n not in PROVIDERS)


def config(provider: str) -> tuple[str, str]:
    # Priorité : réglage SQLite (page web), puis .env, puis hôte par défaut.
    setting, name = _setting(provider), _env_name(provider)
    host = (setting.get('host') or os.getenv(f'{name}_HOST', '').strip()
            or DEFAULT_HOSTS.get(provider, ''))
    key = setting.get('api_key') or os.getenv(f'{name}_API_KEY', '')
    return host.strip().rstrip('/'), key.strip()


def key_source(provider: str) -> str:
    if _setting(provider).get('api_key'):
        return 'database'
    return 'env' if os.getenv(f'{_env_name(provider)}_API_KEY', '').strip() else 'none'


def mask(key: str) -> str:
    if not key:
        return ''
    return '****' if len(key) <= 8 else f'{key[:3]}...{key[-4:]}'


def session_for(provider: str) -> requests.Session:
    _, key = config(provider)
    client = requests.Session()
    client.headers.update({'Accept': 'application/json', 'Content-Type': 'application/json'})
    if key and provider != 'gemini':
        client.headers.update({'Authorization': f'Bearer {key}'})
    return client


def parse_json(response: requests.Response) -> dict:
    try:
        data = response.json()
    except ValueError as exc:
        raise ProviderError(f'Réponse non JSON (HTTP {response.status_code})') from exc
    if not isinstance(data, dict):
        raise ProviderError('Réponse JSON inattendue (objet requis)')
    return data


def error_message(data: dict, response: requests.Response) -> str:
    err = data.get('error', data.get('message', response.reason))
    if isinstance(err, dict):
        return str(err.get('message') or err.get('code') or err)
    return str(err)


def check_response(response: requests.Response) -> dict:
    data = parse_json(response)
    if not response.ok or data.get('error'):
        msg = error_message(data, response)
        raise ProviderError(f'HTTP {response.status_code}: {msg[:400]}')
    return data


def is_chat_id(model_id: str) -> bool:
    # Filtrage conservateur des formats notoirement non conversationnels.
    bad = ('embedding', 'rerank', 'whisper', 'transcrib', 'moderation',
           'prompt-guard', 'safeguard', 'content-safety', 'tts', 'lyria',
           'speech', 'audio-', '-audio', 'veo-', '-image', 'image-',
           '-video', 'video-', '-live', 'live-')
    return not any(x in model_id.lower() for x in bad)


def or_free(item: dict) -> bool:
    pricing = item.get('pricing') or {}
    # Tous les champs de facturation par texte doivent être connus et à zéro.
    keys = ('prompt', 'completion', 'request')
    if not isinstance(pricing, dict) or any(k not in pricing for k in ('prompt', 'completion')):
        return False
    try:
        return all(float(pricing.get(k, 0) or 0) == 0 for k in keys)
    except (ValueError, TypeError):
        return False


def list_standard(client: requests.Session, host: str) -> list[dict]:
    resp = client.get(f'{host}/models', timeout=(15, 35))
    data = check_response(resp)
    models = data.get('data')
    if not isinstance(models, list):
        raise ProviderError('Champ data[] absent pour /models')
    return [m for m in models if isinstance(m, dict)]


def discover(provider: str, with_benchmarks: bool = True) -> list[Model]:
    if provider not in provider_names():
        raise ValueError(f'Fournisseur inconnu: {provider}')
    host, key = config(provider)
    if not key and provider != 'ollama':
        return []
    # Pour le Cloud Ollama, une cle est requise; en local, elle ne l'est pas.
    if provider == 'ollama' and host.startswith('https://ollama.com') and not key:
        return []

    client = session_for(provider)
    try:
        if provider == 'gemini':
            records = []
            token = None
            while True:
                params: dict[str, Any] = {'pageSize': 1000}
                if token:
                    params['pageToken'] = token
                r = client.get(f'{host}/models', headers={'x-goog-api-key': key},
                               params=params, timeout=(15, 35))
                data = check_response(r)
                records.extend(data.get('models') or [])
                token = data.get('nextPageToken')
                if not token:
                    break
            models = [Model(provider, m['name'].removeprefix('models/'))
                      for m in records if isinstance(m, dict) and m.get('name')
                      and 'generateContent' in (m.get('supportedGenerationMethods') or [])
                      and is_chat_id(m['name'])]
        elif provider == 'ollama':
            data = check_response(client.get(f'{host}/api/tags', timeout=(15, 35)))
            models = [Model(provider, m.get('name') or m.get('model'))
                      for m in data.get('models', []) if isinstance(m, dict)
                      and (m.get('name') or m.get('model'))
                      and is_chat_id(m.get('name') or m.get('model'))]
        elif provider == 'cohere':
            # Lister les modèles sur API v1, même si les requêtes chat sont sur API v2.
            root = host.rsplit('/v2', 1)[0] if host.endswith('/v2') else host.rsplit('/v1', 1)[0]
            models = []
            token = None
            while True:
                params = {'page_size': 100}
                if token:
                    params['page_token'] = token
                data = check_response(client.get(f'{root}/v1/models',
                                                 params=params, timeout=(15, 35)))
                for m in data.get('models', []):
                    if not isinstance(m, dict):
                        continue
                    name = m.get('name')
                    endpoints = m.get('endpoints') or m.get('default_endpoints') or []
                    if name and not m.get('is_deprecated') and 'chat' in endpoints and is_chat_id(name):
                        models.append(Model(provider, name))
                token = data.get('next_page_token')
                if not token:
                    break
        else:
            records = list_standard(client, host)
            models = []
            for item in records:
                mid = item.get('id')
                if not mid or not is_chat_id(mid):
                    continue
                if provider == 'openrouter':
                    if mid == 'openrouter/free':
                        continue  # routeur virtuel, non-modèle de base
                    arch = item.get('architecture') or {}
                    if isinstance(arch, dict):
                        inputs, outputs = arch.get('input_modalities'), arch.get('output_modalities')
                        if isinstance(inputs, list) and 'text' not in inputs:
                            continue
                        if isinstance(outputs, list) and 'text' not in outputs:
                            continue
                    if not or_free(item):
                        continue
                    status = 'verified_zero_price'
                else:
                    status = 'unverified'
                    if provider == 'mistral':
                        caps = item.get('capabilities') or {}
                        if caps.get('completion_chat') is False or item.get('archived'):
                            continue
                    if provider == 'groq' and item.get('active') is False:
                        continue
                models.append(Model(provider, mid, status))

            if provider == 'openrouter' and with_benchmarks:
                try:
                    r = client.get(f'{host}/benchmarks',
                                   params={'source': 'artificial-analysis'}, timeout=(15, 35))
                    data = check_response(r)
                    lookup = {b.get('model_permaslug'): b for b in data.get('data', [])
                              if isinstance(b, dict)}
                    metadata = {m['id']: m for m in records if m.get('id')}
                    for m in models:
                        item = metadata.get(m.model_id, {})
                        benchmark = next((lookup[k] for k in (
                            item.get('canonical_slug'), m.model_id,
                            m.model_id.removesuffix(':free')) if k in lookup), None)
                        if benchmark:
                            for attr, key_ in [('intelligence_score', 'intelligence_index'),
                                               ('coding_score', 'coding_index')]:
                                try:
                                    value = benchmark.get(key_)
                                    setattr(m, attr, float(value) if value is not None else None)
                                except (TypeError, ValueError):
                                    pass
                except (ProviderError, requests.RequestException) as exc:
                    print(f'Benchmarks OpenRouter indisponibles: {exc}')

        # Préserver l'ordre d'API tout en retirant les doublons d'identifiants.
        unique: dict[str, Model] = {}
        for m in models:
            if m.model_id:
                unique.setdefault(m.model_id, m)
        return list(unique.values())
    finally:
        client.close()


def response_text(provider: str, data: dict) -> str:
    if provider == 'ollama':
        return str((data.get('message') or {}).get('content') or '').strip()
    if provider == 'gemini':
        chunks = []
        for cand in data.get('candidates') or []:
            for p in ((cand.get('content') or {}).get('parts') or []):
                if isinstance(p, dict) and isinstance(p.get('text'), str):
                    chunks.append(p['text'])
        return ' '.join(chunks).strip()
    if provider == 'cohere':
        content = (data.get('message') or {}).get('content') or []
        return ' '.join(c.get('text', '') for c in content
                        if isinstance(c, dict) and isinstance(c.get('text'), str)).strip()
    choice = (data.get('choices') or [{}])[0]
    content = (choice.get('message') or {}).get('content') or ''
    if isinstance(content, list):
        return ' '.join(str(p.get('text', '')) for p in content if isinstance(p, dict)).strip()
    return str(content).strip()


def classify_error(code: int, error: str) -> str:
    lower = error.lower()
    if code == 402 or any(x in lower for x in (
        'payment required', 'not in the free plan', 'insufficient balance',
        'insufficient credits', 'billing required', 'upgrade your plan')):
        return 'PAYMENT_REQUIRED'
    if code == 429 or 'rate limit' in lower or 'resourceexhausted' in lower or 'quota exceeded' in lower:
        return 'RATE_LIMIT'
    if code in (401, 403):
        return 'ACCESS_DENIED'
    return 'API_ERROR'


def redact(text: str) -> str:
    """Retire toute valeur de clé API configurée d'un message (erreurs, URL)."""
    if not text:
        return text
    for provider in provider_names():
        for key in (config(provider)[1], os.getenv(f'{_env_name(provider)}_API_KEY', '').strip()):
            if len(key) >= 6:
                text = text.replace(key, '***')
    return text


@dataclass
class ChatReply:
    # status = 'OK' en cas de réponse exploitable, sinon une catégorie d'erreur.
    status: str
    http_code: int | None = None
    text: str = ''
    elapsed_seconds: float = 0.0
    error: str = ''


def send_chat(provider: str, model_id: str, prompt: str, max_tokens: int = 1024,
              history: list[dict] | None = None) -> ChatReply:
    """Un appel de génération bloquant, sans délai fixe ni nouvelle tentative.

    `history` : messages précédents [{'role': 'user'|'assistant', 'content': str}].
    """
    host, key = config(provider)
    reply = ChatReply(status='ERROR')
    client = session_for(provider)
    messages = [{'role': m['role'], 'content': m['content']} for m in history or []]
    messages.append({'role': 'user', 'content': prompt})
    start = time.perf_counter()
    try:
        if provider == 'ollama':
            url = f'{host}/api/chat'
            payload = {'model': model_id, 'messages': messages,
                       'stream': False, 'options': {'num_predict': max_tokens}}
        elif provider == 'gemini':
            url = f'{host}/models/{model_id}:generateContent'
            contents = [{'role': 'model' if m['role'] == 'assistant' else 'user',
                         'parts': [{'text': m['content']}]} for m in messages]
            payload = {'contents': contents, 'generationConfig': {'maxOutputTokens': max_tokens}}
        elif provider == 'cohere':
            url = f'{host}/chat'
            payload = {'model': model_id, 'messages': messages,
                       'stream': False, 'max_tokens': max_tokens}
        else:
            url = f'{host}/chat/completions'
            payload = {'model': model_id, 'messages': messages,
                       'stream': False, 'max_tokens': max_tokens}
        headers = {'x-goog-api-key': key} if provider == 'gemini' else None
        resp = client.post(url, json=payload, headers=headers, timeout=(15, 180))
        reply.http_code = resp.status_code
        data = parse_json(resp)
        if not resp.ok or data.get('error'):
            reply.error = error_message(data, resp)[:500]
            reply.status = classify_error(resp.status_code, reply.error)
        else:
            reply.text = response_text(provider, data)
            reply.status = 'OK' if reply.text else 'EMPTY'
    except requests.Timeout:
        reply.status = 'TIMEOUT'
        reply.error = 'Timeout (15 s connexion / 180 s inactivité réseau)'
    except requests.RequestException as exc:
        reply.status = 'NETWORK_ERROR'
        reply.error = str(exc)[:500]
    except (ProviderError, KeyError, IndexError, TypeError, ValueError) as exc:
        reply.status = 'INVALID_RESPONSE'
        reply.error = str(exc)[:500]
    finally:
        reply.elapsed_seconds = round(time.perf_counter() - start, 3)
        reply.error = redact(reply.error)
        client.close()
    return reply


def test_model(model: Model, question: str = QUESTION) -> Result:
    # Ollama conserve sa limite historique plus courte.
    limit = 128 if model.provider == 'ollama' else 256
    reply = send_chat(model.provider, model.model_id, question, max_tokens=limit)
    result = Result(provider=model.provider, model=model.model_id,
                    free_status=model.free_status, status=reply.status,
                    http_code=reply.http_code, answer=reply.text,
                    elapsed_seconds=reply.elapsed_seconds,
                    intelligence_score=model.intelligence_score,
                    coding_score=model.coding_score, error=reply.error)
    if reply.status == 'OK':
        normalized = reply.text.strip().replace('**', '')
        result.status = 'PASS' if re.fullmatch(r'2(?:\.0+)?[.!]?', normalized) else 'CHECK'
    return result


def eligible_for_test(model: Model, allow_unverified: bool) -> bool:
    # La disponibilité et la gratuité sont des notions distinctes.
    return model.free_status == 'verified_zero_price' or allow_unverified


def write_csv(path: Path, results: list[Result]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(Result.__dataclass_fields__)
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(asdict(r) for r in results)


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def top_results(rows: list[dict], metric: str) -> list[dict]:
    passed = [r for r in rows if r.get('status') == 'PASS']
    if metric == 'speed':
        return sorted(passed, key=lambda r: float(r.get('elapsed_seconds') or 1e9))
    if metric == 'intelligence':
        passed = [r for r in passed if r.get('intelligence_score') not in ('', None, 'None')]
        return sorted(passed, key=lambda r: float(r['intelligence_score']), reverse=True)
    raise ValueError('metric doit être speed ou intelligence')
