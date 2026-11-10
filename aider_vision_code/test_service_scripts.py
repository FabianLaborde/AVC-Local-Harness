from pathlib import Path
import unittest


ROOT = Path(__file__).parent
START = (ROOT / 'Start-AVC.ps1').read_text(encoding='utf8')
STOP = (ROOT / 'Stop-AVC.ps1').read_text(encoding='utf8')
STATUS = (ROOT / 'Status-AVC.ps1').read_text(encoding='utf8')
BAT = (ROOT / 'iniciar.bat').read_text(encoding='utf8')
VBS = (ROOT / 'iniciar-sin-ventana.vbs').read_text(encoding='utf8')
STOP_VBS = (ROOT / 'detener-sin-ventana.vbs').read_text(encoding='utf8')
PROGRESS = (ROOT / 'AVC-Service-Progress.ps1').read_text(encoding='utf8')


class ServiceScriptTests(unittest.TestCase):
    def test_start_is_hidden_logged_and_health_checked(self):
        self.assertIn('-WindowStyle Hidden', START)
        self.assertIn('-RedirectStandardOutput $stdoutPath', START)
        self.assertIn('-RedirectStandardError $stderrPath', START)
        self.assertNotEqual('avc-server.stdout.log', 'avc-server.stderr.log')
        self.assertIn("$healthUrl = $url + 'api/version'", START)
        self.assertIn('Set-Content -LiteralPath $pidPath', START)
        self.assertIn('Get-AttributedServerPid (Get-AvcIdentity)', START)
        self.assertIn('Identity.running.installation', START)
        self.assertIn('AVC ya está ejecutándose', START)

    def test_stop_validates_registered_process_before_killing_tree(self):
        self.assertIn("TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim()", STOP)
        self.assertIn('$serverProcess.Path', STOP)
        self.assertIn('& taskkill.exe /PID $serverPid /T /F', STOP)
        self.assertIn('Get-AttributedServerPid $identity', STOP)
        self.assertIn('Identity.running.installation', STOP)
        self.assertIn('no corresponde al Python de esta instalación', STOP)

    def test_status_checks_endpoint_and_registration(self):
        self.assertIn('http://127.0.0.1:8765/api/version', STATUS)
        self.assertIn('Proceso oculto registrado', STATUS)

    def test_legacy_launcher_delegates_without_persistent_console(self):
        self.assertIn('wscript.exe', BAT)
        self.assertIn('iniciar-sin-ventana.vbs', BAT)
        self.assertNotIn('python server.py', BAT)
        self.assertIn('shell.Run command, 0, False', VBS)

    def test_desktop_stop_launcher_is_hidden(self):
        self.assertIn('AVC-Service-Progress.ps1', STOP_VBS)
        self.assertIn('-Action Stop', STOP_VBS)
        self.assertIn('-WindowStyle Hidden', STOP_VBS)
        self.assertIn('shell.Run command, 0, False', STOP_VBS)

    def test_desktop_start_uses_progress_window_without_console(self):
        self.assertIn('AVC-Service-Progress.ps1', VBS)
        self.assertIn('-Action Start', VBS)
        self.assertIn('-WindowStyle Hidden', VBS)
        self.assertIn('shell.Run command, 0, False', VBS)

    def test_progress_wrapper_delegates_and_observes_real_health(self):
        self.assertIn("[ValidateSet('Start', 'Stop')]", PROGRESS)
        self.assertIn("'Start-AVC.ps1'", PROGRESS)
        self.assertIn("'Stop-AVC.ps1'", PROGRESS)
        self.assertIn('http://127.0.0.1:8765/api/version', PROGRESS)
        self.assertIn('-WindowStyle Hidden', PROGRESS)
        self.assertIn('-RedirectStandardOutput $outputPath', PROGRESS)
        self.assertIn('-RedirectStandardError $errorPath', PROGRESS)
        self.assertNotIn('taskkill.exe', PROGRESS)
        self.assertIn('Convert-ToAsciiText', PROGRESS)
        self.assertIn('AutoScaleMode]::Dpi', PROGRESS)
        self.assertIn('Drawing.Size(500, 330)', PROGRESS)
        self.assertIn('Drawing.Size(460, 72)', PROGRESS)
        self.assertIn('$expectedState -and $null -eq $state.FinishedAt', PROGRESS)
        self.assertIn('$state.Worker = Start-Process', PROGRESS)
        self.assertNotIn('Aider Vision & Code', PROGRESS)
        self.assertIn("$Action -eq 'Start'", PROGRESS)
        self.assertIn('$stage.ForeColor = $themeColor', PROGRESS)


if __name__ == '__main__':
    unittest.main()
