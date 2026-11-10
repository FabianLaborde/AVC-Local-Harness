import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from source_editor import (propose, apply_anchors, staged_propose, repair_proposal,
                           visual_repair_proposal, _bounded_implementation_plan,
                           _validate_visual_repair_progress, _visual_repair_plan,
                           _validate_visual_plan_conformance,
                           _validate_visual_plan_grounding,
                           _csharp_member_anchor_candidates,
                           visual_compile_repair_proposal,
                           _minimum_visual_progress_factor,
                           _has_asymmetric_spatial_coverage,
                           validate_no_new_csharp_imports, validate_request_scope,
                           validate_sdk_type_qualification, qualify_sdk_types,
                           validate_unverified_sdk_types,
                           validate_new_private_helpers_reachable,
                           VisualScopeViolation, remove_new_unauthorized_visual_assignments)


class SourceEditorTests(unittest.TestCase):
    def setUp(self):
        self.api = SimpleNamespace(CANCEL=threading.Event(), post_ollama=Mock())
        self.original = {'app.py': b'def label():\n    return "Enviar"\n', 'theme.py': b'COLOR="white"\n'}
        sdk_patch = patch('source_editor.request_sdk_evidence', return_value=None)
        self.sdk = sdk_patch.start()
        self.addCleanup(sdk_patch.stop)
        self.reasoner = self.enterContext(patch(
            'source_editor._visual_repair_plan', return_value=(
                {'why_previous_failed':'Insignificante',
                 'strategy':['Aumentar el efecto pedido'],
                 'parameter_changes':[{'target':'Region',
                     'rejected_expression':'AddArc(..., 20, 20, ...)',
                     'proposed_expression':'20',
                     'effective_rejected':10, 'effective_proposed':20,
                     'unit':'px de radio', 'direction':'increase', 'reason':'visible'}],
                 'constraints':['Conservar el resto']},
                {'response':'plan'})))

    def response(self, edits, reason=None):
        self.api.post_ollama.return_value = dict(message=dict(content=json.dumps(dict(edits=edits))), done_reason=reason)

    def edit(self, **changes):
        return dict(file='app.py', search='return "Enviar"', replace='return "Guardar"', **changes)

    def test_scoped_patch_preserves_other_sources_and_orders_request_last(self):
        self.response([self.edit()])
        changed, _ = propose(self.api, 'Cambiar etiqueta', self.original, ['app.py'], 'local')
        self.assertEqual(set(changed), {'app.py'})
        self.assertIn(b'Guardar', changed['app.py'])
        payload = self.api.post_ollama.call_args.args[1]
        self.assertIn('Cambiar etiqueta', payload['messages'][-1]['content'])
        self.assertIn('SOLO LECTURA', payload['messages'][1]['content'])
        self.assertEqual(payload['format']['properties']['edits']['items']['properties']['file']['enum'], ['app.py'])

    def test_readonly_path_rejected(self):
        self.response([dict(file='theme.py', search='white', replace='blue')])
        with self.assertRaisesRegex(ValueError, 'fuera del alcance'):
            propose(self.api, 'Change', self.original, ['app.py'], 'local')

    def test_missing_search_rejected(self):
        self.response([dict(file='app.py', search='missing', replace='new')])
        with self.assertRaisesRegex(ValueError, 'no existe'):
            propose(self.api, 'Change', self.original, ['app.py'], 'local')

    def test_truncated_response_never_applied(self):
        self.response([self.edit()], 'length')
        with self.assertRaisesRegex(ValueError, 'cortada'):
            propose(self.api, 'Change', self.original, ['app.py'], 'local')

    def test_noop_rejected(self):
        self.response([dict(file='app.py', search='Enviar', replace='Enviar')])
        with self.assertRaisesRegex(ValueError, 'no produjo cambios'):
            propose(self.api, 'Change', self.original, ['app.py'], 'local')

    def test_cancel_before_request(self):
        self.api.CANCEL.set()
        with self.assertRaisesRegex(ValueError, 'cancelada'):
            propose(self.api, 'Change', self.original, ['app.py'], 'local')
        self.api.post_ollama.assert_not_called()

    def test_preserves_bom_and_crlf(self):
        self.original['app.py'] = b'\xef\xbb\xbfdef label():\r\n    return "Enviar"\r\n'
        self.response([self.edit()])
        changed, _ = propose(self.api, 'Change', self.original, ['app.py'], 'local')
        self.assertEqual(changed['app.py'], self.original['app.py'].replace(b'Enviar', b'Guardar'))

    def test_insert_keeps_anchor_and_unrelated_lines(self):
        edits = [dict(file='app.py', operation='insert_before', anchor='def label():',
                      content='def helper():\n    return 1\n')]
        changed = apply_anchors(self.original, edits, ['app.py'])
        self.assertTrue(changed['app.py'].endswith(self.original['app.py']))

    def test_insertions_at_same_boundary_preserve_response_order_and_closing_tag(self):
        original = {'view.xaml': b'<Grid>\r\n</Grid>\r\n'}
        edits = [dict(file='view.xaml', operation='insert_after', anchor='<Grid>', content=text)
                 for text in ('<Border/>', '<Button/>')]
        changed = apply_anchors(original, edits, ['view.xaml'])
        self.assertEqual(changed['view.xaml'], b'<Grid>\r\n<Border/>\r\n<Button/>\r\n</Grid>\r\n')

    def test_conflicting_replacements_still_rejected(self):
        edits = [dict(file='app.py', operation='replace', anchor='def label():', content=text)
                 for text in ('def a():', 'def b():')]
        with self.assertRaisesRegex(ValueError, 'superpuestos'):
            apply_anchors(self.original, edits, ['app.py'])

    def test_csharp_insert_cannot_split_type_declaration_from_opening_brace(self):
        original = {'Form.cs': b'public sealed class Form\n{\n    void Build() {}\n}\n'}
        edit = [dict(file='Form.cs', operation='insert_after', anchor='public sealed class Form',
                     content='    void Helper() {}')]
        with self.assertRaisesRegex(ValueError, 'declaración de tipo'):
            apply_anchors(original, edit, ['Form.cs'])

    def test_csharp_insert_cannot_split_method_signature_from_opening_brace(self):
        original = {'Form.cs': (b'class Form\n{\n    private void ExitOnClick(object sender)\n'
                                b'    {\n    }\n}\n')}
        edit = [dict(file='Form.cs', operation='insert_after',
                     anchor='    private void ExitOnClick(object sender)',
                     content='    private void Round()\n    {\n    }')]
        with self.assertRaisesRegex(ValueError, 'firma C#'):
            apply_anchors(original, edit, ['Form.cs'])

    def test_csharp_insert_cannot_split_constructor_signature_from_opening_brace(self):
        original = {'Form.cs': b'class Form\n{\n    public Form(int value)\n    {\n    }\n}\n'}
        edit = [dict(file='Form.cs', operation='insert_after', anchor='    public Form(int value)',
                     content='    private void Round()\n    {\n    }')]
        with self.assertRaisesRegex(ValueError, 'firma C#'):
            apply_anchors(original, edit, ['Form.cs'])

    def test_csharp_insert_cannot_use_variable_before_its_declaration(self):
        original = {'Form.cs': b'class Form\n{\n    void Build()\n    {\n        var button = Create();\n    }\n}\n'}
        edit = [dict(file='Form.cs', operation='insert_before', anchor='        var button = Create();',
                     content='        button.Region = region;')]
        with self.assertRaisesRegex(ValueError, 'antes de su declaración'):
            apply_anchors(original, edit, ['Form.cs'])

    def test_csharp_method_declaration_cannot_be_inserted_inside_constructor(self):
        original = {'Form.cs': (b'class Form\n{\n    Form()\n    {\n'
                                b'        var button = Create();\n    }\n\n    void Existing() {}\n}\n')}
        edit = [dict(file='Form.cs', operation='insert_before', anchor='        var button = Create();',
                     content='    private void Round()\n    {\n    }')]
        with self.assertRaisesRegex(ValueError, 'directamente dentro de la clase'):
            apply_anchors(original, edit, ['Form.cs'])

    def test_csharp_method_declaration_is_allowed_at_class_boundary(self):
        original = {'Form.cs': (b'class Form\n{\n    Form()\n    {\n'
                                b'        var text = "}"; // { ignored\n'
                                b'        var template = @$"{{ fake } }}";\n'
                                b'    }\n\n    void Existing() {}\n}\n')}
        edit = [dict(file='Form.cs', operation='insert_before', anchor='    void Existing() {}',
                     content='    private void Round()\n    {\n    }')]
        changed = apply_anchors(original, edit, ['Form.cs'])
        self.assertIn(b'private void Round()', changed['Form.cs'])
        self.assertLess(changed['Form.cs'].index(b'private void Round'),
                        changed['Form.cs'].index(b'void Existing'))

    def test_csharp_executable_call_is_rejected_at_class_boundary(self):
        original = {'Form.cs': (b'class Form\n{\n    public Form()\n    {\n'
                                b'    }\n\n    void Existing() {}\n}\n')}
        edit = [dict(file='Form.cs', operation='insert_before',
                     anchor='    public Form()', content='    InitializeStyles();')]
        with self.assertRaisesRegex(ValueError, 'ejecutable.*directamente en la clase'):
            apply_anchors(original, edit, ['Form.cs'])

    def test_csharp_executable_call_is_allowed_inside_method(self):
        original = {'Form.cs': (b'class Form\n{\n    public Form()\n    {\n'
                                b'        var button = Create();\n    }\n}\n')}
        edit = [dict(file='Form.cs', operation='insert_after',
                     anchor='        var button = Create();',
                     content='        InitializeStyles();')]
        changed = apply_anchors(original, edit, ['Form.cs'])
        self.assertIn(b'var button = Create();\n        InitializeStyles();', changed['Form.cs'])

    def test_csharp_member_anchors_are_exact_class_scope_lines(self):
        source = ('public class Form\n{\n    public Form()\n    {\n'
                  '        var button = Create();\n    }\n\n'
                  '    protected override void Dispose(bool disposing)\n    {\n    }\n\n'
                  '    private void Build()\n    {\n    }\n}\n')
        anchors = _csharp_member_anchor_candidates(source)
        self.assertIn('public Form()', anchors)
        self.assertIn('protected override void Dispose(bool disposing)', anchors)
        self.assertIn('private void Build()', anchors)
        self.assertNotIn('var button = Create();', anchors)

    def test_rejected_member_in_method_reports_valid_class_scope_anchors(self):
        source = ('public class Form\n{\n    public Form()\n    {\n'
                  '        var button = Create();\n    }\n\n'
                  '    private void Build()\n    {\n    }\n}\n')
        edit = dict(file='Form.cs', operation='insert_before',
                    anchor='        var button = Create();',
                    content='    private void Helper()\n    {\n    }')
        with self.assertRaisesRegex(ValueError, 'Anclas válidas.*private void Build'):
            apply_anchors({'Form.cs': source.encode()}, [edit], ['Form.cs'])

    def test_nonexistent_ambiguous_and_readonly_anchors_rejected(self):
        for name, anchor in [('theme.py', 'COLOR="white"'), ('app.py', 'missing')]:
            with self.assertRaises(ValueError):
                apply_anchors(self.original, [dict(file=name, operation='replace', anchor=anchor, content='pass')], ['app.py'])
        with self.assertRaisesRegex(ValueError, 'única'):
            apply_anchors({'a.py': b'pass\npass\n'}, [dict(file='a.py', operation='replace', anchor='pass', content='x=1')], ['a.py'])

    def test_stage_output_is_not_an_approval_and_only_two_calls(self):
        edit = dict(file='app.py', operation='replace', anchor='    return "Enviar"', content='    return "Guardar"')
        self.api.post_ollama.side_effect = [dict(message=dict(content='Change the literal only'), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=[edit]))), done_reason='stop')]
        changed, details = staged_propose(self.api, 'Cambiá la etiqueta', self.original, ['app.py'], 'local')
        self.assertEqual(changed['app.py'], self.original['app.py'].replace(b'Enviar', b'Guardar'))
        self.assertEqual(self.api.post_ollama.call_count, 2)
        self.assertNotIn('approved', details)

    def test_verified_sdk_evidence_reaches_plan_and_is_recorded(self):
        evidence = {'schema_version': 1, 'provider': 'dotnet_reference_xml',
                    'status': 'available', 'entries': [{'symbol': 'T:System.Windows.Forms.Button'}]}
        transport = {'message': {'content': '{"types":[],"members":[]}'}}
        self.sdk.return_value = evidence, transport
        original = {'LoginForm.cs': b'class LoginForm\n{\n    void Build() {}\n}\n'}
        edit = dict(file='LoginForm.cs', operation='insert_before', anchor='    void Build() {}',
                    content='    void Helper() {}')
        self.api.post_ollama.side_effect = [
            dict(message=dict(content='Use documented Button API'), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=[edit]))), done_reason='stop')]
        _, details = staged_propose(self.api, 'Agrega un auxiliar', original, ['LoginForm.cs'], 'local')
        integration_prompt = self.api.post_ollama.call_args_list[1].args[1]['messages'][-1]['content']
        self.assertIn('System.Windows.Forms.Button', integration_prompt)
        integration_context = '\n'.join(
            item['content'] for item in
            self.api.post_ollama.call_args_list[1].args[1]['messages'])
        self.assertIn('DETERMINISTIC C# CLASS-SCOPE ANCHORS', integration_context)
        self.assertIn('insert_before: void Build() {}', integration_context)
        self.assertEqual(details['sdk_evidence'], evidence)
        self.assertEqual(len(details['responses']), 3)

    def test_sdk_lookup_failure_is_explicit_and_editing_continues_without_grounding_claim(self):
        self.sdk.side_effect = ValueError('documentacion dañada')
        original = {'LoginForm.cs': b'class LoginForm\n{\n    void Build() {}\n}\n'}
        edit = dict(file='LoginForm.cs', operation='insert_before', anchor='    void Build() {}',
                    content='    void Helper() {}')
        self.api.post_ollama.side_effect = [
            dict(message=dict(content='Add a helper'), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=[edit]))), done_reason='stop')]
        _, details = staged_propose(self.api, 'Agrega un auxiliar', original, ['LoginForm.cs'], 'local')
        self.assertEqual(details['sdk_evidence']['status'], 'unavailable')
        self.assertIn('documentacion dañada', details['sdk_evidence']['reason'])
        integration_prompt = self.api.post_ollama.call_args_list[1].args[1]['messages'][-1]['content']
        self.assertIn('unavailable', integration_prompt)

    def test_unavailable_sdk_blocks_new_external_types_after_one_reintegration(self):
        self.sdk.side_effect = ValueError('consulta inválida')
        original = {'Form.cs': (b'class Form\n{\n    void Build()\n    {\n'
                                b'        var button = CreateButton();\n    }\n}\n')}
        edit = dict(file='Form.cs', operation='insert_after',
                    anchor='        var button = CreateButton();',
                    content='        GraphicsPath path = new GraphicsPath();')
        self.api.post_ollama.side_effect = [
            dict(message=dict(content='Use a framework geometry type'), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=[edit]))), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=[edit]))), done_reason='stop')]
        with self.assertRaisesRegex(ValueError, 'sin evidencia SDK verificada.*GraphicsPath'):
            staged_propose(self.api, 'Cambia la forma', original, ['Form.cs'], 'local')
        self.assertEqual(self.api.post_ollama.call_count, 3)

    def test_staged_editor_exposes_reasoning_sdk_and_coding_phases(self):
        edit = dict(file='app.py', operation='replace', anchor='    return "Enviar"',
                    content='    return "Guardar"')
        self.api.post_ollama.side_effect = [
            dict(message=dict(content='Change the literal'), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=[edit]))), done_reason='stop')]
        with patch('source_editor._set_activity_phase') as phase:
            staged_propose(self.api, 'Cambia la etiqueta', self.original, ['app.py'], 'local')
        self.assertEqual([call.args[1] for call in phase.call_args_list],
                         ['reasoning', 'evidence', 'coding'])

    def test_truncated_plan_does_not_call_integration(self):
        self.api.post_ollama.return_value = dict(message=dict(content='partial'), done_reason='length')
        with self.assertRaisesRegex(ValueError, 'cortada'):
            staged_propose(self.api, 'Change', self.original, ['app.py'], 'local')
        self.assertEqual(self.api.post_ollama.call_count, 1)

    def test_unrequested_color_changes_are_rejected_but_requested_theme_allows_them(self):
        original = {'LoginForm.cs': b'var signIn = CreateButton();\nsignIn.Region = oldRegion;\n'}
        changed = {'LoginForm.cs': (b'var signIn = CreateButton();\n'
                                    b'signIn.Region = newRegion;\n'
                                    b'signIn.BackColor = Color.LightBlue;\n'
                                    b'signIn.ForeColor = Color.Black;\n')}
        with self.assertRaisesRegex(ValueError, 'no solicitadas.*backcolor'):
            validate_request_scope('Redondea las esquinas y conserva todo lo demas', original, changed)
        validate_request_scope('Cambia el tema y los colores; redondea las esquinas', original, changed)
        with self.assertRaisesRegex(ValueError, 'no solicitadas.*backcolor'):
            validate_request_scope('Redondea las esquinas. Conserva colores, dimensiones y todo lo demas.',
                                   original, changed)
        with self.assertRaisesRegex(ValueError, 'no solicitadas.*backcolor'):
            validate_request_scope(
                'En SistemaBascula_CSharp/src/App/LoginForm.cs, redondea las esquinas. Conserva colores.',
                original, changed)
        shape_only = {'LoginForm.cs': (b'var signIn = CreateButton();\n'
                                       b'signIn.Region = newRegion;\n')}
        validate_request_scope(
            'Redondea las esquinas y conserva todos los colores y dimensiones.', original, shape_only)

    def test_csharp_anchored_editor_rejects_every_new_using(self):
        original = {'LoginForm.cs': b'namespace App;\nclass LoginForm\n{\n    void Build() {}\n}\n'}
        changed = {'LoginForm.cs': b'namespace App;\nclass LoginForm\n{\nusing System.Drawing.Drawing2D;\n    void Build() {}\n}\n'}
        with self.assertRaisesRegex(ValueError, 'plenamente calificados'):
            validate_no_new_csharp_imports(original, changed)

    def test_new_verified_sdk_type_must_be_fully_qualified(self):
        evidence = {'status': 'available', 'entries': [
            {'symbol': 'T:System.Drawing.Drawing2D.GraphicsPath'}]}
        original = {'Form.cs': b'class Form\n{\n    void Existing() {}\n}\n'}
        simple = {'Form.cs': b'class Form\n{\n    GraphicsPath Build() => new GraphicsPath();\n}\n'}
        with self.assertRaisesRegex(ValueError, 'GraphicsPath.*System.Drawing.Drawing2D.GraphicsPath'):
            validate_sdk_type_qualification(original, simple, evidence)
        qualified = {'Form.cs': (b'class Form\n{\n    System.Drawing.Drawing2D.GraphicsPath Build() '
                                 b'=> new System.Drawing.Drawing2D.GraphicsPath();\n}\n')}
        validate_sdk_type_qualification(original, qualified, evidence)

    def test_verified_sdk_types_are_qualified_without_touching_literals_or_comments(self):
        evidence = {'status': 'available', 'entries': [
            {'symbol': 'T:System.Drawing.Drawing2D.GraphicsPath'}]}
        original = {'Form.cs': b'class Form\n{\n    void Existing() {}\n}\n'}
        changed = {'Form.cs': (b'class Form\n{\n    // GraphicsPath stays descriptive\n'
                               b'    string label = "GraphicsPath";\n'
                               b'    GraphicsPath Build() => new GraphicsPath();\n}\n')}
        result = qualify_sdk_types(original, changed, evidence)['Form.cs']
        self.assertIn(b'// GraphicsPath stays descriptive', result)
        self.assertIn(b'"GraphicsPath"', result)
        self.assertIn(b'System.Drawing.Drawing2D.GraphicsPath Build()', result)
        self.assertIn(b'new System.Drawing.Drawing2D.GraphicsPath()', result)
        validate_sdk_type_qualification(original, {'Form.cs': result}, evidence)

    def test_preexisting_simple_sdk_type_remains_allowed(self):
        evidence = {'status': 'available', 'entries': [
            {'symbol': 'T:System.Drawing.Drawing2D.GraphicsPath'}]}
        original = {'Form.cs': b'class Form\n{\n    GraphicsPath? _path;\n}\n'}
        changed = {'Form.cs': b'class Form\n{\n    GraphicsPath? _path;\n    GraphicsPath Build() => _path!;\n}\n'}
        self.assertEqual(qualify_sdk_types(original, changed, evidence), changed)
        validate_sdk_type_qualification(original, changed, evidence)

    def test_staged_editor_rejects_unrequested_visual_side_effect(self):
        original = {'LoginForm.cs': b'class LoginForm\n{\n    void Build() {}\n}\n'}
        edits = [dict(file='LoginForm.cs', operation='insert_before', anchor='    void Build() {}',
                      content='    void Round()\n    {\n        BackColor = Color.Blue;\n    }')]
        self.api.post_ollama.side_effect = [
            dict(message=dict(content='Add a helper'), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=edits))), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=edits))), done_reason='stop')]
        with self.assertRaisesRegex(ValueError, 'propiedades visuales no solicitadas'):
            staged_propose(self.api, 'Redondea las esquinas', original, ['LoginForm.cs'], 'local')
        self.assertEqual(self.api.post_ollama.call_count, 3)

    def test_staged_editor_deterministically_removes_new_standalone_scope_side_effects(self):
        original = {'LoginForm.cs': (b'class LoginForm\n{\n    void Build()\n    {\n'
                                     b'        var signIn = CreateButton();\n    }\n}\n')}
        edits = [dict(file='LoginForm.cs', operation='insert_after',
                      anchor='        var signIn = CreateButton();', content=(
                          '        signIn.Region = CreateRoundedRegion();\n'
                          '        signIn.FlatAppearance.BorderColor = AuthUi.Border;\n'
                          '        signIn.FlatAppearance.MouseDownBackColor = AuthUi.Blue;\n'
                          '        signIn.FlatAppearance.MouseOverBackColor = AuthUi.Blue;'))]
        self.api.post_ollama.side_effect = [
            dict(message=dict(content='Assign a rounded region'), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=edits))), done_reason='stop')]
        changed, details = staged_propose(
            self.api, 'Redondea visiblemente las esquinas. Conserva todo lo demas.',
            original, ['LoginForm.cs'], 'local')
        result = changed['LoginForm.cs']
        self.assertIn(b'signIn.Region = CreateRoundedRegion();', result)
        self.assertNotIn(b'BorderColor', result)
        self.assertNotIn(b'MouseDownBackColor', result)
        self.assertNotIn(b'MouseOverBackColor', result)
        self.assertEqual(self.api.post_ollama.call_count, 2)
        self.assertEqual(len(details['scope_cleanups']), 1)
        self.assertEqual(len(details['scope_cleanups'][0]['removed']), 3)

    def test_scope_cleanup_fails_closed_for_mixed_or_existing_property_lines(self):
        original = {'LoginForm.cs': (b'class LoginForm\n{\n    void Build()\n    {\n'
                                     b'        signIn.BackColor = Color.White;\n    }\n}\n')}
        mixed = {'LoginForm.cs': original['LoginForm.cs'].replace(
            b'        signIn.BackColor = Color.White;',
            b'        signIn.Region = rounded; signIn.BackColor = Color.Blue;')}
        with self.assertRaises(VisualScopeViolation) as caught:
            validate_request_scope('Redondea las esquinas', original, mixed)
        with self.assertRaisesRegex(VisualScopeViolation, 'mezcla|existente'):
            remove_new_unauthorized_visual_assignments(
                'Redondea las esquinas', original, mixed, caught.exception)

    def test_readonly_visual_facts_cannot_mask_requested_effect_after_cleanup(self):
        original = {
            'LoginForm.cs': b'class LoginForm\n{\n    void Build() {}\n}\n',
            'Theme.cs': b'class Theme\n{\n    void Existing() { button.Region = rounded; }\n}\n',
        }
        changed = {'LoginForm.cs': (b'class LoginForm\n{\n    void Build() {}\n'
                                    b'    button.Region = rounded;\n'
                                    b'    button.BackColor = Color.Blue;\n}\n')}
        with self.assertRaises(VisualScopeViolation) as caught:
            validate_request_scope('Redondea las esquinas', original, changed)
        repaired, removed = remove_new_unauthorized_visual_assignments(
            'Redondea las esquinas', original, changed, caught.exception)
        self.assertIn(b'button.Region = rounded;', repaired['LoginForm.cs'])
        self.assertNotIn(b'BackColor', repaired['LoginForm.cs'])
        self.assertEqual([item['property'] for item in removed], ['backcolor'])

    def test_staged_editor_reintegrates_once_after_deterministic_rejection(self):
        original = {'LoginForm.cs': (b'class LoginForm\n{\n    void Build()\n    {\n'
                                     b'        var signIn = CreateButton();\n    }\n}\n')}
        bad = [dict(file='LoginForm.cs', operation='insert_before', anchor='    void Build()',
                    content='using System.Drawing.Drawing2D;')]
        good = [dict(file='LoginForm.cs', operation='insert_after',
                     anchor='        var signIn = CreateButton();',
                     content='        signIn.Region = CreateRoundedRegion();')]
        self.api.post_ollama.side_effect = [
            dict(message=dict(content='Use the existing button'), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=bad))), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=good))), done_reason='stop')]
        changed, details = staged_propose(self.api, 'Redondea las esquinas', original,
                                          ['LoginForm.cs'], 'local')
        self.assertIn(b'signIn.Region = CreateRoundedRegion();', changed['LoginForm.cs'])
        self.assertEqual(len(details['responses']), 3)
        feedback = self.api.post_ollama.call_args.args[1]['messages'][-1]['content']
        self.assertIn('rejected before compilation', feedback)

    def test_repair_accepts_fully_qualified_type_without_import_and_preserves_color(self):
        original = {'LoginForm.cs': (b'class LoginForm\n{\n    void Round(Button button)\n    {\n'
                                     b'        button.Region = oldRegion;\n    }\n}\n')}
        edits = [dict(file='LoginForm.cs', operation='replace', anchor='        button.Region = oldRegion;',
                      content=('        using var path = new System.Drawing.Drawing2D.GraphicsPath();\n'
                               '        button.Region = new System.Drawing.Region(path);'))]
        self.api.post_ollama.return_value = dict(
            message=dict(content=json.dumps(dict(edits=edits))), done_reason='stop')
        evidence = {'status': 'available', 'entries': [
            {'symbol': 'T:System.Drawing.Drawing2D.GraphicsPath'},
            {'symbol': 'T:System.Drawing.Region'}]}
        changed, _ = repair_proposal(self.api, 'Redondea las esquinas', original,
                                     ['LoginForm.cs'], 'local', 'CS0246 GraphicsPath',
                                     sdk_evidence=evidence)
        self.assertIn(b'System.Drawing.Drawing2D.GraphicsPath', changed['LoginForm.cs'])
        self.assertNotIn(b'using System.Drawing', changed['LoginForm.cs'])

    def test_repair_rejects_import_inserted_inside_class(self):
        original = {'LoginForm.cs': b'class LoginForm\n{\n    void Build() {}\n}\n'}
        edits = [dict(file='LoginForm.cs', operation='insert_before', anchor='    void Build() {}',
                      content='using System.Drawing.Drawing2D;')]
        self.api.post_ollama.return_value = dict(
            message=dict(content=json.dumps(dict(edits=edits))), done_reason='stop')
        with self.assertRaisesRegex(ValueError, 'plenamente calificados'):
            repair_proposal(self.api, 'Redondea las esquinas', original,
                            ['LoginForm.cs'], 'local', 'CS0246 GraphicsPath')

    def test_repair_retries_post_anchor_validation_rejection(self):
        original = {'LoginForm.cs': (b'class LoginForm\n{\n    void Round()\n    {\n'
                                     b'    }\n}\n')}
        current = {'LoginForm.cs': original['LoginForm.cs'].replace(
            b'    }\n}\n', b'        GraphicsPath path = new GraphicsPath();\n    }\n}\n', 1)}
        invalid = [dict(file='LoginForm.cs', operation='insert_before',
                        anchor='    void Round()', content='using System.Drawing.Drawing2D;')]
        valid = [dict(file='LoginForm.cs', operation='replace',
                      anchor='        GraphicsPath path = new GraphicsPath();',
                      content=('        System.Drawing.Drawing2D.GraphicsPath path = '
                               'new System.Drawing.Drawing2D.GraphicsPath();'))]
        evidence = {'status': 'available', 'entries': [
            {'symbol': 'T:System.Drawing.Drawing2D.GraphicsPath'}]}
        self.api.post_ollama.side_effect = [
            dict(message=dict(content=json.dumps(dict(edits=invalid))), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=valid))), done_reason='stop')]
        changed, _ = repair_proposal(
            self.api, 'Cambia la forma', original, ['LoginForm.cs'], 'local',
            'CS0246 GraphicsPath', sdk_evidence=evidence, current_changed=current)
        self.assertIn(b'System.Drawing.Drawing2D.GraphicsPath', changed['LoginForm.cs'])
        self.assertNotIn(b'using System.Drawing.Drawing2D;', changed['LoginForm.cs'])
        self.assertEqual(self.api.post_ollama.call_count, 2)
        retry = self.api.post_ollama.call_args.args[1]['messages'][-1]['content']
        self.assertIn('plenamente calificados', retry)

    def test_repair_retries_invalid_anchor_against_current_candidate(self):
        original = {'View.cs': (b'class View\n{\n    void Build()\n    {\n'
                                b'        var item = Create();\n    }\n}\n')}
        current = {'View.cs': original['View.cs'].replace(
            b'        var item = Create();',
            b'        var item = Create();\n        item.Shape = MissingApi();')}
        invalid = [dict(file='View.cs', operation='replace',
                        anchor='        item.Shape = NotTheCurrentLine();',
                        content='        item.Shape = ValidShape();')]
        valid = [dict(file='View.cs', operation='replace',
                      anchor='        item.Shape = MissingApi();',
                      content='        item.Shape = ValidShape();')]
        self.api.post_ollama.side_effect = [
            dict(message=dict(content=json.dumps(dict(edits=invalid))), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=valid))), done_reason='stop')]
        changed, _ = repair_proposal(
            self.api, 'Cambia la forma', original, ['View.cs'], 'local',
            'CS1061 MissingApi', current_changed=current)
        self.assertIn(b'item.Shape = ValidShape();', changed['View.cs'])
        self.assertNotIn(b'MissingApi', changed['View.cs'])
        self.assertEqual(self.api.post_ollama.call_count, 2)
        retry = self.api.post_ollama.call_args.args[1]['messages'][-1]['content']
        self.assertIn('CURRENT CANDIDATE', retry)

    def test_repair_retries_change_unrelated_to_reported_location(self):
        original_text = ('class View\n{\n    void Build()\n    {\n'
                         '        var button = Create();\n'
                         '        button.BackColor = White;\n' +
                         ''.join(f'        Keep({index});\n' for index in range(8)) +
                         '        Existing();\n    }\n}\n')
        failed_text = original_text.replace(
            'button.BackColor = White;', 'button.BackColor = Blue;').replace(
            '        Existing();', '        MissingCall();')
        original = {'View.cs': original_text.encode()}
        current = {'View.cs': failed_text.encode()}
        irrelevant = [dict(file='View.cs', operation='replace',
                           anchor='        button.BackColor = Blue;',
                           content='        button.BackColor = LightBlue;')]
        relevant = [dict(file='View.cs', operation='replace',
                         anchor='        MissingCall();', content='        Existing();')]
        line = failed_text.splitlines().index('        MissingCall();') + 1
        diagnostics = f'C:\\trial\\View.cs({line},9): error CS0103: MissingCall'
        self.api.post_ollama.side_effect = [
            dict(message=dict(content=json.dumps(dict(edits=irrelevant))), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=relevant))), done_reason='stop')]
        changed, _ = repair_proposal(
            self.api, 'Cambia el fondo a azul', original, ['View.cs'], 'local',
            diagnostics, current_changed=current)
        self.assertIn(b'button.BackColor = Blue;', changed['View.cs'])
        self.assertIn(b'Existing();', changed['View.cs'])
        self.assertNotIn(b'MissingCall();', changed['View.cs'])
        self.assertEqual(self.api.post_ollama.call_count, 2)
        feedback = self.api.post_ollama.call_args.args[1]['messages'][-1]['content']
        self.assertIn('ubicación diagnosticada', feedback)

    def test_visual_repair_edits_current_candidate_and_preserves_prior_change(self):
        original = {'LoginForm.cs': (b'class LoginForm\n{\n    void Build()\n    {\n'
                                     b'        var signIn = CreateButton();\n    }\n}\n')}
        rejected = {'LoginForm.cs': original['LoginForm.cs'].replace(
            b'var signIn = CreateButton();',
            b'var signIn = CreateButton();\n        signIn.Region = TinyRegion();')}
        edits = [dict(file='LoginForm.cs', operation='replace',
                      anchor='        signIn.Region = TinyRegion();',
                      content='        signIn.Region = ClearlyRoundedRegion(20);')]
        self.api.post_ollama.return_value = dict(
            message=dict(content=json.dumps(dict(edits=edits))), done_reason='stop')
        review = dict(status='not_fulfilled', reason='Cambio insignificante',
                      metrics=dict(target_changed_ratio=.012, gate='not_fulfilled'),
                      vision=dict(status='uncertain', observed_change='No se ve redondeo'))
        changed, _ = visual_repair_proposal(
            self.api, 'Redondea las esquinas', original, ['LoginForm.cs'], 'local',
            rejected, review)
        self.assertIn(b'ClearlyRoundedRegion', changed['LoginForm.cs'])
        self.assertNotIn(b'TinyRegion', changed['LoginForm.cs'])
        messages = self.api.post_ollama.call_args.args[1]['messages']
        self.assertIn('TinyRegion', messages[1]['content'])
        self.assertIn('TinyRegion', messages[2]['content'])
        self.assertIn('0.012', messages[3]['content'])
        self.assertIn('Aumentar el efecto pedido', messages[4]['content'])

    def test_visual_repair_rejects_identical_candidate_and_wrong_status(self):
        review = dict(status='not_fulfilled', reason='No visible', metrics={}, vision={})
        edit = dict(file='app.py', operation='replace', anchor='    return "Guardar"',
                    content='    return "Guardar"')
        rejected = {'app.py': self.original['app.py'].replace(b'Enviar', b'Guardar')}
        self.api.post_ollama.side_effect = [dict(
            message=dict(content=json.dumps(dict(edits=[edit]))), done_reason='stop'), dict(
            message=dict(content=json.dumps(dict(edits=[edit]))), done_reason='stop')]
        with self.assertRaisesRegex(ValueError, 'no produjo cambios'):
            visual_repair_proposal(self.api, 'Cambia la etiqueta', self.original,
                                   ['app.py'], 'local', rejected, review)
        with self.assertRaisesRegex(ValueError, 'no habilita'):
            visual_repair_proposal(self.api, 'Cambia la etiqueta', self.original,
                                   ['app.py'], 'local', rejected,
                                   dict(status='uncertain'))

    def test_visual_repair_reintegrates_once_after_invalid_anchor(self):
        original = {'Form.cs': (b'class Form\n{\n    void Build()\n    {\n'
                                b'        var button = CreateButton();\n    }\n}\n')}
        rejected = {'Form.cs': original['Form.cs'].replace(
            b'var button = CreateButton();', b'var button = CreateButton(); // tiny')}
        bad = dict(file='Form.cs', operation='insert_after', anchor='    void Build()',
                   content='        button.Region = VisibleRegion(20);')
        good = dict(file='Form.cs', operation='insert_after',
                    anchor='        var button = CreateButton(); // tiny',
                    content='        button.Region = VisibleRegion(20);')
        self.api.post_ollama.side_effect = [
            dict(message=dict(content=json.dumps(dict(edits=[bad]))), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=[good]))), done_reason='stop')]
        changed, details = visual_repair_proposal(
            self.api, 'Redondea las esquinas', original, ['Form.cs'], 'local', rejected,
            dict(status='not_fulfilled', reason='No visible', metrics={}, vision={}))
        self.assertIn(b'VisibleRegion', changed['Form.cs'])
        self.assertEqual(len(details['responses']), 2)
        feedback = self.api.post_ollama.call_args.args[1]['messages'][-1]['content']
        self.assertIn('rejected before compilation', feedback)

    def test_reasoner_plan_cannot_expand_visual_or_behavioral_scope(self):
        plan = dict(strategy=[
            'Usar un radio mayor para redondear las esquinas.',
            'Cambiar el BackColor para reforzar el efecto.',
            'Agregar hover y MouseEnter.',
            'Aplicar el helper a las instancias existentes.'], parameter_changes=[
                {'target':'AddArc', 'rejected_expression':'diametro 10',
                 'proposed_expression':'diametro 24', 'effective_rejected':5,
                 'effective_proposed':12, 'unit':'px de radio',
                 'direction':'increase', 'reason':'redondeo visible'},
                {'target':'BackColor', 'rejected_expression':'blanco',
                 'proposed_expression':'azul', 'effective_rejected':0,
                 'effective_proposed':1, 'unit':'categoria',
                 'direction':'different', 'reason':'reforzar color'},
                {'target':'signIn', 'rejected_expression':'new Size(210, 42)',
                 'proposed_expression':'new Size(420, 42)', 'effective_rejected':210,
                 'effective_proposed':420, 'unit':'px', 'direction':'increase',
                 'reason':'duplicar el ancho del boton'}])
        bounded = _bounded_implementation_plan('Redondea las esquinas', plan)
        text = ' '.join(bounded['strategy'])
        self.assertIn('radio mayor', text)
        self.assertIn('instancias existentes', text)
        self.assertNotIn('BackColor', text)
        self.assertNotIn('hover', text)
        self.assertEqual(len(bounded['parameter_changes']), 1)
        self.assertEqual(bounded['parameter_changes'][0]['proposed_expression'], 'diametro 24')

    def test_visual_repair_safely_removes_new_scope_side_effects(self):
        original = {'Form.cs': (b'class Form\n{\n    void Build()\n    {\n'
                                b'        var button = CreateButton();\n    }\n}\n')}
        rejected = {'Form.cs': original['Form.cs'].replace(
            b'var button = CreateButton();', b'var button = CreateButton(); // no effect')}
        edits = [dict(file='Form.cs', operation='insert_after',
                      anchor='        var button = CreateButton(); // no effect',
                      content=('        button.Region = VisibleRegion(20);\n'
                               '        button.BackColor = Color.Red;'))]
        self.api.post_ollama.return_value = dict(
            message=dict(content=json.dumps(dict(edits=edits))), done_reason='stop')
        changed, details = visual_repair_proposal(
            self.api, 'Redondea las esquinas', original, ['Form.cs'], 'local', rejected,
            dict(status='not_fulfilled', reason='No visible', metrics={}, vision={}))
        self.assertIn(b'VisibleRegion(20)', changed['Form.cs'])
        self.assertNotIn(b'BackColor', changed['Form.cs'])
        self.assertEqual(details['scope_cleanups'][0]['removed'][0]['category'], 'color')

    def test_visibly_describes_perceptibility_not_visibility_scope(self):
        bounded = _bounded_implementation_plan('Redondea visiblemente las esquinas', dict(
            strategy=['Aplicar un radio de esquina claramente visible'],
            parameter_changes=[dict(
                target='radio', rejected_expression='radius=0',
                proposed_expression='radius=8', effective_rejected=0,
                effective_proposed=8, unit='px', direction='increase',
                reason='redondeo visible')]))
        self.assertEqual(len(bounded['parameter_changes']), 1)

    def test_visual_progress_rejects_equal_weaker_and_unknown_direction(self):
        base = dict(target='radio', rejected_expression='radius=10',
                    proposed_expression='radius=16', effective_rejected=10,
                    effective_proposed=16, unit='px', direction='increase',
                    reason='hacer visible el cambio')
        self.assertEqual(_validate_visual_repair_progress(
            {'parameter_changes':[base]})[0]['effective_proposed'], 16.0)
        for proposed, direction in ((10, 'increase'), (8, 'increase'),
                                    (12, 'decrease'), (12, 'unknown')):
            invalid = dict(base, effective_proposed=proposed, direction=direction)
            with self.assertRaisesRegex(ValueError, 'no mejora'):
                _validate_visual_repair_progress({'parameter_changes':[invalid]})

    def test_measured_shortfall_requires_material_numeric_progress(self):
        factor = _minimum_visual_progress_factor(dict(metrics=dict(
            gate='not_fulfilled', target_changed_ratio=.0032)))
        self.assertGreater(factor, 2.5)
        weak = dict(target='radio', rejected_expression='10', proposed_expression='15',
                    effective_rejected=10, effective_proposed=15, unit='px',
                    direction='increase', reason='visible')
        with self.assertRaisesRegex(ValueError, 'exige al menos'):
            _validate_visual_repair_progress(
                {'parameter_changes':[weak]}, minimum_factor=factor)
        strong = dict(weak, proposed_expression='28', effective_proposed=28)
        _validate_visual_repair_progress(
            {'parameter_changes':[strong]}, minimum_factor=factor)
        near_threshold = _minimum_visual_progress_factor(dict(metrics=dict(
            gate='not_fulfilled', target_changed_ratio=.012)))
        self.assertEqual(near_threshold, 2.0)
        zero_baseline = dict(weak, rejected_expression='radius=0',
                             proposed_expression='radius=8', effective_rejected=0,
                             effective_proposed=8)
        self.assertEqual(_validate_visual_repair_progress(
            {'parameter_changes':[zero_baseline]}, minimum_factor=4)[0]
            ['effective_proposed'], 8.0)

    def test_asymmetric_coverage_is_a_topology_failure(self):
        evidence = dict(contract=dict(conditions=[dict(spatial_mode='corners')]),
            metrics=dict(target_spatial_profiles=[dict(
            name='objetivo',
            corners=dict(top_left=dict(observed=True), top_right=dict(observed=False),
                         bottom_right=dict(observed=False), bottom_left=dict(observed=False)),
            sides={})]))
        self.assertTrue(_has_asymmetric_spatial_coverage(evidence))
        evidence['metrics']['target_spatial_profiles'][0]['corners']['top_right']['observed'] = True
        evidence['metrics']['target_spatial_profiles'][0]['corners']['bottom_right']['observed'] = True
        evidence['metrics']['target_spatial_profiles'][0]['corners']['bottom_left']['observed'] = True
        self.assertFalse(_has_asymmetric_spatial_coverage(evidence))

    def test_asymmetric_coverage_without_matching_contract_is_not_topology(self):
        profile = dict(name='objetivo', corners=dict(
            top_left=dict(observed=True), top_right=dict(observed=False)), sides={})
        evidence = dict(contract=dict(conditions=[dict(spatial_mode='position')]),
                        metrics=dict(target_spatial_profiles=[profile]))
        self.assertFalse(_has_asymmetric_spatial_coverage(evidence))

    def test_visual_repair_rejects_inconsistent_mocked_reasoner_plan(self):
        self.reasoner.return_value = (dict(
            why_previous_failed='El radio 10 fue apenas perceptible',
            strategy=['Aumentar el radio'], constraints=[], parameter_changes=[dict(
                target='radio', rejected_expression='radius=10',
                proposed_expression='radius=8', effective_rejected=10,
                effective_proposed=8, unit='px', direction='increase',
                reason='redondear')]), {'response':'bad plan'})
        with self.assertRaisesRegex(ValueError, 'no mejora'):
            visual_repair_proposal(
                self.api, 'Redondea las esquinas', self.original, ['app.py'], 'local',
                dict(self.original), dict(status='not_fulfilled', metrics={}, vision={}))

    def test_generated_code_must_preserve_approved_numeric_value(self):
        plan = dict(parameter_changes=[dict(
            target='radio', rejected_expression='radius=10',
            proposed_expression='radius=16', effective_rejected=10,
            effective_proposed=16, unit='px', direction='increase', reason='visible')])
        with self.assertRaisesRegex(ValueError, 'no conserva'):
            _validate_visual_plan_conformance(
                self.original, {'app.py': self.original['app.py'] + b'RADIUS=8\n'}, plan)
        _validate_visual_plan_conformance(
            self.original, {'app.py': self.original['app.py'] + b'RADIUS=16\n'}, plan)

    def test_visual_plan_baseline_must_exist_in_rejected_diff(self):
        plan = dict(parameter_changes=[dict(
            target='helper', rejected_expression='cornerRadius = 10',
            proposed_expression='cornerRadius = 20', effective_rejected=10,
            effective_proposed=20, unit='px', direction='increase', reason='visible')])
        diff = '+ RoundButtonCorners(signIn, 10);'
        with self.assertRaisesRegex(ValueError, 'no existe en el diff'):
            _validate_visual_plan_grounding(plan, diff)

    def test_visual_plan_allows_exact_diff_expression_and_zero_baseline(self):
        exact = dict(parameter_changes=[dict(
            target='signIn', rejected_expression='RoundButtonCorners(signIn, 10)',
            proposed_expression='RoundButtonCorners(signIn, 20)', effective_rejected=10,
            effective_proposed=20, unit='px', direction='increase', reason='visible')])
        _validate_visual_plan_grounding(exact, '+ RoundButtonCorners(signIn, 10);')
        absent = dict(parameter_changes=[dict(
            target='shape', rejected_expression='shape absent',
            proposed_expression='radius=12', effective_rejected=0,
            effective_proposed=12, unit='px', direction='increase', reason='crear forma')])
        _validate_visual_plan_grounding(absent, '+ BorderSize = 1;')

    def test_visual_compile_repair_edits_current_and_keeps_plan_value(self):
        plan = dict(strategy=['Aumentar radio'], constraints=[], parameter_changes=[dict(
            target='radio', rejected_expression='radius=10',
            proposed_expression='radius=16', effective_rejected=10,
            effective_proposed=16, unit='px', direction='increase', reason='visible')])
        failed = {'app.py': self.original['app.py'] + b'RADIUS=16\nBAD_TYPE\n'}
        self.response([dict(file='app.py', operation='replace',
                            anchor='BAD_TYPE', content='GOOD_TYPE')])
        changed, _ = visual_compile_repair_proposal(
            self.api, 'Redondea las esquinas', self.original, ['app.py'], 'local',
            failed, 'CS1503 tipo incorrecto', plan)
        self.assertIn(b'RADIUS=16', changed['app.py'])
        self.assertNotIn(b'BAD_TYPE', changed['app.py'])
        self.assertIn(b'GOOD_TYPE', changed['app.py'])
        messages = self.api.post_ollama.call_args.args[1]['messages']
        self.assertIn('BAD_TYPE', messages[2]['content'])
        self.assertIn('CS1503', messages[3]['content'])

    def test_visual_compile_repair_rejects_exact_repetition_once(self):
        plan = dict(strategy=['Aumentar radio'], constraints=[], parameter_changes=[dict(
            target='radio', rejected_expression='radius=10',
            proposed_expression='radius=16', effective_rejected=10,
            effective_proposed=16, unit='px', direction='increase', reason='visible')])
        failed = {'app.py': self.original['app.py'] + b'RADIUS=16\nBAD_TYPE\n'}
        repeated = [dict(file='app.py', operation='replace',
                         anchor='BAD_TYPE', content='BAD_TYPE')]
        corrected = [dict(file='app.py', operation='replace',
                          anchor='BAD_TYPE', content='GOOD_TYPE')]
        self.api.post_ollama.side_effect = [
            dict(message=dict(content=json.dumps(dict(edits=repeated))), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=corrected))), done_reason='stop')]
        changed, audit = visual_compile_repair_proposal(
            self.api, 'Redondea las esquinas', self.original, ['app.py'], 'local',
            failed, 'CS1503 tipo incorrecto', plan)
        self.assertIn(b'GOOD_TYPE', changed['app.py'])
        self.assertNotIn(b'BAD_TYPE', changed['app.py'])
        self.assertEqual(len(audit['responses']), 2)
        self.assertIn('rejected deterministically',
                      self.api.post_ollama.call_args.args[1]['messages'][-1]['content'])

    def test_visual_compile_repair_rejects_remote_cosmetic_change(self):
        plan = dict(strategy=['Aumentar radio'], constraints=[], parameter_changes=[dict(
            target='radio', rejected_expression='radius=10',
            proposed_expression='radius=16', effective_rejected=10,
            effective_proposed=16, unit='px', direction='increase', reason='visible')])
        failed = {'app.py': (self.original['app.py'] +
                             b'RADIUS=16\nKEEP1\nKEEP2\nKEEP3\nKEEP4\nBAD_TYPE\n')}
        irrelevant = [dict(file='app.py', operation='replace',
                           anchor='    return "Enviar"', content='    return "Otro"')]
        corrected = [dict(file='app.py', operation='replace',
                          anchor='BAD_TYPE', content='GOOD_TYPE')]
        self.api.post_ollama.side_effect = [
            dict(message=dict(content=json.dumps(dict(edits=irrelevant))), done_reason='stop'),
            dict(message=dict(content=json.dumps(dict(edits=corrected))), done_reason='stop')]
        line = failed['app.py'].decode().splitlines().index('BAD_TYPE') + 1
        changed, audit = visual_compile_repair_proposal(
            self.api, 'Redondea las esquinas', self.original, ['app.py'], 'local',
            failed, f'app.py:{line}:1: error TS100: BAD_TYPE', plan)
        self.assertIn(b'GOOD_TYPE', changed['app.py'])
        self.assertIn(b'return "Enviar"', changed['app.py'])
        self.assertEqual(len(audit['responses']), 2)
        self.assertIn('ubicación diagnosticada',
                      self.api.post_ollama.call_args.args[1]['messages'][-1]['content'])


