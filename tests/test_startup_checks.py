from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import startup_checks as checks
import windows_tools as windows


class EnvironmentTests(unittest.TestCase):
    def test_unsupported_platform(self):
        with patch.object(checks.sys,'platform','linux'):
            with self.assertRaisesRegex(RuntimeError,'Windows'):checks.check_environment('.')

    def test_old_python(self):
        with patch.object(checks.sys,'platform','win32'),patch.object(checks.sys,'version_info',(3,10)):
            with self.assertRaisesRegex(RuntimeError,'3.11'):checks.check_environment('.')

    def test_incomplete_package(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError,'檔案不完整'):checks.check_environment(folder)

    def test_read_only_folder(self):
        with patch.object(checks.tempfile,'TemporaryFile',side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(RuntimeError,'無法寫入'):checks.check_environment(Path(checks.__file__).parent)

    def test_complete_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(Path,'is_file',return_value=True):checks.check_environment(folder)

    def test_disabled_and_enabled_startup(self):
        for state,expected in ((2,True),(3,False),(7,False)):
            with self.subTest(state=state),patch('winreg.OpenKey',return_value=MagicMock()),patch('winreg.QueryValueEx',side_effect=[('command',1),(state.to_bytes(4,'little')+bytes(8),3)]),patch.object(windows,'_autostart_command_valid',return_value=True):
                self.assertEqual(windows.autostart_status()['enabled'],expected)

    def test_registry_denied(self):
        with patch('winreg.OpenKey',side_effect=PermissionError):
            self.assertIn('無法讀取',windows.autostart_status()['reason'])
