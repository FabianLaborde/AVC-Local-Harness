import base64
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import server
import visual_pipeline
from visual_steps import make_steps, step_prompt, edit_diagnostic, EDIT_FAILURE_MARKERS
from visual_test_support import plan_for
import test_edit_inspection as fixtures

REAL_SAVE=server.save_working_checkpoint
REAL_RESTORE=server.restore_working_checkpoint


def scene():
    plan=plan_for(fixtures.png('red'))
    for key in ('a','b','c','d','e','f'):
        node=copy.deepcopy(plan['nodes'][0])
        node.update(id=key,kind='text',parent='surface',label='Elemento '+key)
        plan['nodes'].append(node)
    return plan


def request(plan,scope='reproduce'):
    return dict(instruction='Aproximá el diseño. Conservá eventos.',scope=scope,preserve=['Conservar eventos'],
                goals=[dict(id='composition',targets=[n['id'] for n in plan['nodes']],basis='reference',expected='Distribución'),
                       dict(id='appearance',targets=[n['id'] for n in plan['nodes']],basis='reference',expected='Colores'),
                       dict(id='content',targets=[n['id'] for n in plan['nodes']],basis='reference',expected='Contenido')])


class PlanningTests(unittest.TestCase):
    def test_global_steps_cover_scene_once_and_parent_precedes_children(self):
        plan=scene();steps=make_steps(request(plan),plan)
        self.assertEqual([s['targets'] for s in steps],[['surface'],['a','b','c'],['d','e','f']])
        self.assertEqual(sum(len(s['targets']) for s in steps),len(plan['nodes']))

    def test_partial_goals_never_expand_to_unrequested_reference_nodes(self):
        plan=scene();contract=request(plan,'partial');contract['goals']=contract['goals'][:1]
        contract['goals'][0]['targets']=['a','b','c','d']
        steps=make_steps(contract,plan)
        self.assertEqual([s['targets'] for s in steps],[['a','b','c'],['d']])
        prompt=step_prompt(contract,plan,steps[0])
        self.assertIn('Conservar eventos',prompt)
        self.assertIn('Elemento a',prompt)
        self.assertNotIn('Elemento d',prompt)
        self.assertNotIn('Elemento e',prompt)
        self.assertIn('surface',prompt)  # parent context is retained

    def test_multiple_partial_goals_without_targets_are_not_lost(self):
        plan=scene();contract=request(plan,'partial')
        for g in contract['goals']:g['targets']=[]
        self.assertEqual(len(make_steps(contract,plan)),3)

    def test_failure_is_reported_instead_of_being_a_silent_noop(self):
        self.assertEqual(edit_diagnostic(-2,'')[0],'timeout')
        self.assertEqual(edit_diagnostic(0,'The LLM did not conform to the edit format.')[0],'format')
        self.assertEqual(edit_diagnostic(0,'Applied edit'),('',''))


class ProcessWatchTests(unittest.TestCase):
    def setUp(self):
        self.project=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(server,'audit'))
        self.enterContext(patch.object(server,'log'))
        server.CANCEL.clear();self.addCleanup(server.CANCEL.clear)

    def test_format_error_interrupts_internal_retry_before_process_deadline(self):
        start=time.monotonic()
        script='import time; print("The LLM did not conform to the edit format.",flush=True);time.sleep(30)'
        code,output=server.run_process([sys.executable,'-c',script],self.project,timeout=10,stop_output=EDIT_FAILURE_MARKERS)
        self.assertEqual(code,-3)
        self.assertLess(time.monotonic()-start,5)
        self.assertIn('fallo de formato',output)
        self.assertIsNone(server.PROCESS)

    def test_watched_success_keeps_output_and_exit_code(self):
        code,output=server.run_process([sys.executable,'-c','print("applied");exit(4)'],self.project,timeout=5,stop_output=EDIT_FAILURE_MARKERS)
        self.assertEqual(code,4);self.assertIn('applied',output)

    def test_watched_process_obeys_deadline(self):
        code,_=server.run_process([sys.executable,'-c','import time;time.sleep(30)'],self.project,timeout=.2,stop_output=EDIT_FAILURE_MARKERS)
        self.assertEqual(code,-2)


