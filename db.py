"""Stockage SQLite : catalogue des modèles, historique des tests et suivi des refresh.

`status` (disponibilité observée) et `free_status` (gratuité vérifiée) sont des
colonnes indépendantes : PASS ne signifie jamais gratuit.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

OUTPUT = Path(__file__).resolve().parent / 'results'

SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_settings (
    name        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    host        TEXT,
    api_key     TEXT,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS models (
    provider            TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    free_status         TEXT NOT NULL DEFAULT 'unverified',
    intelligence_score  REAL,
    coding_score        REAL,
    status              TEXT NOT NULL DEFAULT 'UNTESTED',
    http_code           INTEGER,
    latency_seconds     REAL,
    error               TEXT NOT NULL DEFAULT '',
    last_verified_at    TEXT,
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    PRIMARY KEY (provider, model_id)
);
CREATE TABLE IF NOT EXISTS test_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    provider         TEXT NOT NULL,
    model_id         TEXT NOT NULL,
    status           TEXT NOT NULL,
    http_code        INTEGER,
    latency_seconds  REAL,
    error            TEXT NOT NULL DEFAULT '',
    tested_at        TEXT NOT NULL,
    source           TEXT NOT NULL,
    refresh_id       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_model ON test_runs (provider, model_id, tested_at);
CREATE TABLE IF NOT EXISTS refreshes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    provider          TEXT,
    allow_unverified  INTEGER NOT NULL,
    state             TEXT NOT NULL,
    total             INTEGER NOT NULL DEFAULT 0,
    completed         INTEGER NOT NULL DEFAULT 0,
    current_model     TEXT,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    errors            TEXT NOT NULL DEFAULT '[]'
);
"""

MODEL_COLUMNS = ('provider', 'model_id', 'free_status', 'intelligence_score', 'coding_score',
                 'status', 'http_code', 'latency_seconds', 'error', 'last_verified_at',
                 'first_seen_at', 'last_seen_at')


def db_path() -> Path:
    custom = os.getenv('LLM_DB_PATH', '').strip()
    return Path(custom) if custom else OUTPUT / 'models.db'


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


_initialized: set[Path] = set()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if path not in _initialized:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript(SCHEMA)
            _initialized.add(path)
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    _initialized.discard(db_path())
    with connect():
        pass


def is_empty() -> bool:
    with connect() as conn:
        return conn.execute('SELECT COUNT(*) FROM models').fetchone()[0] == 0


