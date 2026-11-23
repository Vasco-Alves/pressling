"""The tests can't reach the settings, the music or the Discogs token of whoever runs them: what _no_real_settings.py promises. Standard library only:

    python -m unittest discover -s tests
"""
import ast
import glob
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _no_real_settings  # noqa: E402,E702,F401  first of all: keeps the tests away from your own settings, music and token
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from music_curator import config, discogs  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD = "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _no_real_settings"
STAND_IN = _no_real_settings.FOLDER


def inside(path):
    return os.path.normcase(os.path.realpath(path)).startswith(os.path.normcase(os.path.realpath(STAND_IN)) + os.sep)


class NoRealSettings(unittest.TestCase):
    def setUp(self):
        self.addCleanup(config.load, None)
        config.load(None)  # "the defaults", as most tests leave them

    def test_every_test_file_has_the_line_before_anything_of_the_app(self):
        files = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
        self.assertGreater(len(files), 20)
        for file in files:
            with open(file, encoding="utf-8") as f:
                lines = f.read().splitlines()
            guard = [i for i, line in enumerate(lines) if line.startswith(GUARD)]
            app = [i for i, line in enumerate(lines) if re.match(r"\s*(from|import)\s+music_curator\b", line)]
            self.assertTrue(guard and (not app or guard[0] < app[0]), f"{os.path.basename(file)} has to import _no_real_settings before anything of the app (see that file)")

    def test_the_defaults_are_an_empty_folder_made_for_this_run(self):
        for system in ("win32", "linux", "darwin"):  # some tests pretend to be on another system
            with mock.patch.object(config.sys, "platform", system):
                self.assertTrue(inside(config.user_config_path()), system)
        self.assertNotIn("CURATOR_CONFIG", os.environ)
        self.assertNotIn("PRESSLING_CONFIG", os.environ)
        self.assertIsNone(config.config_path(), "with no settings file named, none is found")
        self.assertTrue(inside(config.target_path()) and inside(config.env_file()))
        self.assertEqual((config.path("library"), config.path("log"), config.path("inbox")), ("", "", []))

    def test_no_token_can_be_found(self):
        self.assertNotIn("DISCOGS_TOKEN", os.environ)
        with mock.patch.object(discogs, "_tok", [None]), self.assertRaises(SystemExit) as stopped:
            discogs.token()
        self.assertIn("No Discogs token found", str(stopped.exception))

    @unittest.skipUnless(sys.platform == "win32", "only Windows has a registry")
    def test_the_registry_has_no_token_for_a_test(self):
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            for name in ("DISCOGS_TOKEN", "Discogs_Token"):
                with self.assertRaises(FileNotFoundError):
                    winreg.QueryValueEx(key, name)

    def test_a_program_the_tests_start_gets_the_same(self):
        code = "import os; print(ascii([os.environ.get(k) for k in ('APPDATA', 'XDG_CONFIG_HOME', 'PRESSLING_CONFIG', 'CURATOR_CONFIG', 'DISCOGS_TOKEN')]))"
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
        self.assertEqual(ast.literal_eval(done.stdout.strip()), [STAND_IN, STAND_IN, None, None, None], done.stderr[-400:])

    def test_a_run_of_its_own_gets_its_own_folder_and_takes_it_away_again(self):
        code = "import sys; sys.path.insert(0, sys.argv[1]); import _no_real_settings as mine; import os; print(ascii([mine.FOLDER, os.listdir(mine.FOLDER)]))"
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run([sys.executable, "-I", "-c", code, HERE], capture_output=True, text=True, timeout=120, cwd=folder)
            self.assertEqual(done.returncode, 0, done.stderr[-400:])
            theirs, held = ast.literal_eval(done.stdout.strip())
            self.assertEqual((held, os.path.exists(theirs), os.path.isdir(STAND_IN)), ([], False, True))
            self.assertNotEqual(theirs, STAND_IN)

    def test_a_settings_file_where_the_tests_are_started_stops_them(self):
        code = "import sys; sys.path.insert(0, sys.argv[1]); import _no_real_settings; print('went on')"
        for name in ("pressling.toml", "curator.toml"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                    f.write("[paths]\nlibrary = 'the music of whoever runs the tests'\n")
                done = subprocess.run([sys.executable, "-I", "-c", code, HERE], capture_output=True, text=True, timeout=120, cwd=folder)
                self.assertNotEqual(done.returncode, 0)
                self.assertNotIn("went on", done.stdout)
                self.assertIn(name, done.stderr)


if __name__ == "__main__":
    unittest.main()
