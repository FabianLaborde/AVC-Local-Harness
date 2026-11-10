"""Safety tests for read-only mode, token parsing and Aider env configuration."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from usage_metrics import record, record_aider_report, summary


class AnalysisSafetyTests(unittest.TestCase):
    def test_aider_report_separate_from_direct_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / 'stats.jsonl'
            record(log,'a','coder',{'prompt_eval_count':12,'eval_count':5},.4,'direct_ollama')
            self.assertTrue(record_aider_report(log,'a','coder','Tokens: 2.8k sent, 53 received.'))
            totals=summary(log,'a')
            self.assertEqual((totals['input_tokens'],totals['output_tokens']),(12,5))
            self.assertEqual((totals['aider_reported_input_tokens'],totals['aider_reported_output_tokens']),(2800,53))
            self.assertTrue(totals['aider_reported_approximate'])
            self.assertEqual(totals['requests'],1)

    def test_does_not_parse_noisy_token_phrases(self):
        with tempfile.TemporaryDirectory() as temp:
            log=Path(temp)/'stats.jsonl'
            self.assertFalse(record_aider_report(log,'a','m','No tokens were sent.'))
            self.assertFalse(log.exists())

    def test_analysis_context_avoids_secrets(self):
        try:
            import server
        except ImportError:
            self.skipTest('Flask no está instalado')
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            (p/'MainWindow.xaml').write_text('<Window />',encoding='utf8')
            (p/'.env').write_text('SECRET',encoding='utf8')
            (p/'password.txt').write_text('PRIVATE',encoding='utf8')
            (p/'bin').mkdir()
            (p/'bin'/'cached.cs').write_text('BIN_PRIVATE',encoding='utf8')
            context=server.analysis_context(p)
            self.assertIn('MainWindow.xaml',context)
            self.assertNotIn('SECRET',context)
            self.assertNotIn('PRIVATE',context)
            self.assertNotIn('BIN_PRIVATE',context)
            self.assertTrue(server.READ_ONLY_RE.search('Analizá el proyecto. No modifiques ningún archivo.'))
            self.assertFalse(server.READ_ONLY_RE.search('Crea una ventana WPF y compila.'))

if __name__=='__main__':
    unittest.main()