def upsert_model(provider: str, model_id: str, free_status: str,
                 intelligence_score: float | None = None, coding_score: float | None = None,
                 seen_at: str | None = None) -> None:
    seen_at = seen_at or now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO models (provider, model_id, free_status, intelligence_score,
                                   coding_score, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (provider, model_id) DO UPDATE SET
                   free_status = excluded.free_status,
                   intelligence_score = COALESCE(excluded.intelligence_score, intelligence_score),
                   coding_score = COALESCE(excluded.coding_score, coding_score),
                   last_seen_at = excluded.last_seen_at""",
            (provider, model_id, free_status, intelligence_score, coding_score, seen_at, seen_at))


def save_result(provider: str, model_id: str, status: str, http_code: int | None,
                latency_seconds: float | None, error: str, tested_at: str, source: str,
                refresh_id: int | None = None, update_latest: bool = True) -> None:
    """Ajoute une entrée d'historique ; met à jour l'état courant si demandé."""
    with connect() as conn:
        conn.execute(
            """INSERT INTO test_runs (provider, model_id, status, http_code, latency_seconds,
                                      error, tested_at, source, refresh_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (provider, model_id, status, http_code, latency_seconds, error or '',
             tested_at, source, refresh_id))
        if update_latest:
            conn.execute(
                """UPDATE models SET status = ?, http_code = ?, latency_seconds = ?, error = ?,
                                     last_verified_at = ?
                   WHERE provider = ? AND model_id = ?""",
                (status, http_code, latency_seconds, error or '', tested_at, provider, model_id))


def list_models(provider: str | None = None, status: str | None = None) -> list[dict]:
    query, params = 'SELECT * FROM models WHERE 1 = 1', []
    if provider:
        query += ' AND provider = ?'
        params.append(provider)
    if status:
        query += ' AND status = ?'
        params.append(status)
    query += ' ORDER BY provider, model_id'
    with connect() as conn:
        return [dict(r) for r in conn.execute(query, params)]


def get_model(provider: str, model_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute('SELECT * FROM models WHERE provider = ? AND model_id = ?',
                           (provider, model_id)).fetchone()
    return dict(row) if row else None


def history(provider: str, model_id: str, limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM test_runs WHERE provider = ? AND model_id = ?
               ORDER BY tested_at DESC, id DESC LIMIT ?""", (provider, model_id, limit))
        return [dict(r) for r in rows]


def provider_stats() -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    with connect() as conn:
        for row in conn.execute(
                """SELECT provider, status, COUNT(*) AS n,
                          SUM(free_status = 'verified_zero_price') AS free_n,
                          MAX(last_verified_at) AS last_verified
                   FROM models GROUP BY provider, status"""):
            entry = stats.setdefault(row['provider'], {
                'total': 0, 'by_status': {}, 'verified_free': 0, 'last_verified_at': None})
            entry['total'] += row['n']
            entry['by_status'][row['status']] = row['n']
            entry['verified_free'] += row['free_n'] or 0
            if row['last_verified'] and (entry['last_verified_at'] is None
                                         or row['last_verified'] > entry['last_verified_at']):
                entry['last_verified_at'] = row['last_verified']
    return stats


def create_refresh(provider: str | None, allow_unverified: bool) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO refreshes (provider, allow_unverified, state, started_at)
               VALUES (?, ?, 'running', ?)""", (provider, int(allow_unverified), now()))
        return int(cur.lastrowid)


def update_refresh(refresh_id: int, **fields: Any) -> None:
    allowed = {'state', 'total', 'completed', 'current_model', 'finished_at', 'errors'}
    if not fields or set(fields) - allowed:
        raise ValueError(f'Champs refresh invalides: {set(fields) - allowed}')
    if 'errors' in fields:
        fields['errors'] = json.dumps(fields['errors'], ensure_ascii=False)
    sets = ', '.join(f'{k} = ?' for k in fields)
    with connect() as conn:
        conn.execute(f'UPDATE refreshes SET {sets} WHERE id = ?', (*fields.values(), refresh_id))


def _refresh_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    data['allow_unverified'] = bool(data['allow_unverified'])
    data['errors'] = json.loads(data['errors'] or '[]')
    return data


def get_refresh(refresh_id: int) -> dict | None:
    with connect() as conn:
        return _refresh_row(conn.execute('SELECT * FROM refreshes WHERE id = ?',
                                         (refresh_id,)).fetchone())


def latest_refresh() -> dict | None:
    with connect() as conn:
        return _refresh_row(conn.execute(
            'SELECT * FROM refreshes ORDER BY id DESC LIMIT 1').fetchone())


def get_provider_setting(name: str) -> dict | None:
    with connect() as conn:
        row = conn.execute('SELECT * FROM provider_settings WHERE name = ?', (name,)).fetchone()
    return dict(row) if row else None


def list_provider_settings(kind: str | None = None) -> list[dict]:
    query, params = 'SELECT * FROM provider_settings', []
    if kind:
        query += ' WHERE kind = ?'
        params.append(kind)
    with connect() as conn:
        return [dict(r) for r in conn.execute(query + ' ORDER BY name', params)]


_UNSET: Any = object()


def upsert_provider_setting(name: str, kind: str, host: str | None = _UNSET,
                            api_key: str | None = _UNSET) -> None:
    """Crée ou met à jour un réglage ; un champ omis garde sa valeur, None l'efface."""
    current = get_provider_setting(name) or {'host': None, 'api_key': None}
    host = current['host'] if host is _UNSET else host
    api_key = current['api_key'] if api_key is _UNSET else api_key
    with connect() as conn:
        conn.execute(
            """INSERT INTO provider_settings (name, kind, host, api_key, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (name) DO UPDATE SET kind = excluded.kind, host = excluded.host,
                   api_key = excluded.api_key, updated_at = excluded.updated_at""",
            (name, kind, host, api_key, now()))


def delete_provider_setting(name: str, with_models: bool = False) -> None:
    with connect() as conn:
        conn.execute('DELETE FROM provider_settings WHERE name = ?', (name,))
        if with_models:
            conn.execute('DELETE FROM models WHERE provider = ?', (name,))


def fail_stale_refreshes() -> None:
    """Un refresh 'running' au démarrage a été interrompu par l'arrêt du serveur."""
    with connect() as conn:
        conn.execute("""UPDATE refreshes SET state = 'failed', finished_at = ?, current_model = NULL
                        WHERE state = 'running'""", (now(),))
