import copy
import unittest
from catalog_image_edit import apply_edits
from test_wpf_catalog import example
from wpf_catalog import MAX_NODES, render


def edit(plan, *ops):
    return apply_edits(plan,dict(supported=True,operations=list(ops)))


def addition(**changes):
    op=dict(kind='add',parent='card',before='submit',component=dict(kind='field',text='Teléfono',required=True,icon='none'))
    op.update(changes)
    return op


class ComponentTests(unittest.TestCase):
    def test_add_remove_roundtrip_preserves_plan_and_sources(self):
        original=example();original.update(window_fit='content_height',outer_margin=24)
        added=edit(original,addition())
        new=next(n for n in added['nodes'] if n['id'] not in {x['id'] for x in original['nodes']})
        self.assertIn('C_'+new['id']+'.Text',render(added)['MainWindow.xaml.cs'])
        self.assertEqual(added['nodes'][-2],new)
        restored=edit(added,dict(kind='remove',ids=[new['id']]))
        self.assertEqual(restored,original)
        self.assertEqual(render(restored),render(original))

    def test_remove_required_field_and_button_cleans_generated_references(self):
        after=edit(example(),dict(kind='remove',ids=['secret','submit']))
        files=render(after)
        self.assertNotIn('C_secret',files['MainWindow.xaml.cs'])
        self.assertNotIn('On_submit',files['MainWindow.xaml.cs'])
        self.assertNotIn('Click=',files['MainWindow.xaml'])
        self.assertIn('C_user.Text',files['MainWindow.xaml.cs'])

    def test_invalid_additions_and_deletions_are_atomic(self):
        original=example()
        for op in [addition(parent='user'),addition(before='help'),
                   addition(component=dict(kind='field',text='x',action='validate_required')),
                   addition(component=dict(kind='button',text='x',action='save_database')),
                   addition(component=dict(kind='text',text='x',id='user')),
                   dict(kind='remove',ids=['card']),dict(kind='remove',ids=['missing']),
                   dict(kind='remove',ids=['user','user'])]:
            with self.subTest(op=op),self.assertRaises(ValueError):edit(original,op)
        self.assertEqual(original,example())

    def test_append_in_container_follows_existing_children(self):
        after=edit(example(),addition(parent='row',before='',component=dict(kind='text',text='Nuevo')))
        children=[n['text'] for n in after['nodes'] if n['parent']=='row']
        self.assertEqual(children,['Recordarme','Ayuda','Nuevo'])
        render(after)

    def test_component_limit_is_enforced(self):
        plan=example()
        while len(plan['nodes'])<MAX_NODES:plan=edit(plan,addition(component=dict(kind='text',text='x')))
        with self.assertRaises(ValueError):edit(plan,addition())

    def test_supported_leaf_types_render(self):
        for component in [dict(kind='text',text='Ayuda'),dict(kind='checkbox',text='Aceptar'),
                          dict(kind='button',text='Validar',action='validate_required'),
                          dict(kind='password',text='Clave extra',required=True),
                          dict(kind='title',text='Título'),dict(kind='avatar',text='',icon='user')]:
            with self.subTest(component=component):render(edit(example(),addition(component=component)))


if __name__=='__main__':unittest.main()
