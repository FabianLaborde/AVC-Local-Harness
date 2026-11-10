"""Static regression checks for the dedicated visual-workspace layout."""
import unittest
from pathlib import Path

HTML = Path(__file__).with_name('web').joinpath('index.html').read_text(encoding='utf-8')


class DedicatedVisualLayoutTests(unittest.TestCase):
    def test_visual_mode_hides_irrelevant_panels(self):
        self.assertIn('.main.visualMode>.promptHistory', HTML)
        self.assertIn('.main.visualMode>.composer', HTML)
        self.assertIn('.main.visualMode>#chatView', HTML)
        self.assertIn('display:none!important', HTML)
        self.assertIn('grid-template-rows:44px minmax(0,1fr)!important', HTML)

    def test_visual_view_is_scrollable_and_full_height(self):
        self.assertIn('.main.visualMode>#visionView.view.active', HTML)
        self.assertIn('overflow-y:auto!important', HTML)
        self.assertIn('max-height:100%', HTML)

    def test_toggling_is_state_preserving(self):
        self.assertIn("document.querySelector('.main').classList.toggle('visualMode',visual)", HTML)
        self.assertIn("$('chatView').classList.toggle('active',!visual)", HTML)
        self.assertIn("$('visionView').classList.toggle('active',visual)", HTML)
        self.assertIn("selectTab('chat');$('instruction').focus()", HTML)
        self.assertIn("$('analysisOnly').checked=true", HTML)

    def test_simple_interface_exposes_models_but_hides_advanced_controls(self):
        self.assertIn('body.simple #advancedSettings>h2:first-child',HTML)
        self.assertIn('body.simple #advancedSettings>#settings',HTML)
        self.assertIn('.sidebar #toggleAdvanced{display:none!important}',HTML)
        self.assertNotIn("post({automatic_settings:",HTML)
        request=HTML.split("$('run').onclick=async",1)[1].split("$('stop').onclick",1)[0]
        for obsolete in ('automatic_settings','test_command','build_mode','quality_mode',
                         'attempts:','tech_mode','analysis_only','inspection_criteria'):
            self.assertNotIn(obsolete,request)
        self.assertIn("code_model:$('codeModel').value",request)
        self.assertIn("vision_model:$('visionModel').value",request)

    def test_rejected_preview_shows_images_but_disables_apply(self):
        self.assertIn("const blocked=!!pendingPreview.blocked",HTML)
        self.assertIn("$('applyPreview').disabled=blocked",HTML)
        self.assertIn('Comparación diagnóstica',HTML)

    def test_project_path_wraps_and_tracks_the_selected_folder(self):
        self.assertIn('.projectPathFull{',HTML)
        self.assertIn("projectPathFull.id='projectPathFull'",HTML)
        self.assertIn('overflow-wrap:anywhere',HTML)
        self.assertIn("$('project').addEventListener('input',updateProjectPath)",HTML)
        self.assertIn("$('project').value=folderCurrent;updateProjectPath()",HTML)

    def test_attachment_button_is_first_in_its_existing_tool_row(self):
        self.assertIn("const attachButton=$('attachImage')",HTML)
        self.assertIn("grid-template-areas:'tools tools' 'attachment attachment'",HTML)
        self.assertIn('>.composerTools{justify-content:flex-start;grid-column:1/-1}',HTML)
        self.assertIn('attachButton.parentElement.prepend(attachButton)',HTML)

    def test_simple_composer_scrolls_only_the_text_and_keeps_controls_fixed(self):
        self.assertIn('.main .composerEditor{display:grid',HTML)
        self.assertIn('min-height:0!important;overflow:hidden}',HTML)
        self.assertIn('.main .composerInner{padding:0;border:0;background:transparent',HTML)
        self.assertIn("grid-template-rows:auto auto minmax(38px,1fr) auto",HTML)
        self.assertIn("composerEditor>#instruction{min-height:38px!important",HTML)
        self.assertIn('overflow-y:auto}',HTML)

    def test_simple_history_is_compact_but_can_be_expanded(self):
        self.assertIn('grid-template-rows:44px 132px minmax(0,1fr) 140px!important',HTML)
        self.assertIn('.main.historyExpanded:not(.visualMode)',HTML)
        self.assertIn('minmax(220px,48%) minmax(0,1fr) 140px!important',HTML)
        self.assertIn('body.simple #expandExecution,body.simple #jobUiaEvidence{display:none!important}',HTML)
        self.assertNotIn('body.simple .paneExpand,body.simple #jobUiaEvidence',HTML)

    def test_sidebar_can_toggle_persistent_neutral_dark_theme(self):
        self.assertIn("themeButton.id='toggleTheme'",HTML)
        self.assertIn("const THEME='avc028_theme'",HTML)
        self.assertIn("document.body.classList.toggle('deepDark',dark)",HTML)
        self.assertIn("dark?'◐ Tema azul':'🌙 Modo oscuro'",HTML)
        self.assertIn('body.deepDark .topbar',HTML)


if __name__ == '__main__':
    unittest.main()
