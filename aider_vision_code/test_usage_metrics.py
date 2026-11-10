import json
import tempfile
import unittest
from pathlib import Path
from usage_metrics import record, record_job, summary


class UsageTests(unittest.TestCase):
    def test_project_isolation_and_counts(self):
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / 'usage.jsonl'
            record(file, 'C:/Project A', 'vision', {'prompt_eval_count': 125, 'eval_count': 45, 'total_duration': 100}, 1.5, 'direct_ollama')
            record(file, 'C:/Project A', 'code', {'prompt_eval_count': 75, 'eval_count': 35}, 2, 'direct_ollama')
            record(file, 'C:/Project B', 'vision', {'prompt_eval_count': 999, 'eval_count': 999}, 1, 'direct_ollama')
            record_job(file, 'C:/Project A', 300, 'done')
            report = summary(file, 'C:/Project A')
            self.assertEqual((report['input_tokens'], report['output_tokens']), (200, 80))
            self.assertEqual(report['jobs'], 1)
            self.assertEqual(report['job_seconds'], 300)
            self.assertFalse(report['aider_tokens_measured'])
    def test_no_token_guessing(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root) / 'usage.jsonl'
            record(path, 'Project', 'test', {'message': {'content':'foo'}}, .3, 'direct_ollama')
            report=summary(path, 'Project')
            self.assertEqual(report['measured_requests'],0)
            self.assertEqual(report['requests'],1)
            self.assertEqual(report['input_tokens'],0)

if __name__ == '__main__': unittest.main()
