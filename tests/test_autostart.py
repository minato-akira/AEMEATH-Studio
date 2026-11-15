from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import windows_tools


@unittest.skipUnless(sys.platform == 'win32', 'Windows command-line parser')
class AutostartTests(unittest.TestCase):
    def test_paths_and_arguments(self):
        with tempfile.TemporaryDirectory(prefix='啟動 test ') as folder:
            root = Path(folder)
            exe = root / 'pythonw.exe'
            script = root / 'Launch.pyw'
            exe.touch()
            script.touch()
            command = subprocess.list2cmdline([str(exe), str(script), '--startup'])
            with patch.object(windows_tools, '__file__', str(root / 'windows_tools.py')):
                self.assertTrue(windows_tools._autostart_command_valid(command))
                self.assertFalse(windows_tools._autostart_command_valid(command + ' --extra'))
                self.assertFalse(windows_tools._autostart_command_valid(''))
                self.assertFalse(windows_tools._autostart_command_valid(None))
                other = root / 'other.pyw'
                other.touch()
                self.assertFalse(windows_tools._autostart_command_valid(
                    subprocess.list2cmdline([str(exe), str(other), '--startup'])))
                exe.unlink()
                self.assertFalse(windows_tools._autostart_command_valid(command))
                exe.touch()
                script.unlink()
                self.assertFalse(windows_tools._autostart_command_valid(command))

    def test_missing_registry_value(self):
        with patch('winreg.OpenKey', side_effect=FileNotFoundError):
            self.assertFalse(windows_tools.autostart_enabled())


if __name__ == '__main__':
    unittest.main()
