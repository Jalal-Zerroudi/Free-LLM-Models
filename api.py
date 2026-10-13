"""API REST locale adossée à SQLite et page web. Exécuter : uvicorn api:app --host 127.0.0.1 --port 8000

Les GET lisent uniquement SQLite. Seuls POST /refresh, /chat et /chat/auto
appellent les fournisseurs. Les clés API ne sont jamais renvoyées (seulement masquées).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import db
import service
from core import config, provider_names

INDEX_HTML = Path(__file__).resolve().parent / 'static' / 'index.html'


@asynccontextmanager
async def lifespan(_: FastAPI):
    service.import_csv_if_empty()
    db.fail_stale_refreshes()
    yield


app = FastAPI(title='Free LLM Models', version='0.3.0', lifespan=lifespan)
# Contre le DNS rebinding : seuls les noms locaux sont acceptés.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def require_json_writes(request: Request, call_next):
    # Un formulaire d'un autre site ne peut pas envoyer de JSON sans pré-vol CORS (refusé).
    if request.method in ('POST', 'PUT', 'PATCH') and request.url.path.startswith('/api/'):
        if not request.headers.get('content-type', '').startswith('application/json'):
            return JSONResponse({'detail': 'Content-Type must be application/json'}, status_code=415)
    return await call_next(request)


class ModelOut(BaseModel):
    provider: str
    model_id: str
    status: str
    free_status: str
    latency_seconds: float | None = None
    http_code: int | None = None
    intelligence_score: float | None = None
    coding_score: float | None = None
    error: str = ''
    last_verified_at: str | None = None
    first_seen_at: str
    last_seen_at: str
    rank: int | None = None


class ModelList(BaseModel):
    count: int
    ranking: list[str] | None = None
    models: list[ModelOut]


class ProviderOut(BaseModel):
    provider: str
    configured: bool
    host: str
    total: int
    passed: int
    verified_free: int
    by_status: dict[str, int]
    last_verified_at: str | None = None


class RefreshRequest(BaseModel):
    provider: str | None = None
    allow_unverified: bool = False


class RefreshOut(BaseModel):
    id: int
    provider: str | None
    allow_unverified: bool
    state: str
    total: int
    completed: int
    current_model: str | None
    started_at: str
    finished_at: str | None
    errors: list[dict]


class ChatMessage(BaseModel):
    role: Literal['user', 'assistant']
    content: str


class ChatRequest(BaseModel):
    provider: str
    model: str
    prompt: str = Field(min_length=1)
    allow_unverified: bool = False
    history: list[ChatMessage] = []


class AutoChatRequest(BaseModel):
    prompt: str = Field(min_length=1)
    provider: str | None = None
    allow_unverified: bool = False
    history: list[ChatMessage] = []


class ChatOut(BaseModel):
    provider: str
    model: str
    answer: str
    latency_seconds: float
    free_status: str
    model_status: str
    attempts: list[dict] | None = None


class ProviderSettingOut(BaseModel):
    name: str
    kind: str
    host: str
    configured: bool
    key_set: bool
    key_masked: str
    key_source: str


class ProviderCreate(BaseModel):
    name: str
    host: str
    api_key: str = ''


class ProviderUpdate(BaseModel):
    host: str | None = None
    api_key: str | None = None


def check_provider(provider: str | None) -> None:
    if provider is not None and provider not in provider_names():
        raise HTTPException(404, f'Unknown provider: {provider}')


def with_ranks(rows: list[dict]) -> list[dict]:
    """Rang global (classement /top) pour les PASS, placés en tête ; les autres suivent."""
    ranked = service.rank([r for r in rows if r['status'] == 'PASS'])
    for position, row in enumerate(ranked, 1):
        row['rank'] = position
    return ranked + [r for r in rows if r['status'] != 'PASS']


def call(fn, *args):
    try:
        return fn(*args)
    except service.ServiceError as exc:
        raise HTTPException(exc.http_status, exc.detail) from exc


@app.get('/', include_in_schema=False)
def index():
    return FileResponse(INDEX_HTML, media_type='text/html')


@app.get('/api/v1/health')
def health():
    return {'status': 'ok'}


@app.get('/api/v1/settings/providers', response_model=list[ProviderSettingOut])
def provider_settings():
    return [service.provider_view(p) for p in provider_names()]


@app.post('/api/v1/settings/providers', status_code=201, response_model=ProviderSettingOut)
def add_provider(body: ProviderCreate):
    return call(service.add_provider, body.name, body.host, body.api_key)


@app.put('/api/v1/settings/providers/{name}', response_model=ProviderSettingOut)
def update_provider(name: str, body: ProviderUpdate):
    return call(service.update_provider, name, body.host, body.api_key)


@app.delete('/api/v1/settings/providers/{name}', status_code=204)
def delete_provider(name: str):
    call(service.delete_provider, name)


@app.get('/api/v1/models', response_model=ModelList)
def models():
    rows = with_ranks(db.list_models(status='PASS'))
    return {'count': len(rows), 'ranking': service.ranking_order(), 'models': rows}


@app.get('/api/v1/models/all', response_model=ModelList)
def models_all():
    rows = with_ranks(db.list_models())
    return {'count': len(rows), 'ranking': service.ranking_order(), 'models': rows}


@app.get('/api/v1/providers', response_model=list[ProviderOut])
def providers():
    stats = db.provider_stats()
    out = []
    for p in provider_names():
        s = stats.get(p, {'total': 0, 'by_status': {}, 'verified_free': 0, 'last_verified_at': None})
        out.append({'provider': p, 'configured': service.is_configured(p), 'host': config(p)[0],
                    'passed': s['by_status'].get('PASS', 0), **s})
    return out


@app.get('/api/v1/providers/{provider}/models', response_model=ModelList)
def provider_models(provider: str, status: str | None = Query(None, description='e.g. PASS, RATE_LIMIT')):
    check_provider(provider)
    rows = db.list_models(provider=provider, status=status.upper() if status else None)
    return {'count': len(rows), 'models': rows}


@app.post('/api/v1/refresh', status_code=202, response_model=RefreshOut)
def refresh(body: RefreshRequest | None = None):
    body = body or RefreshRequest()
    check_provider(body.provider)
    refresh_id = call(service.start_refresh, body.provider, body.allow_unverified)
    return db.get_refresh(refresh_id)


@app.get('/api/v1/refresh/status', response_model=RefreshOut)
def refresh_status():
    latest = db.latest_refresh()
    if latest is None:
        raise HTTPException(404, 'No refresh has been run yet')
    return latest


@app.get('/api/v1/refresh/{refresh_id}', response_model=RefreshOut)
def refresh_by_id(refresh_id: int):
    found = db.get_refresh(refresh_id)
    if found is None:
        raise HTTPException(404, 'Refresh not found')
    return found


@app.post('/api/v1/chat', response_model=ChatOut, response_model_exclude_none=True)
def chat(body: ChatRequest):
    check_provider(body.provider)
    return call(service.chat, body.provider, body.model, body.prompt, body.allow_unverified,
                [m.model_dump() for m in body.history])


@app.post('/api/v1/chat/auto', response_model=ChatOut)
def chat_auto(body: AutoChatRequest):
    check_provider(body.provider)
    return call(service.chat_auto, body.prompt, body.allow_unverified, body.provider,
                [m.model_dump() for m in body.history])


@app.get('/api/v1/top', response_model=ModelOut)
def top(allow_unverified: bool = Query(False), provider: str | None = Query(None)):
    check_provider(provider)
    ranked = service.candidates(allow_unverified, provider)
    if not ranked:
        raise HTTPException(404, 'No eligible PASS model available')
    return ranked[0]