class VisualRepairReasonerTests(unittest.TestCase):
    def test_asymmetric_topology_allows_causal_plan_without_fake_multiplier(self):
        api = SimpleNamespace(CANCEL=threading.Event(), post_ollama=Mock())
        plan = dict(
            why_previous_failed='El mecanismo solo cubre una region equivalente',
            strategy=['Redondear todas las esquinas con una forma cerrada dentro de sus limites'],
            constraints=['Conservar el resto'], parameter_changes=[])
        api.post_ollama.return_value = dict(response=json.dumps(plan), done_reason='stop')
        evidence = dict(status='not_fulfilled',
            contract=dict(conditions=[dict(spatial_mode='corners')]), metrics=dict(
            gate='not_fulfilled', target_changed_ratio=.0048,
            target_spatial_profiles=[dict(name='objetivo', corners=dict(
                top_left=dict(observed=True), top_right=dict(observed=False),
                bottom_right=dict(observed=False), bottom_left=dict(observed=False)),
                sides={})]))
        result, audit = _visual_repair_plan(
            api, 'Redondea las esquinas', 'class Form {}', '+ forma parcial', evidence)
        self.assertEqual(result['parameter_changes'], [])
        self.assertEqual(len(audit['attempts']), 1)
        self.assertIn('falla de topologia',
                      api.post_ollama.call_args.args[1]['prompt'])

    def test_shape_plan_may_read_existing_control_dimensions(self):
        plan = dict(why_previous_failed='faltaba geometria',
            strategy=['Aplicar Region con radio real'], constraints=[],
            parameter_changes=[dict(
                target='signIn.Region', rejected_expression='radius=0',
                proposed_expression=(
                    'signIn.Region = Region.FromHrgn(CreateRoundRectRgn('
                    '0, 0, signIn.Width, signIn.Height, 12, 12))'),
                effective_rejected=0, effective_proposed=12, unit='px',
                direction='increase', reason='redondeo perceptible')])
        bounded = _bounded_implementation_plan('Redondea las esquinas', plan)
        self.assertEqual(len(bounded['parameter_changes']), 1)

    def test_inconsistent_plan_gets_one_reasoner_correction(self):
        api = SimpleNamespace(CANCEL=threading.Event(), post_ollama=Mock())
        def plan(proposed):
            return dict(why_previous_failed='radio insuficiente', strategy=['aumentar radio'],
                        constraints=[], parameter_changes=[dict(
                            target='radio', rejected_expression='radius=10',
                            proposed_expression=f'radius={proposed}', effective_rejected=10,
                            effective_proposed=proposed, unit='px', direction='increase',
                            reason='cambio perceptible')])
        api.post_ollama.side_effect = [
            dict(response=json.dumps(plan(8)), done_reason='stop'),
            dict(response=json.dumps(plan(16)), done_reason='stop')]
        result, audit = _visual_repair_plan(
            api, 'Redondea las esquinas', 'class Form {}', '+ radius=10',
            {'status':'not_fulfilled'})
        self.assertEqual(result['parameter_changes'][0]['effective_proposed'], 16)
        self.assertEqual(len(audit['attempts']), 2)
        self.assertIn('RECHAZADO DETERMINISTICAMENTE',
                      api.post_ollama.call_args.args[1]['prompt'])

    def test_out_of_scope_numeric_plan_gets_one_reasoner_correction(self):
        api = SimpleNamespace(CANCEL=threading.Event(), post_ollama=Mock())
        width_plan = dict(why_previous_failed='cambio insuficiente',
            strategy=['Aumentar el ancho'], constraints=[], parameter_changes=[dict(
                target='signIn', rejected_expression='new Size(210, 42)',
                proposed_expression='new Size(420, 42)', effective_rejected=210,
                effective_proposed=420, unit='px', direction='increase',
                reason='duplicar el ancho')])
        radius_plan = dict(why_previous_failed='faltaba geometria de esquina',
            strategy=['Aplicar un radio real'], constraints=[], parameter_changes=[dict(
                target='radio', rejected_expression='radius=0',
                proposed_expression='radius=8', effective_rejected=0,
                effective_proposed=8, unit='px', direction='increase',
                reason='redondeo perceptible')])
        api.post_ollama.side_effect = [
            dict(response=json.dumps(width_plan), done_reason='stop'),
            dict(response=json.dumps(radius_plan), done_reason='stop')]
        result, audit = _visual_repair_plan(
            api, 'Redondea las esquinas', 'class Form {}', '+ BorderSize = 1',
            {'status':'not_fulfilled'})
        self.assertEqual(result['parameter_changes'][0]['target'], 'radio')
        self.assertEqual(len(audit['attempts']), 2)
        self.assertIn('RECHAZADO DETERMINISTICAMENTE',
                      api.post_ollama.call_args.args[1]['prompt'])

    def test_alternative_planner_runs_once_after_two_rejected_plans(self):
        api = SimpleNamespace(CANCEL=threading.Event(), post_ollama=Mock())
        bad = dict(why_previous_failed='sin efecto', strategy=['Aumentar ancho'],
            constraints=[], parameter_changes=[dict(
                target='boton', rejected_expression='width=100',
                proposed_expression='width=200', effective_rejected=100,
                effective_proposed=200, unit='px', direction='increase',
                reason='cambiar ancho')])
        good = dict(why_previous_failed='faltaba recorte geometrico',
            strategy=['Aplicar region con radio real'], constraints=[],
            parameter_changes=[dict(
                target='radio', rejected_expression='radius=0',
                proposed_expression='radius=12', effective_rejected=0,
                effective_proposed=12, unit='px', direction='increase',
                reason='redondeo perceptible')])
        api.post_ollama.side_effect = [
            dict(response=json.dumps(bad), done_reason='stop'),
            dict(response=json.dumps(bad), done_reason='stop'),
            dict(response=json.dumps(good), done_reason='stop')]
        result, audit = _visual_repair_plan(
            api, 'Redondea las esquinas', 'class Form {}', '+ BorderSize = 1',
            {'status':'not_fulfilled'})
        self.assertEqual(result['parameter_changes'][0]['target'], 'radio')
        self.assertEqual(len(audit['attempts']), 3)
        self.assertEqual(api.post_ollama.call_args.args[1]['model'], 'qwen3.5:9b')
        self.assertFalse(api.post_ollama.call_args.args[1]['think'])

    def test_csharp_builtin_alias_does_not_require_external_evidence(self):
        original = {'Form.cs': b'class Form { void Run() {} }'}
        changed = {'Form.cs': b'class Form { void Run() { var data = new byte[4]; } }'}
        validate_unverified_sdk_types(original, changed, None)

    def test_new_unreferenced_private_helper_is_rejected(self):
        original = {'Form.cs': b'''class Form {
    public void Build() { var button = Create(); }
}
'''}
        changed = {'Form.cs': b'''class Form {
    public void Build() { var button = Create(); }
    private static void RoundCorners(object button) { Paint(button); }
}
'''}
        with self.assertRaisesRegex(ValueError, 'RoundCorners'):
            validate_new_private_helpers_reachable(original, changed)

    def test_new_private_helper_called_by_existing_flow_is_allowed(self):
        original = {'Form.cs': b'''class Form {
    public void Build() { var button = Create(); }
}
'''}
        changed = {'Form.cs': b'''class Form {
    public void Build() { var button = Create(); RoundCorners(button); }
    private static void RoundCorners(object button) { Paint(button); }
}
'''}
        validate_new_private_helpers_reachable(original, changed)

    def test_new_helpers_do_not_make_each_other_reachable(self):
        original = {'Form.cs': b'''class Form {
    public void Build() { var button = Create(); }
}
'''}
        changed = {'Form.cs': b'''class Form {
    public void Build() { var button = Create(); }
    private static void Prepare(object button) { RoundCorners(button); }
    private static void RoundCorners(object button) { Prepare(button); }
}
'''}
        with self.assertRaisesRegex(ValueError, 'Prepare'):
            validate_new_private_helpers_reachable(original, changed)

    def test_homonym_in_nested_type_does_not_connect_new_outer_helper(self):
        original = {'Form.cs': b'''class Form {
    public void Build() { Helper.RoundCorners(Create(), 10); }
    private static class Helper {
        public static void RoundCorners(object button, int radius) { Paint(button); }
    }
}
'''}
        changed = {'Form.cs': b'''class Form {
    public void Build() { Helper.RoundCorners(Create(), 10); }
    private static void RoundCorners(object button, int radius) { Paint(button); }
    private static class Helper {
        public static void RoundCorners(object button, int radius) { Paint(button); }
    }
}
'''}
        with self.assertRaisesRegex(ValueError, r'Form\.RoundCorners/2'):
            validate_new_private_helpers_reachable(original, changed)

    def test_new_overload_requires_and_accepts_matching_arity_reference(self):
        original = {'Form.cs': b'''class Form {
    public void Build() { Configure(Create()); }
    private static void Configure(object control) { Paint(control); }
}
'''}
        changed = {'Form.cs': b'''class Form {
    public void Build() { Configure(Create()); Configure(Create(), 12); }
    private static void Configure(object control) { Paint(control); }
    private static void Configure(object control, int radius) { Paint(control); }
}
'''}
        validate_new_private_helpers_reachable(original, changed)

    def test_private_constructor_is_not_treated_as_unused_helper(self):
        original = {'Form.cs': b'class Form { public void Build() {} }\n'}
        changed = {'Form.cs': b'class Form { private Form() {} public void Build() {} }\n'}
        validate_new_private_helpers_reachable(original, changed)


if __name__ == '__main__':
    unittest.main()