class SequenceTests(unittest.TestCase):
    def setUp(self):
        fixtures.EditCycleTests.setUp(self)
        self.plan=scene();self.contract=request(self.plan)
        self.enterContext(patch.object(server,'visual_specification',return_value=self.plan))
        self.enterContext(patch.object(visual_pipeline,'prepare_contract',return_value=self.contract))
        self.snapshots=[fixtures.observation(i) for i in range(8)]
        self.vision.side_effect=lambda *a,**kw:json.dumps(dict(checks=[dict(goal_id=g['id'],state='observed',
            confidence='medium',evidence='Según el modelo',suggestion='') for g in self.contract['goals']]))

    def run_sequence(self,attempts=2):
        server.run_job(self.project,self.contract['instruction'],'',attempts,'coder',
            image=base64.b64encode(fixtures.png('red')).decode(),build_mode='wpf',quality_mode=False)
        return server.STATE['verification']

    def test_each_step_builds_and_captures_before_next_editor_reads_updated_files(self):
        original=self.edit.side_effect;seen=[]
        def edit(args,*a,**kw):
            seen.append((self.file.read_text(),self.captured,args))
            return original(args,*a,**kw)
        self.edit.side_effect=edit
        report=self.run_sequence()
        self.assertEqual(self.edits,3);self.assertEqual(self.captured,4)
        self.assertEqual([item[1] for item in seen],[1,2,3])
        self.assertNotEqual(seen[0][0],seen[1][0])
        self.assertNotIn('Elemento d',seen[1][2])
        self.assertEqual([p['state'] for p in report['pasos']],['built']*3)
        self.assertTrue(report['compilado'])
        self.assertEqual(self.vision.call_count,1)  # no full vision re-analysis between edits

    def test_two_format_failures_stop_and_do_not_claim_remaining_steps_done(self):
        self.edit.side_effect=lambda *a,**kw:(-3,EDIT_FAILURE_MARKERS[0])
        report=self.run_sequence()
        self.assertEqual(self.edit.call_count,2)
        self.assertEqual(self.build.call_count,1)
        self.assertEqual([p['state'] for p in report['pasos']],['editor_failed','editor_failed','pending'])
        self.assertNotEqual(report['estado'],'coincidencia_orientativa')
        self.assertIsNotNone(server.VISUAL_CAPTURES['despues'])
        self.assertEqual(server.STATE['phase'],'error')
        self.assertNotIn('Presupuesto agotado',server.STATE['result'])

    def test_repair_excerpt_uses_only_the_reported_project_file(self):
        self.file.write_text('\n'.join('line'+str(i) for i in range(20)),encoding='utf8')
        adapter=visual_pipeline.WpfAdapter(server,self.project,'coder',{})
        excerpt=adapter.repair_context(str(self.file)+'(10,3): error MC0000')
        self.assertIn('line9',excerpt)
        self.assertNotIn('line0\n',excerpt)
        self.assertNotIn('line19',excerpt)

    def test_repair_prompt_does_not_resend_the_global_redesign(self):
        self.build.side_effect=[(0,'base'),(0,'first'),(1,'broken'),(0,'repaired'),(0,'last')]
        original=self.edit.side_effect;messages=[]
        def edit(args,*a,**kw):
            messages.append(args)
            return original(args,*a,**kw)
        self.edit.side_effect=edit
        self.run_sequence()
        repair=messages[2]
        self.assertIn('SOLAMENTE la causa',repair)
        self.assertNotIn(self.contract['instruction'],repair)

    def test_repairs_share_one_budget_and_recovery_keeps_prior_work(self):
        # Use real checkpoint implementation captured before fixture mocks.
        self.build.side_effect=[(0,'base'),(0,'step1'),(1,'bad2'),(0,'repair2'),(1,'bad3'),(0,'restored')]
        with patch.object(server,'save_working_checkpoint',side_effect=REAL_SAVE),patch.object(server,'restore_working_checkpoint',side_effect=REAL_RESTORE):
            report=self.run_sequence()
        self.assertEqual(self.edits,4)  # three planned edits + one repair, not one repair per step
        self.assertIn('4*',self.file.read_text())  # step2 repaired version, not initial or failed step3
        self.assertEqual(report['pasos'][-1]['state'],'failed')
        self.assertEqual(server.STATE['phase'],'error')

    def test_cancel_clears_final_evidence_and_never_runs_more_steps(self):
        def edit(*a,**kw):server.CANCEL.set();return -1,'cancelled'
        self.edit.side_effect=edit
        self.run_sequence()
        self.assertEqual(self.edit.call_count,1)
        self.assertIsNone(server.VISUAL_CAPTURES['despues'])
        self.assertEqual(server.STATE['phase'],'stopped')

    def test_failed_step_is_restored_before_following_step_and_progress_survives(self):
        self.build.side_effect=[(0,'base'),(1,'first failed'),(0,'restored'),(0,'second'),(0,'third')]
        original=self.edit.side_effect;seen=[]
        def edit(*a,**kw):
            seen.append(self.file.read_text())
            return original(*a,**kw)
        self.edit.side_effect=edit
        with patch.object(server,'save_working_checkpoint',side_effect=REAL_SAVE),patch.object(server,'restore_working_checkpoint',side_effect=REAL_RESTORE):
            report=self.run_sequence(attempts=1)
        self.assertEqual(seen[1],self.initial)
        self.assertEqual([p['state'] for p in report['pasos']],['failed','built','built'])
        self.assertIn('4*',self.file.read_text())

    def test_exhausted_budget_leaves_explicit_pending_steps(self):
        with patch('visual_sequence.MAX_EDIT_CALLS',1):
            report=self.run_sequence()
        self.assertEqual(self.edits,1)
        self.assertEqual([p['state'] for p in report['pasos']],['built','pending','pending'])
        self.assertIn('Presupuesto agotado',server.STATE['result'])

    def test_partial_output_is_built_but_never_reported_as_complete_step(self):
        original=self.edit.side_effect
        def edit(*a,**kw):
            original(*a,**kw)
            return -3,EDIT_FAILURE_MARKERS[0]
        self.edit.side_effect=edit
        report=self.run_sequence()
        self.assertTrue(report['compilado'])
        self.assertEqual([p['state'] for p in report['pasos']],['partial']*3)
        self.assertEqual(report['estado'],'no_verificable')

    def test_deadline_stops_before_launching_another_edit(self):
        with patch('visual_sequence.JOB_SECONDS',0):
            report=self.run_sequence()
        self.edit.assert_not_called()
        self.assertEqual([p['state'] for p in report['pasos']],['pending']*3)

    def test_partial_repair_output_remains_marked_even_if_build_succeeds(self):
        self.build.side_effect=[(0,'base'),(1,'failed'),(0,'repair'),(0,'second'),(0,'third')]
        original=self.edit.side_effect
        def edit(*a,**kw):
            result=original(*a,**kw)
            return (-3,EDIT_FAILURE_MARKERS[0]) if self.edits==2 else result
        self.edit.side_effect=edit
        report=self.run_sequence()
        self.assertEqual(report['pasos'][0]['state'],'partial')
        self.assertEqual(report['pasos'][0]['repair_issue'],'format')


if __name__=='__main__':unittest.main()
