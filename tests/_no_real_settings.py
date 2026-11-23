"""Keeps the tests away from the settings, the music and the Discogs token of whoever runs them.

Every test file imports this first, before anything of the app, with this one line:

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _no_real_settings  # noqa: E402,E702

Why. When no settings file is named, the app looks for one in this order: the PRESSLING_CONFIG (or older CURATOR_CONFIG) environment variable, a
pressling.toml (or older curator.toml) in the current folder, and last the person's own profile (%APPDATA%/pressling on Windows, $XDG_CONFIG_HOME/pressling
or ~/.config/pressling elsewhere, or the older music-curator folder there). Its data (answers,
playlists, play counts, the log) and a .env file with the Discogs token live beside those settings; the token is also read from the DISCOGS_TOKEN environment
variable and, on Windows, from the registry. Many tests end with config.load(None), "back to the defaults", and many never load any settings. On the computer
of somebody who uses the app, "the defaults" are their real settings: a test could read their library, write beside their data, or send their token to
whatever it was testing with. A test run must never be able to do that, however it is started: the whole suite, one file, one class or one test.

What this does, once, for the run and for every program the tests start:
- the profile's settings folder is an empty folder made for this run (APPDATA and XDG_CONFIG_HOME, both of them on every system, because some tests
  pretend to be on another one). So "the defaults" are what they are on a computer the app was never used on: no settings, no data, no token;
- PRESSLING_CONFIG, CURATOR_CONFIG and DISCOGS_TOKEN are taken out of the environment;
- on Windows, asking the registry for DISCOGS_TOKEN gets the answer "there is none";
- a pressling.toml or curator.toml in the folder the tests are started from stops the run before it begins, with the reason: nothing here can make the
  app not look there;
- when the run ends the folder is removed. If a test left anything in it, that is said, because on a real computer it would have gone into somebody's
  settings folder (and every test after it would have found it).

A test that is about one of these things still tests it, by setting it for itself: mock.patch.dict(os.environ, ...), a settings file in its own temp folder,
mock.patch.object(discogs.sys, "platform", ...). tests/test_no_real_settings.py checks that all of this holds, and that every test file has the line.
"""
import atexit
import os
import shutil
import sys
import tempfile

for _name in ("pressling.toml", "curator.toml"):
    if os.path.exists(os.path.join(os.getcwd(), _name)):
        raise SystemExit(f"The tests were not run: there is a {_name} in {os.getcwd()}, and the app would use it as its settings, so the tests could read or change "
                     "the music it points at. Start them from a folder without one (python -m unittest discover -s <this repository>/tests works from anywhere).")

FOLDER = tempfile.mkdtemp(prefix="pressling-tests-")  # stands in for the profile's settings folder; it starts empty and should end empty
for _name in ("APPDATA", "XDG_CONFIG_HOME"):
    os.environ[_name] = FOLDER
for _name in ("PRESSLING_CONFIG", "CURATOR_CONFIG", "DISCOGS_TOKEN"):
    os.environ.pop(_name, None)

if sys.platform == "win32":
    import winreg

    _ask_the_registry = winreg.QueryValueEx

    def _no_token_there(key, name):
        if str(name).upper() == "DISCOGS_TOKEN":
            raise FileNotFoundError(2, "the tests are kept away from the Discogs token in the registry")
        return _ask_the_registry(key, name)

    winreg.QueryValueEx = _no_token_there  # the app looks this up when it asks, so it gets this one


def _tidy():
    left = []
    for folder, _, names in os.walk(FOLDER):
        left += [os.path.relpath(os.path.join(folder, name), FOLDER) for name in names]
    shutil.rmtree(FOLDER, ignore_errors=True)
    if left:
        print(f"\nA test wrote into what stands in for your settings folder: {', '.join(sorted(left)[:10])}. It should keep its files in a temp folder of its own "
              "(see tests/_no_real_settings.py).", file=sys.stderr)


atexit.register(_tidy)
