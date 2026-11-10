import unittest

from candidate_lineage import (CandidateLineage, changes_from_baseline,
                               complete_snapshot, compose_revision)


class CandidateLineageTests(unittest.TestCase):
    def setUp(self):
        self.baseline = {
            'app.txt': b'value=0\n',
            'readonly.txt': b'keep\n',
        }

    def test_revision_edits_current_candidate_but_remains_delta_from_baseline(self):
        first = {'app.txt': b'value=10\nhelper=true\n'}
        current = complete_snapshot(self.baseline, first)
        step = {'app.txt': current['app.txt'].replace(b'value=10', b'value=20')}
        final = compose_revision(self.baseline, first, step, ['app.txt'])
        self.assertEqual(final['app.txt'], b'value=20\nhelper=true\n')
        self.assertEqual(self.baseline['app.txt'], b'value=0\n')

    def test_lineage_records_parent_hash_and_rejects_repetition_and_budget(self):
        lineage = CandidateLineage(self.baseline, ['app.txt'], max_candidates=2)
        first = lineage.record({'app.txt': b'value=10\n'}, 'initial')
        second = lineage.record({'app.txt': b'value=20\n'}, 'revision')
        self.assertEqual(second.parent_hash, first.content_hash)
        self.assertEqual(lineage.current_changes['app.txt'], b'value=20\n')
        with self.assertRaisesRegex(ValueError, 'límite'):
            lineage.record({'app.txt': b'value=30\n'}, 'too many')

        repeated = CandidateLineage(self.baseline, ['app.txt'], max_candidates=3)
        repeated.record({'app.txt': b'value=10\n'}, 'first')
        repeated.record({'app.txt': b'value=20\n'}, 'second')
        with self.assertRaisesRegex(ValueError, 'estado anterior'):
            repeated.record({'app.txt': b'value=10\n'}, 'cycle')

    def test_readonly_changes_and_noops_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'solo lectura'):
            changes_from_baseline(
                self.baseline,
                {'readonly.txt': b'changed\n'},
                ['app.txt'],
            )
        with self.assertRaisesRegex(ValueError, 'repite exactamente'):
            compose_revision(
                self.baseline,
                {'app.txt': b'value=10\n'},
                {'app.txt': b'value=10\n'},
                ['app.txt'],
            )


if __name__ == '__main__':
    unittest.main()
