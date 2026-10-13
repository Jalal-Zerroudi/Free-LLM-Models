"""Tests hors ligne, sans appel API ni clé."""
import unittest
from unittest import mock

from core import (Model, Result, classify_error, eligible_for_test,
                  is_chat_id, or_free, response_text, send_chat, top_results)

HISTORY = [{'role': 'user', 'content': 'Je m\'appelle Ali.'},
           {'role': 'assistant', 'content': 'Bonjour Ali.'}]


class FakeResponse:
    ok, status_code, reason = True, 200, 'OK'

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class SendChatTest(unittest.TestCase):
    def payload(self, provider, reply):
        with mock.patch('requests.Session.post', return_value=FakeResponse(reply)) as post:
            result = send_chat(provider, 'm', 'Mon nom ?', max_tokens=50, history=HISTORY)
        self.assertEqual(result.status, 'OK')
        return post.call_args.kwargs['json']

    def test_openai_history(self):
        body = self.payload('groq', {'choices': [{'message': {'content': 'Ali'}}]})
        self.assertEqual(body['messages'], HISTORY + [{'role': 'user', 'content': 'Mon nom ?'}])

    def test_gemini_history_uses_model_role(self):
        body = self.payload('gemini', {'candidates': [{'content': {'parts': [{'text': 'Ali'}]}}]})
        self.assertEqual([c['role'] for c in body['contents']], ['user', 'model', 'user'])
        self.assertEqual(body['contents'][2]['parts'], [{'text': 'Mon nom ?'}])


class CoreTest(unittest.TestCase):
    def test_free_pricing(self):
        self.assertTrue(or_free({'pricing': {'prompt': '0', 'completion': '0', 'request': '0'}}))
        self.assertFalse(or_free({'pricing': {'prompt': '0.01', 'completion': '0'}}))
        self.assertFalse(or_free({'pricing': {'prompt': '0'}}))

    def test_safety_gate(self):
        self.assertFalse(eligible_for_test(Model('groq', 'example'), False))
        self.assertTrue(eligible_for_test(Model('groq', 'example'), True))
        self.assertTrue(eligible_for_test(Model('openrouter', 'x', 'verified_zero_price'), False))

    def test_model_filter(self):
        self.assertFalse(is_chat_id('whisper-large-v3'))
        self.assertFalse(is_chat_id('gemini-tts'))
        self.assertTrue(is_chat_id('gpt-oss:120b'))

    def test_error_codes(self):
        self.assertEqual(classify_error(402, 'payment'), 'PAYMENT_REQUIRED')
        self.assertEqual(classify_error(403, 'not allowed'), 'ACCESS_DENIED')
        self.assertEqual(classify_error(429, 'busy'), 'RATE_LIMIT')
        self.assertEqual(classify_error(500, 'ResourceExhausted'), 'RATE_LIMIT')

    def test_responses(self):
        self.assertEqual(response_text('ollama', {'message': {'content': '2'}}), '2')
        self.assertEqual(response_text('gemini', {'candidates': [{'content': {'parts': [{'text': '2'}]}}]}), '2')
        self.assertEqual(response_text('cohere', {'message': {'content': [{'type': 'text', 'text': '2'}]}}), '2')
        self.assertEqual(response_text('groq', {'choices': [{'message': {'content': '2'}}]}), '2')

    def test_ranking(self):
        rows = [dict(provider='openrouter', model='a', status='PASS', elapsed_seconds='1.2', intelligence_score='10'),
                dict(provider='openrouter', model='b', status='PASS', elapsed_seconds='0.7', intelligence_score='20'),
                dict(provider='openrouter', model='c', status='RATE_LIMIT', elapsed_seconds='0.1', intelligence_score='30')]
        self.assertEqual(top_results(rows, 'speed')[0]['model'], 'b')
        self.assertEqual(top_results(rows, 'intelligence')[0]['model'], 'b')


if __name__ == '__main__':
    unittest.main()
