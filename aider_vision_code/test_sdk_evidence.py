import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sdk_evidence import (discover_reference_sets, lookup, request_evidence,
                          _normalize_query, _parse_query_content, _validate_query)


XML = '''<doc><members>
<member name="T:System.Drawing.Drawing2D.GraphicsPath"><summary>Represents connected lines and curves.</summary></member>
<member name="M:System.Drawing.Drawing2D.GraphicsPath.AddArc(System.Single,System.Single,System.Single,System.Single,System.Single,System.Single)"><summary>Adds an arc.</summary></member>
</members></doc>'''


class SdkEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)

    def reference(self, version='10.0.12', tfm='net10.0'):
        folder = self.root/'dotnet'/'packs'/'Microsoft.WindowsDesktop.App.Ref'/version/'ref'/tfm
        folder.mkdir(parents=True, exist_ok=True)
        path = folder/'System.Drawing.Common.xml'
        path.write_text(XML, encoding='utf8')
        return folder, path

    def test_discovers_latest_installed_pack_without_fixed_tfm(self):
        self.reference('9.0.4', 'net9.0')
        latest, _ = self.reference('10.0.12', 'net10.0')
        found = discover_reference_sets(self.root)
        self.assertEqual(found[0]['version'], '10.0.12')
        self.assertEqual(found[0]['tfm'], 'net10.0')
        self.assertEqual(Path(found[0]['path']), latest.resolve())

    def test_lookup_returns_exact_symbols_with_hashed_local_provenance(self):
        folder, path = self.reference()
        references = [dict(pack='Microsoft.WindowsDesktop.App.Ref', version='10.0.12',
                           tfm='net10.0', path=str(folder))]
        result = lookup(references, {'types': ['System.Drawing.Drawing2D.GraphicsPath'],
                                     'members': ['System.Drawing.Drawing2D.GraphicsPath.AddArc']})
        self.assertEqual(result['status'], 'available')
        self.assertEqual({item['symbol'].split('(')[0] for item in result['entries']},
                         {'T:System.Drawing.Drawing2D.GraphicsPath',
                          'M:System.Drawing.Drawing2D.GraphicsPath.AddArc'})
        self.assertEqual(result['sources'][0]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(result['not_found'], [])

    def test_lookup_resolves_member_owner_from_queried_type_without_fuzzy_search(self):
        folder, _ = self.reference()
        references = [dict(pack='Microsoft.WindowsDesktop.App.Ref', version='10.0.12',
                           tfm='net10.0', path=str(folder))]
        result = lookup(references, {'types': ['System.Drawing.Drawing2D.GraphicsPath'],
                                     'members': ['GraphicsPath.AddArc']})
        self.assertEqual(result['not_found'], [])
        self.assertEqual(result['resolved_members'], [{
            'query': 'GraphicsPath.AddArc',
            'symbol': 'System.Drawing.Drawing2D.GraphicsPath.AddArc'}])
        self.assertEqual(result['entries'][1]['symbol'].split('(')[0],
                         'M:System.Drawing.Drawing2D.GraphicsPath.AddArc')

    def test_invalid_or_overbroad_query_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'sobran claves: instructions'):
            lookup([], {'types': [], 'members': [], 'instructions': ['do it']})
        with self.assertRaisesRegex(ValueError, r'types\[0\].*símbolo SDK válido'):
            lookup([], {'types': ['bad-name'], 'members': []})

    def test_normalizes_only_equivalent_sdk_notation(self):
        query, changes = _normalize_query({
            'types': ' T:System.Drawing.Drawing2D.GraphicsPath ',
            'members': ['M:System.Drawing.Drawing2D.GraphicsPath.AddArc(System.Single)'],
            'explanation': 'ignored'})
        self.assertEqual(query, {
            'types': ['System.Drawing.Drawing2D.GraphicsPath'],
            'members': ['System.Drawing.Drawing2D.GraphicsPath.AddArc']})
        self.assertIn('ignored_keys:explanation', changes)
        self.assertEqual(_validate_query(query), (
            ['System.Drawing.Drawing2D.GraphicsPath'],
            ['System.Drawing.Drawing2D.GraphicsPath.AddArc']))

    def test_normalizer_does_not_invent_missing_fields_or_bad_symbols(self):
        missing, _ = _normalize_query({'types': ['System.String']})
        with self.assertRaisesRegex(ValueError, 'faltan claves: members'):
            _validate_query(missing)
        invalid, _ = _normalize_query({'types': ['bad-name'], 'members': []})
        with self.assertRaisesRegex(ValueError, r'types\[0\].*símbolo SDK válido'):
            _validate_query(invalid)

    def test_parser_accepts_only_a_complete_json_fence(self):
        query, changes = _parse_query_content(
            '```json\n{"types": [], "members": []}\n```')
        self.assertEqual(query, {'types': [], 'members': []})
        self.assertEqual(changes, ['json_fence'])
        with self.assertRaises(json.JSONDecodeError):
            _parse_query_content('texto {"types": [], "members": []}')

    def test_non_dotnet_sources_do_not_call_model(self):
        api = SimpleNamespace(CANCEL=threading.Event(), post_ollama=Mock())
        self.assertIsNone(request_evidence(api, 'Cambiar', {'app.py': b'print(1)'}, 'model'))
        api.post_ollama.assert_not_called()

    def test_request_uses_strict_schema_then_verifies_installed_xml(self):
        folder, _ = self.reference()
        references = [dict(pack='Microsoft.WindowsDesktop.App.Ref', version='10.0.12',
                           tfm='net10.0', path=str(folder))]
        reply = {'types': ['System.Drawing.Drawing2D.GraphicsPath'],
                 'members': ['System.Drawing.Drawing2D.GraphicsPath.AddArc']}
        api = SimpleNamespace(CANCEL=threading.Event(), post_ollama=Mock(return_value={
            'done_reason': 'stop', 'message': {'content': json.dumps(reply)}}))
        with patch('sdk_evidence.discover_reference_sets', return_value=references):
            evidence, response = request_evidence(
                api, 'Redondea el boton', {'LoginForm.cs': b'Button signIn;'}, 'model')
        payload = api.post_ollama.call_args.args[1]
        self.assertIsInstance(payload['format'], dict)
        self.assertFalse(payload['format']['additionalProperties'])
        self.assertEqual(evidence['query'], reply)
        self.assertEqual(evidence['status'], 'available')
        self.assertEqual(response['done_reason'], 'stop')
        self.assertEqual(evidence['query_attempts'], 1)
        self.assertEqual(evidence['query_normalizations'], [])
        self.assertEqual(evidence['query_rejections'], [])

    def test_request_recovers_unambiguous_schema_deviations_without_second_call(self):
        folder, _ = self.reference()
        references = [dict(pack='Microsoft.WindowsDesktop.App.Ref', version='10.0.12',
                           tfm='net10.0', path=str(folder))]
        reply = {'types': 'T:System.Drawing.Drawing2D.GraphicsPath',
                 'members': ['M:System.Drawing.Drawing2D.GraphicsPath.AddArc(System.Single)'],
                 'note': 'not part of the query'}
        api = SimpleNamespace(CANCEL=threading.Event(), audit=Mock(),
            post_ollama=Mock(return_value={
                'done_reason': 'stop', 'message': {'content': json.dumps(reply)}}))
        with patch('sdk_evidence.discover_reference_sets', return_value=references):
            evidence, _ = request_evidence(
                api, 'Transforma la forma', {'View.cs': b'class View {}'}, 'model')
        self.assertEqual(api.post_ollama.call_count, 1)
        self.assertEqual(evidence['query_attempts'], 1)
        self.assertTrue(evidence['query_normalizations'])
        normalized = [call for call in api.audit.call_args_list
                      if call.args[0] == 'sdk_query_normalized']
        self.assertEqual(len(normalized), 1)
        self.assertIn('content_sha256', normalized[0].kwargs['response'])

    def test_invalid_query_gets_one_bounded_correction(self):
        folder, _ = self.reference()
        references = [dict(pack='Microsoft.WindowsDesktop.App.Ref', version='10.0.12',
                           tfm='net10.0', path=str(folder))]
        invalid = {'types': ['bad-name'], 'members': []}
        valid = {'types': ['System.Drawing.Drawing2D.GraphicsPath'],
                 'members': ['System.Drawing.Drawing2D.GraphicsPath.AddArc']}
        api = SimpleNamespace(CANCEL=threading.Event(), audit=Mock(),
            post_ollama=Mock(side_effect=[
                {'done_reason': 'stop', 'message': {'content': json.dumps(invalid)}},
                {'done_reason': 'stop', 'message': {'content': json.dumps(valid)}}]))
        with patch('sdk_evidence.discover_reference_sets', return_value=references):
            evidence, _ = request_evidence(
                api, 'Transforma la forma', {'View.cs': b'class View {}'}, 'model')
        self.assertEqual(api.post_ollama.call_count, 2)
        self.assertEqual(evidence['query_attempts'], 2)
        self.assertEqual(len(evidence['query_rejections']), 1)
        retry = api.post_ollama.call_args.args[1]['messages'][-1]['content']
        self.assertIn('deterministic validator', retry)

    def test_two_invalid_queries_fail_closed(self):
        self.reference()
        invalid = {'types': ['bad-name'], 'members': []}
        api = SimpleNamespace(CANCEL=threading.Event(), audit=Mock(),
            post_ollama=Mock(return_value={
                'done_reason': 'stop', 'message': {'content': json.dumps(invalid)}}))
        with self.assertRaisesRegex(ValueError, 'después de una corrección'):
            request_evidence(api, 'Transforma', {'View.cs': b'class View {}'}, 'model')
        self.assertEqual(api.post_ollama.call_count, 2)
        rejected = [call for call in api.audit.call_args_list
                    if call.args[0] == 'sdk_query_rejected']
        self.assertEqual(len(rejected), 2)
        self.assertIn('types[0]', rejected[0].kwargs['error'])
        rejected_value = rejected[0].kwargs['response']['fields']['types'][0]
        self.assertEqual(rejected_value['type'], 'invalid_symbol')
        self.assertEqual(rejected_value['chars'], len('bad-name'))


if __name__ == '__main__':
    unittest.main()
