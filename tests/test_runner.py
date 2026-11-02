"""Tests hors ligne de l'orchestrateur CLI."""
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import runner


class RunnerConfigurationTest(unittest.TestCase):
    def test_loopback_ollama_without_key_is_discovered(self):
        with mock.patch.object(
            runner, 'config', return_value=('http://127.0.0.1:11434', '')
        ), mock.patch.object(
            runner, 'discover', return_value=[]
        ) as discover, mock.patch.object(
            runner, 'save_catalog'
        ), redirect_stdout(io.StringIO()) as output:
            result = runner.run_provider('ollama', discover_only=True)

        self.assertEqual(result, [])
        discover.assert_called_once_with('ollama')
        self.assertNotIn('SKIPPED', output.getvalue())

    def test_official_ollama_cloud_without_key_is_skipped(self):
        with mock.patch.object(
            runner, 'config', return_value=('https://ollama.com', '')
        ), mock.patch.object(
            runner, 'discover'
        ) as discover, redirect_stdout(io.StringIO()) as output:
            result = runner.run_provider('ollama', discover_only=True)

        self.assertEqual(result, [])
        discover.assert_not_called()
        self.assertIn('SKIPPED', output.getvalue())


if __name__ == '__main__':
    unittest.main()
