import unittest

from source_patch import PatchApplicationError, apply_patches


class SourcePatchTests(unittest.TestCase):
    def test_same_engine_edits_xaml_python_and_html_without_mutating_input(self):
        sources = {
            'View.xaml': '<TextBlock HorizontalAlignment="Left"/>',
            'app.py': 'enabled = False\n',
            'template.html': '<h1 class="old">Datos</h1>',
        }
        original = dict(sources)
        result = apply_patches(sources, [
            {'file': 'View.xaml', 'search': 'HorizontalAlignment="Left"',
             'replace': 'HorizontalAlignment="Right"', 'replace_all': False},
            {'file': 'app.py', 'search': 'enabled = False',
             'replace': 'enabled = True', 'replace_all': False},
            {'file': 'template.html', 'search': 'class="old"',
             'replace': 'class="new"', 'replace_all': False},
        ])
        self.assertEqual(original, sources)
        self.assertIn('Right', result['View.xaml'])
        self.assertIn('True', result['app.py'])
        self.assertIn('class="new"', result['template.html'])

    def test_replace_all_is_explicit_and_bounded(self):
        result = apply_patches({'style.css': '.item{} .item{}'}, [{
            'file': 'style.css', 'search': '.item', 'replace': '.entry',
            'replace_all': True,
        }])
        self.assertEqual('.entry{} .entry{}', result['style.css'])
        with self.assertRaises(PatchApplicationError):
            apply_patches({'style.css': '.item{} .item{}'}, [{
                'file': 'style.css', 'search': '.item', 'replace': '.entry',
                'replace_all': False,
            }])

    def test_rejected_edit_exposes_partial_copy_but_never_mutates_sources(self):
        sources = {'app.py': 'value = 1\n', 'page.html': '<p>old</p>'}
        with self.assertRaises(PatchApplicationError) as caught:
            apply_patches(sources, [
                {'file': 'app.py', 'search': 'value = 1', 'replace': 'value = 2',
                 'replace_all': False},
                {'file': '../secret.txt', 'search': 'x', 'replace': 'y',
                 'replace_all': False},
            ])
        self.assertEqual('value = 1\n', sources['app.py'])
        self.assertEqual('value = 2\n', caught.exception.partial['app.py'])
        self.assertNotIn('../secret.txt', caught.exception.partial)

    def test_invalid_schema_and_noop_are_rejected(self):
        for edits in ([],
                      [{'file': 'a.py', 'search': '', 'replace': 'x'}],
                      [{'file': 'a.py', 'search': 'x', 'replace': 'x'}],
                      [{'file': 'a.py', 'search': 'x', 'replace': 'y',
                        'replace_all': 'yes'}]):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                apply_patches({'a.py': 'x'}, edits)


if __name__ == '__main__':
    unittest.main()
