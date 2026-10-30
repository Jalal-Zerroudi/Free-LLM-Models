"""Tests hors ligne : réglages des fournisseurs (clés en SQLite) et page web."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import api
import core
import db
import service

ENV_KEY = 'gsk-env-key-1234567890'
DB_KEY = 'gsk-db-key-abcdefghij'
CUSTOM_KEY = 'tgt-custom-key-0987654321'


class FakeResponse:
    ok, status_code, reason = True, 200, 'OK'

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {f'{p.upper()}_API_KEY': '' for p in core.PROVIDERS}
        env.update(LLM_DB_PATH=str(Path(self.tmp.name) / 'test.db'), GROQ_API_KEY=ENV_KEY)
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        db.init_db()
        # La base de test reste vide : ne pas importer les vrais CSV de results/.
        self.no_import = mock.patch.object(service, 'import_csv_if_empty', return_value=0)
        self.no_import.start()
        self.client = TestClient(api.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.no_import.stop()
        self.env.stop()
        self.tmp.cleanup()

    def settings(self):
        return {p['name']: p for p in self.client.get('/api/v1/settings/providers').json()}

    def test_index_page(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Free LLM Models', r.text)

    def test_db_key_overrides_env_and_clear_falls_back(self):
        self.assertEqual(self.settings()['groq']['key_source'], 'env')
        r = self.client.put('/api/v1/settings/providers/groq', json={'api_key': DB_KEY})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(core.config('groq')[1], DB_KEY)
        self.assertEqual(r.json()['key_source'], 'database')
        self.assertEqual(r.json()['key_masked'], 'gsk...ghij')
        self.client.put('/api/v1/settings/providers/groq', json={'api_key': ''})
        self.assertEqual(core.config('groq')[1], ENV_KEY)
        self.assertEqual(self.settings()['groq']['key_source'], 'env')

    def test_host_override_keeps_key(self):
        self.client.put('/api/v1/settings/providers/groq', json={'api_key': DB_KEY})
        self.client.put('/api/v1/settings/providers/groq', json={'host': 'https://proxy.local/v1/'})
        self.assertEqual(core.config('groq'), ('https://proxy.local/v1', DB_KEY))

    def test_custom_provider_lifecycle(self):
        r = self.client.post('/api/v1/settings/providers',
                             json={'name': 'together', 'host': 'https://api.together.xyz/v1',
                                   'api_key': CUSTOM_KEY})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['kind'], 'openai_compatible')
        self.assertTrue(r.json()['configured'])
        self.assertIn('together', core.provider_names())
        self.assertEqual(self.client.get('/api/v1/providers/together/models').status_code, 200)
        self.assertIn('together', {p['provider'] for p in self.client.get('/api/v1/providers').json()})

        db.upsert_model('together', 'm1', 'unverified')
        self.assertEqual(self.client.delete('/api/v1/settings/providers/together').status_code, 204)
        self.assertNotIn('together', core.provider_names())
        self.assertIsNone(db.get_model('together', 'm1'))

    def test_validation(self):
        bad = [
            {'name': 'Bad Name', 'host': 'https://x.io'},
            {'name': 'ok-name', 'host': 'ftp://x.io'},
            {'name': 'with-space', 'host': 'https://x.io invalid'},
            {'name': 'with-userinfo', 'host': 'https://user:pass@x.io'},
            {'name': 'with-query', 'host': 'https://x.io?token=secret'},
            {'name': 'with-fragment', 'host': 'https://x.io#section'},
            {'name': 'groq', 'host': 'https://x.io'},
        ]
        for body in bad:
            with self.subTest(body=body):
                self.assertIn(
                    self.client.post('/api/v1/settings/providers', json=body).status_code,
                    (409, 422))
        self.assertEqual(self.client.delete('/api/v1/settings/providers/groq').status_code, 400)
        self.assertEqual(self.client.put('/api/v1/settings/providers/nope', json={}).status_code, 404)

    def test_writes_require_json(self):
        r = self.client.post('/api/v1/settings/providers', content='name=x',
                             headers={'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(r.status_code, 415)

    def test_keys_never_returned(self):
        self.client.put('/api/v1/settings/providers/groq', json={'api_key': DB_KEY})
        self.client.post('/api/v1/settings/providers',
                         json={'name': 'together', 'host': 'https://x.io/v1', 'api_key': CUSTOM_KEY})
        for path in ('/api/v1/settings/providers', '/api/v1/providers', '/api/v1/models/all'):
            text = self.client.get(path).text
            for key in (ENV_KEY, DB_KEY, CUSTOM_KEY):
                self.assertNotIn(key, text)
        self.assertNotIn(CUSTOM_KEY, core.redact(f'bad key {CUSTOM_KEY}'))
        self.assertNotIn(ENV_KEY, core.redact(f'old key {ENV_KEY}'))

    def test_custom_provider_discovery(self):
        service.add_provider('together', 'https://api.together.xyz/v1', CUSTOM_KEY)
        payload = {'data': [{'id': 'meta/llama-chat'}, {'id': 'text-embedding-x'}]}
        with mock.patch('requests.Session.get', return_value=FakeResponse(payload)) as get:
            found = core.discover('together')
        self.assertEqual(get.call_args.args[0], 'https://api.together.xyz/v1/models')
        self.assertEqual([(m.model_id, m.free_status) for m in found],
                         [('meta/llama-chat', 'unverified')])


if __name__ == '__main__':
    unittest.main()
