"""Execution, cancellation and editing-protocol regressions without a real model."""
import subprocess
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


class AiderExecutionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(server, 'audit'))
        self.log = self.enterContext(patch.object(server, 'log'))
        server.CANCEL.clear()
        self.addCleanup(server.CANCEL.clear)

    def test_prompt_allows_required_edit_protocol(self):
        self.assertIn('formato que exige Aider', server.SYSTEM_PROMPT)
        self.assertNotIn('NO escribas', server.SYSTEM_PROMPT)
        self.assertNotIn('no generes ejemplos ni bloques', server.SYSTEM_PROMPT)

    def test_broken_git_uses_no_git_and_explicit_sources_even_without_build_mode(self):
        for name in ('MainWindow.xaml', 'App.xaml', 'MainWindow.xaml.cs'):
            (self.root / name).write_text('source', encoding='utf8')
        with patch.object(server.subprocess, 'run', return_value=Mock(returncode=128, stderr='dubious ownership')):
            cmd = server.aider_command(self.root, 'coder', 'Cambiar distribución')
        self.assertIn('--no-git', cmd)
        self.assertEqual(cmd.count('--file'), 3)
        self.assertIn('MainWindow.xaml.cs', cmd)
        self.assertNotIn('App.xaml.cs', cmd)
        self.assertIn('Cambiar distribución', cmd[-1])

    def test_healthy_git_remains_enabled(self):
        with patch.object(server.subprocess, 'run', return_value=Mock(returncode=0, stderr='',stdout=str(self.root))):
            cmd = server.aider_command(self.root, 'coder', 'Editar')
        self.assertNotIn('--no-git', cmd)

    def test_parent_repository_is_not_inherited_by_aider(self):
        with patch.object(server.subprocess,'run',return_value=Mock(returncode=0,stderr='',stdout=str(self.root.parent))):
            cmd=server.aider_command(self.root,'coder','Editar')
        self.assertIn('--no-git',cmd)

    def test_unresolved_repository_root_disables_git(self):
        with patch.object(server.subprocess,'run',side_effect=[Mock(returncode=0,stderr=''),Mock(returncode=128,stderr='error',stdout='')]):
            cmd=server.aider_command(self.root,'coder','Editar')
        self.assertIn('--no-git',cmd)

    def test_generation_budget_is_passed_to_main_and_fallback_formats(self):
        for edit_format in ('whole', 'diff'):
            with self.subTest(edit_format=edit_format), \
                 patch.object(server.subprocess, 'run', return_value=Mock(returncode=0, stderr='',stdout=str(self.root))):
                cmd = server.aider_command(self.root, 'coder', 'Editar', edit_format)
                path = Path(cmd[cmd.index('--model-settings-file') + 1])
                settings = json.loads(path.read_text(encoding='utf8'))
                self.assertEqual(settings, [{'name': 'aider/extra_params', 'extra_params': {'max_tokens': 4096}}])
                self.assertEqual(cmd[cmd.index('--timeout') + 1], '180')
                self.assertEqual(cmd[cmd.index('--edit-format') + 1], edit_format)

    def test_real_process_output_and_exit_status(self):
        code, output = server.run_process([sys.executable, '-c', 'print("salida real");exit(3)'], self.root, timeout=5)
        self.assertEqual(code, 3)
        self.assertIn('salida real', output)
        self.assertIsNone(server.PROCESS)

    def test_real_timeout_ends_selected_process(self):
        code, output = server.run_process([sys.executable, '-c', 'import time;time.sleep(20)'], self.root, timeout=.1)
        self.assertEqual(code, -2)
        self.assertIn('Tiempo de espera agotado', output)
        self.assertIsNone(server.PROCESS)

    def test_cancelled_request_does_not_start_command(self):
        server.CANCEL.set()
        with patch.object(server.subprocess, 'Popen') as start:
            code, _ = server.run_process(['unused'], self.root)
        self.assertEqual(code, -1)
        start.assert_not_called()

    def test_wait_notice_does_not_claim_model_progress(self):
        proc = Mock(returncode=0)
        proc.communicate.side_effect = [subprocess.TimeoutExpired('cmd', 1), ('done', None)]
        with patch.object(server.subprocess, 'Popen', return_value=proc), \
             patch.object(server.time, 'monotonic', side_effect=[0, 0, 31, 31, 31, 32]):
            code, _ = server.run_process(['unused'], self.root)
        self.assertEqual(code, 0)
        self.assertIn('no confirma avance del modelo', self.log.call_args.args[0])

    def test_timeout_does_not_wait_forever_on_inherited_pipe(self):
        proc = Mock()
        proc.communicate.side_effect = subprocess.TimeoutExpired('cmd', 5, output=b'partial')
        with patch.object(server.subprocess, 'Popen', return_value=proc), \
             patch.object(server, 'terminate_process_tree') as stop, \
             patch.object(server.time, 'monotonic', side_effect=[0, 11]):
            code, output = server.run_process(['unused'], self.root, timeout=10)
        stop.assert_called_once_with(proc)
        self.assertEqual(code, -2)
        self.assertIn('partial', output)
        self.assertEqual(proc.communicate.call_args.kwargs['timeout'], 5)

    def test_failed_backup_prevents_aider_execution(self):
        with patch.object(server, 'save_working_checkpoint', return_value=False), \
             patch.object(server, 'aider_command') as command, \
             patch.object(server, 'record_job'), patch.object(server, 'LOCK', Mock()), \
             patch.object(server, 'checkpoint_path', return_value=self.root / 'checkpoint.zip'), \
             patch.object(server, 'best_checkpoint_path', return_value=self.root / 'best.zip'), \
             patch.dict(server.STATE, started=None, result=None):
            server.run_job(self.root, 'Editar', '', 1, 'coder', tech_mode='manual')
            command.assert_not_called()
            self.assertIn('respaldo previo', server.STATE['result'])

    def test_both_aider_formats_have_a_total_process_deadline(self):
        with patch.object(server, 'save_working_checkpoint', return_value=True), \
             patch.object(server, 'aider_command', return_value=['aider', '--edit-format', 'whole', '--message', 'edit']), \
             patch.object(server, 'run_process', return_value=(0, 'No edits')) as run, \
             patch.object(server, 'record_aider_report'), patch.object(server, 'record_job'), \
             patch.object(server, 'LOCK', Mock()), \
             patch.object(server, 'checkpoint_path', return_value=self.root / 'checkpoint.zip'), \
             patch.object(server, 'best_checkpoint_path', return_value=self.root / 'best.zip'), \
             patch.dict(server.STATE, started=None, result=None):
            server.run_job(self.root, 'Corregí la suma', '', 1, 'coder', tech_mode='manual')
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(call.kwargs['timeout'] == 240 for call in run.call_args_list))
        self.assertEqual(run.call_args_list[1].args[0][2], 'diff')


if __name__ == '__main__':
    unittest.main()
