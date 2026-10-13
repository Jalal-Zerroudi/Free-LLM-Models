"""Tests hors ligne de l'API SQLite : fournisseurs simulés, base temporaire."""
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
from core import ChatReply, Model, Result

SECRET = 'sk-test-secret-123456'


def fixture_results(folder: Path) -> None:
    core.write_csv(folder / 'results_openrouter.csv', [
        Result('openrouter', 'free-smart', 'verified_zero_price', 'PASS', 200, '2', 2.0, 40.0),
        Result('openrouter', 'free-fast', 'verified_zero_price', 'PASS', 200, '2', 0.5, 20.0),
        Result('openrouter', 'free-busy', 'verified_zero_price', 'RATE_LIMIT', 429, '', 0.1,
               error='rate limit'),
    ])
    core.write_csv(folder / 'results_groq.csv', [
        Result('groq', 'paid-maybe', 'unverified', 'PASS', 200, '2', 0.2),
        Result('groq', 'never-tested', 'unverified', 'SKIPPED_UNVERIFIED'),
    ])
    # Doublon volontaire : results_all.csv ne doit pas être importé.
    core.write_csv(folder / 'results_all.csv', [
        Result('groq', 'only-in-all', 'unverified', 'PASS', 200, '2', 0.1)])


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {
            'LLM_DB_PATH': str(folder / 'test.db'),
            'MODEL_RANKING': 'free,intelligence,latency',
            'CHAT_AUTO_MAX_ATTEMPTS': '3',
            'OPENROUTER_API_KEY': SECRET,
        })
        self.env.start()
        fixture_results(folder)
        service.import_csv_if_empty(folder)
        self.client = TestClient(api.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        service.wait_for_refresh(5)
        self.env.stop()
        self.tmp.cleanup()

    def ids(self, payload):
        return {m['model_id'] for m in payload['models']}

    def test_csv_import_and_model_lists(self):
        self.assertEqual(self.ids(self.client.get('/api/v1/models').json()),
                         {'free-smart', 'free-fast', 'paid-maybe'})
        everything = self.client.get('/api/v1/models/all').json()
        self.assertEqual(everything['count'], 5)
        statuses = {m['model_id']: m['status'] for m in everything['models']}
        self.assertEqual(statuses['never-tested'], 'UNTESTED')
        self.assertNotIn('only-in-all', statuses)
        self.assertEqual(len(db.history('openrouter', 'free-busy')), 1)

    def test_pass_is_not_free(self):
        paid = db.get_model('groq', 'paid-maybe')
        self.assertEqual((paid['status'], paid['free_status']), ('PASS', 'unverified'))

    def test_providers_and_filter(self):
        stats = {p['provider']: p for p in self.client.get('/api/v1/providers').json()}
        self.assertEqual(stats['openrouter']['total'], 3)
        self.assertEqual(stats['openrouter']['passed'], 2)
        self.assertEqual(stats['openrouter']['verified_free'], 3)
        self.assertEqual(stats['cohere']['total'], 0)
        r = self.client.get('/api/v1/providers/openrouter/models', params={'status': 'rate_limit'})
        self.assertEqual(self.ids(r.json()), {'free-busy'})
        self.assertEqual(self.client.get('/api/v1/providers/nope/models').status_code, 404)

    def test_top_ranking(self):
        self.assertEqual(self.client.get('/api/v1/top').json()['model_id'], 'free-smart')
        with mock.patch.dict(os.environ, {'MODEL_RANKING': 'latency'}):
            self.assertEqual(self.client.get('/api/v1/top').json()['model_id'], 'free-fast')
            self.assertEqual(self.client.get('/api/v1/top', params={'allow_unverified': True})
                             .json()['model_id'], 'paid-maybe')

    def test_model_ranks(self):
        body = self.client.get('/api/v1/models/all').json()
        self.assertEqual(body['ranking'], ['free', 'intelligence', 'latency'])
        ranks = [(m['model_id'], m['rank']) for m in body['models']]
        self.assertEqual(ranks[:3], [('free-smart', 1), ('free-fast', 2), ('paid-maybe', 3)])
        self.assertIsNone(dict(ranks)['free-busy'])
        with mock.patch.dict(os.environ, {'MODEL_RANKING': 'latency'}):
            ranked = [m['model_id'] for m in self.client.get('/api/v1/models').json()['models']]
        self.assertEqual(ranked, ['paid-maybe', 'free-fast', 'free-smart'])

    def test_chat_requires_pass_and_opt_in(self):
        with mock.patch.object(core, 'send_chat', return_value=ChatReply('OK', 200, 'Hi', 0.3)) as send:
            r = self.client.post('/api/v1/chat', json={'provider': 'groq', 'model': 'paid-maybe',
                                                       'prompt': 'hello'})
            self.assertEqual(r.status_code, 403)
            r = self.client.post('/api/v1/chat', json={'provider': 'openrouter', 'model': 'free-busy',
                                                       'prompt': 'hello'})
            self.assertEqual(r.status_code, 409)
            send.assert_not_called()
            r = self.client.post('/api/v1/chat', json={'provider': 'groq', 'model': 'paid-maybe',
                                                       'prompt': 'hello', 'allow_unverified': True})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()['answer'], 'Hi')

    def test_chat_maps_errors_and_hides_keys(self):
        failure = ChatReply('RATE_LIMIT', 429, '', 0.1, f'quota for key {SECRET}')
        with mock.patch.object(core, 'send_chat', return_value=failure):
            r = self.client.post('/api/v1/chat', json={'provider': 'openrouter', 'model': 'free-fast',
                                                       'prompt': 'hello'})
        self.assertEqual(r.status_code, 429)
        self.assertNotIn(SECRET, r.text)
        self.assertEqual(db.get_model('openrouter', 'free-fast')['status'], 'PASS')
        self.assertEqual(db.history('openrouter', 'free-fast')[0]['source'], 'chat')
        for path in ('/api/v1/providers', '/api/v1/models/all'):
            self.assertNotIn(SECRET, self.client.get(path).text)

    def test_chat_passes_history(self):
        history = [{'role': 'user', 'content': 'Je suis Ali'}, {'role': 'assistant', 'content': 'Salut'}]
        with mock.patch.object(core, 'send_chat', return_value=ChatReply('OK', 200, 'Ali', 0.2)) as send:
            r = self.client.post('/api/v1/chat', json={'provider': 'openrouter', 'model': 'free-fast',
                                                       'prompt': 'Mon nom ?', 'history': history})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(send.call_args.kwargs['history'], history)

    def test_chat_history_limit(self):
        history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'm{i}'} for i in range(30)]
        with mock.patch.dict(os.environ, {'CHAT_HISTORY_MAX': '20'}), \
                mock.patch.object(core, 'send_chat', return_value=ChatReply('OK', 200, 'ok', 0.2)) as send:
            self.client.post('/api/v1/chat', json={'provider': 'openrouter', 'model': 'free-fast',
                                                   'prompt': 'suite', 'history': history})
        sent = send.call_args.kwargs['history']
        self.assertEqual(len(sent), 20)
        self.assertEqual(sent[-1]['content'], 'm29')

    def test_chat_history_invalid_role(self):
        r = self.client.post('/api/v1/chat', json={'provider': 'openrouter', 'model': 'free-fast',
                                                   'prompt': 'x', 'history': [{'role': 'system', 'content': 'y'}]})
        self.assertEqual(r.status_code, 422)

    def test_chat_auto_passes_history(self):
        history = [{'role': 'user', 'content': 'a'}, {'role': 'assistant', 'content': 'b'}]
        replies = [ChatReply('RATE_LIMIT', 429, '', 0.1, 'busy'), ChatReply('OK', 200, 'c', 0.3)]
        with mock.patch.object(core, 'send_chat', side_effect=replies) as send:
            r = self.client.post('/api/v1/chat/auto', json={'prompt': 'q', 'history': history})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(all(c.kwargs['history'] == history for c in send.call_args_list))

    def test_chat_auto_fallback(self):
        replies = [ChatReply('RATE_LIMIT', 429, '', 0.1, 'busy'), ChatReply('OK', 200, 'Answer', 0.4)]
        with mock.patch.object(core, 'send_chat', side_effect=replies):
            r = self.client.post('/api/v1/chat/auto', json={'prompt': 'hello'})
        body = r.json()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(body['model'], 'free-fast')
        self.assertEqual([a['status'] for a in body['attempts']], ['RATE_LIMIT', 'OK'])

    def test_chat_auto_all_fail(self):
        with mock.patch.object(core, 'send_chat', return_value=ChatReply('TIMEOUT', None, '', 1.0, 't')):
            r = self.client.post('/api/v1/chat/auto', json={'prompt': 'hello'})
        self.assertEqual(r.status_code, 502)
        self.assertEqual(len(r.json()['detail']['attempts']), 2)

    def test_refresh_preserves_and_records_history(self):
        discovered = [Model('openrouter', 'free-smart', 'verified_zero_price', 41.0),
                      Model('openrouter', 'free-new', 'verified_zero_price')]
        tested = []

        def fake_test(model):
            tested.append(model.model_id)
            return Result(model.provider, model.model_id, model.free_status, 'PASS', 200, '2', 0.9)

        with mock.patch.object(core, 'discover', return_value=discovered), \
                mock.patch.object(core, 'test_model', side_effect=fake_test), \
                mock.patch.object(service, 'is_configured', return_value=True):
            r = self.client.post('/api/v1/refresh', json={'provider': 'openrouter'})
            self.assertEqual(r.status_code, 202)
            service.wait_for_refresh(5)
        status = self.client.get('/api/v1/refresh/status').json()
        self.assertEqual((status['state'], status['total'], status['completed']), ('done', 2, 2))
        self.assertEqual(tested, ['free-smart', 'free-new'])
        # Modèles absents de la nouvelle découverte conservés.
        self.assertEqual(db.get_model('openrouter', 'free-busy')['status'], 'RATE_LIMIT')
        self.assertEqual(db.get_model('openrouter', 'free-new')['status'], 'PASS')
        self.assertEqual(db.get_model('openrouter', 'free-smart')['intelligence_score'], 41.0)
        self.assertEqual([h['source'] for h in db.history('openrouter', 'free-smart')],
                         ['refresh', 'csv_import'])

    def test_refresh_skips_unverified_and_keeps_status(self):
        with mock.patch.object(core, 'discover', return_value=[Model('groq', 'paid-maybe')]), \
                mock.patch.object(core, 'test_model') as fake_test, \
                mock.patch.object(service, 'is_configured', return_value=True):
            self.client.post('/api/v1/refresh', json={'provider': 'groq'})
            service.wait_for_refresh(5)
        fake_test.assert_not_called()
        self.assertEqual(db.get_model('groq', 'paid-maybe')['status'], 'PASS')

    def test_refresh_discovery_error_keeps_data(self):
        with mock.patch.object(core, 'discover', side_effect=core.ProviderError(f'HTTP 401 {SECRET}')), \
                mock.patch.object(service, 'is_configured', return_value=True):
            self.client.post('/api/v1/refresh', json={'provider': 'openrouter'})
            service.wait_for_refresh(5)
        status = self.client.get('/api/v1/refresh/status')
        self.assertEqual(status.json()['state'], 'done')
        self.assertNotIn(SECRET, status.text)
        self.assertEqual(self.client.get('/api/v1/providers/openrouter/models').json()['count'], 3)

    def test_unknown_provider_refresh(self):
        self.assertEqual(self.client.post('/api/v1/refresh', json={'provider': 'nope'}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
