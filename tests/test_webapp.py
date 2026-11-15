"""Tests for the browser app's file reader and server. Standard library only, synthetic files in a temp folder:

    python -m unittest discover -s tests
"""
import contextlib
import csv
import http.client
import io
import ipaddress
import json
import os
import socket
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _no_real_settings  # noqa: E402,E702,F401  first of all: keeps the tests away from your own settings, music and token
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from music_curator import library, tagwriter, webapp  # noqa: E402

FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413  # MPEG-1 Layer III, 128 kbps, 44.1 kHz: 417 bytes, 1152 samples
JPEG = b"\xff\xd8\xff\xe0not-really-a-jpeg"


def write_mp3(path, frames=200, **tags):
    e = dict(title="Song", artist="Artist", album="Album", genre="Acid", year="1995", discogs="https://www.discogs.com/release/1")
    e.update(tags)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(tagwriter.build_tag(e, JPEG) + FRAME * frames)
    return path


def v24_frame(fid, body):
    n = len(body)
    return fid + bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127]) + b"\x00\x00" + body


class Reading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def test_tags_length_and_cover(self):
        info = library.read_song(write_mp3(self.path("a.mp3"), title="Tïtle", artist="Ärtist & Co"))
        self.assertEqual((info["title"], info["artist"], info["album"], info["genre"], info["year"]), ("Tïtle", "Ärtist & Co", "Album", "Acid", "1995"))
        self.assertTrue(info["cover"])
        self.assertAlmostEqual(info["secs"], 200 * 1152 / 44100, delta=0.05)
        self.assertEqual(info["kbps"], 128)
        self.assertEqual(library.read_cover(self.path("a.mp3")), (JPEG, "image/jpeg"))

    def test_id3v24_utf8_and_date(self):
        frames = v24_frame(b"TIT2", b"\x03" + "Tïtle".encode()) + v24_frame(b"TPE1", b"\x03A\x00B") + v24_frame(b"TDRC", b"\x032001-05-01")
        n = len(frames)
        head = b"ID3\x04\x00\x00" + bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127])
        with open(self.path("b.mp3"), "wb") as f:
            f.write(head + frames + FRAME * 10)
        info = library.read_song(self.path("b.mp3"))
        self.assertEqual((info["title"], info["artist"], info["year"]), ("Tïtle", "A & B", "2001"))

    def test_untagged_file_is_named_from_the_filename(self):
        with open(self.path("Some One - A Song.mp3"), "wb") as f:
            f.write(FRAME * 5)
        info = library.read_song(self.path("Some One - A Song.mp3"))
        self.assertEqual((info["artist"], info["title"], info["cover"]), ("Some One", "A Song", False))

    def test_junk_tags_count_as_missing(self):
        info = library.read_song(write_mp3(self.path("Show Me.mp3"), title="(Unknown)", artist="<unknown>", year="0000"))
        self.assertEqual((info["title"], info["artist"], info["year"]), ("Show Me", "", ""))

    def test_the_size_of_a_cover(self):
        png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (600).to_bytes(4, "big") + (500).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 9 + b"\xff\xc0\x00\x11\x08" + (300).to_bytes(2, "big") + (400).to_bytes(2, "big") + b"\x03" + b"\x00" * 9
        gif = b"GIF89a" + (32).to_bytes(2, "little") + (16).to_bytes(2, "little")
        self.assertEqual([library.image_size(x) for x in (png, jpeg, gif)], [(600, 500), (400, 300), (32, 16)])
        for junk in (JPEG, b"", None, b"\x89PNG", b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x00\x00\x00"):
            self.assertIsNone(library.image_size(junk), junk)

    def test_broken_file_is_still_listed(self):
        with open(self.path("broken.mp3"), "wb") as f:
            f.write(b"ID3\x03\x00\x00\x7f\x7f\x7f\x7f" + b"garbage")
        self.assertEqual(library.read_song(self.path("broken.mp3"))["title"], "broken")


class Helpers(unittest.TestCase):
    def test_parse_range(self):
        self.assertEqual(webapp.parse_range("bytes=0-99", 1000), (0, 99))
        self.assertEqual(webapp.parse_range("bytes=900-", 1000), (900, 999))
        self.assertEqual(webapp.parse_range("bytes=-100", 1000), (900, 999))
        self.assertEqual(webapp.parse_range("bytes=990-5000", 1000), (990, 999))
        self.assertEqual(webapp.parse_range("bytes=1000-", 1000), "bad")
        self.assertIsNone(webapp.parse_range(None, 1000))
        self.assertIsNone(webapp.parse_range("items=1-2", 1000))

    def test_genre_options(self):
        self.assertEqual(webapp.genre_options("Techno / Acid / Hardcore", "Techno"), ["Techno", "Acid", "Hardcore"])
        self.assertEqual(webapp.genre_options("Acid / Trance", "Goa"), ["Goa", "Acid", "Trance"])
        self.assertEqual(webapp.genre_options("the album lists Trance / Hip Hop / Progressive Trance, Hip Hop belongs to other tracks"), ["Trance", "Hip Hop", "Progressive Trance"])
        self.assertEqual(webapp.genre_options("just one"), [])

    def test_trust(self):
        self.assertEqual(webapp.trust_of(None), "unknown")
        self.assertEqual(webapp.trust_of(dict(notes="", discogs="https://www.discogs.com/release/1")), "verified")
        self.assertEqual(webapp.trust_of(dict(notes="", discogs="https://www.youtube.com/watch?v=x")), "source")
        self.assertEqual(webapp.trust_of(dict(notes="unverified: unofficial remix", discogs="https://www.discogs.com/release/1")), "unverified")


class Packs(unittest.TestCase):
    def test_the_example_is_a_pack(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples", "example.curator-pack.json")
        with open(path, encoding="utf-8") as f:
            pack = json.load(f)
        self.assertEqual((pack["curatorPack"], pack["name"]), (1, "Example pack"))
        self.assertTrue(set(pack) <= {"curatorPack", "name", "by", "about", "words", "genres", "settings"})

    def test_the_page_can_load_the_rules(self):
        self.assertEqual(webapp.STATIC["/packs.js"][0], "packs.js")
        self.assertTrue(os.path.exists(os.path.join(webapp.WEB, "packs.js")))


class HomeNetwork(unittest.TestCase):
    KEY = "abc123def0"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = lambda *a: os.path.join(self.tmp.name, *a)  # noqa: E731
        write_mp3(p("lib", "Acid", "Artist - Song.mp3"))
        os.makedirs(p("cache", "discogs"))
        self.collection = webapp.Collection(dict(library=p("lib"), review="", review_list="", inbox=[], log=p("log.csv"), cache=p("cache"), decisions=p("decisions.json")))
        self.collection.refresh()
        self.server = webapp.Server(("127.0.0.1", 0), webapp.Handler)
        self.port = self.server.server_address[1]
        self.lan_host = f"192.168.1.42:{self.port}"
        self.server.collection, self.server.hosts = self.collection, {f"127.0.0.1:{self.port}", self.lan_host}
        self.server.lan = dict(network=ipaddress.ip_network("192.168.1.0/24"), key=self.KEY)
        self.from_ip = "192.168.1.50"
        patcher = mock.patch.object(webapp.Handler, "_client_ip", lambda _: self.from_ip)
        patcher.start()
        self.addCleanup(patcher.stop)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def ask(self, method, path, body=None, headers=None, cookie=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body, headers={"Host": self.lan_host, **({"Cookie": f"{webapp.LAN_COOKIE}={self.KEY}"} if cookie else {}), **(headers or {})})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def post(self, route, body="{}", cookie=True):
        return self.ask("POST", route, body, {"Content-Type": "application/json"}, cookie)

    def song_id(self):
        return next(iter(self.collection.songs))

    def test_it_is_off_unless_asked_for(self):
        self.assertIsNone(webapp.Server.lan)

    def test_without_the_link_there_is_nothing(self):
        for path in ("/", "/app.js", "/api/all", f"/audio/{self.song_id()}", f"/cover/{self.song_id()}"):
            self.assertEqual(self.ask("GET", path, cookie=False)[0].status, 403, path)
        self.assertEqual(self.post("/api/played", json.dumps(dict(id=self.song_id())), cookie=False)[0].status, 403)

    def test_the_wrong_key_is_no_use(self):
        self.assertEqual(self.ask("GET", "/?key=nope", cookie=False)[0].status, 403)
        self.assertEqual(self.ask("GET", "/", headers={"Cookie": f"{webapp.LAN_COOKIE}=nope"}, cookie=False)[0].status, 403)

    def test_the_link_is_remembered_and_leaves_the_address_bar(self):
        r, _ = self.ask("GET", f"/?key={self.KEY}", cookie=False)
        self.assertEqual((r.status, r.getheader("Location")), (303, "/"))
        cookie = r.getheader("Set-Cookie")
        self.assertIn(f"{webapp.LAN_COOKIE}={self.KEY}", cookie)
        for part in ("HttpOnly", "SameSite=Strict"):
            self.assertIn(part, cookie)

    def test_with_it_everything_is_there(self):
        for path in ("/", "/api/all", f"/audio/{self.song_id()}"):
            self.assertIn(self.ask("GET", path)[0].status, (200, 206), path)
        self.assertEqual(self.post("/api/played", json.dumps(dict(id=self.song_id())))[0].status, 200)

    def test_another_network_is_refused_even_with_the_key(self):
        self.from_ip = "192.168.56.7"
        self.assertEqual(self.ask("GET", "/")[0].status, 403)
        self.assertEqual(self.ask("GET", f"/?key={self.KEY}", cookie=False)[0].status, 403)
        self.from_ip = "8.8.8.8"
        self.assertEqual(self.ask("GET", "/")[0].status, 403)

    def test_this_computer_needs_no_key(self):
        self.from_ip = "127.0.0.1"
        self.assertEqual(self.ask("GET", "/", cookie=False)[0].status, 200)

    def test_another_name_for_it_is_still_refused(self):
        self.assertEqual(self.ask("GET", "/", headers={"Host": "evil.example"})[0].status, 403)

    def test_a_form_on_another_site_gets_nowhere(self):
        r, _ = self.ask("POST", "/api/played", "{}", {"Content-Type": "application/json", "Origin": "http://evil.example"})
        self.assertEqual(r.status, 403)

    def test_folders_and_copying_stay_on_the_computer(self):
        for route in sorted(webapp.PC_ONLY):
            r, data = self.post(route)
            self.assertEqual(r.status, 403, route)
            self.assertIn("computer", json.loads(data)["error"])

    def test_the_computer_itself_can_still_do_those(self):
        self.from_ip = "127.0.0.1"
        self.assertNotEqual(self.post("/api/settings/check", json.dumps(dict(path=self.tmp.name)), cookie=False)[0].status, 403)

    def test_it_only_opens_on_a_private_address(self):
        class Probe:
            def __init__(self, address):
                self.address = address
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def connect(self, _):
                pass
            def getsockname(self):
                return (self.address, 0)
        for address, wanted in (("192.168.1.42", "192.168.1.42"), ("10.0.0.3", "10.0.0.3"), ("8.8.8.8", ""), ("127.0.0.1", ""), ("169.254.1.1", "")):
            with mock.patch.object(webapp.socket, "socket", lambda *a: Probe(address)):
                self.assertEqual(webapp.lan_address(), wanted, address)

    def test_the_key_stays_the_same_between_runs(self):
        with mock.patch.object(webapp.config, "env_file", lambda: os.path.join(self.tmp.name, ".env")):
            first = webapp.lan_key()
            self.assertRegex(first, r"^[0-9a-f]{10}$")
            self.assertEqual(webapp.lan_key(), first)


class App(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = self.tmp.name
        p = lambda *a: os.path.join(root, *a)  # noqa: E731
        self.song = write_mp3(p("lib", "Acid", "Artist - Song.mp3"))
        write_mp3(p("review", "Unknown One - Mystery.mp3"), title="Mystery", artist="Unknown One")
        write_mp3(p("inbox", "New One - Fresh.mp3"), title="Fresh", artist="New One")
        with open(p("log.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=tagwriter.LOG_FIELDS)
            w.writeheader()
            w.writerow(dict(date="2026-01-02T10:00:00", status="ok", src=p("inbox", "old name.mp3"), dest=self.song, artist="Artist", title="Song",
                            genre="Acid", discogs="https://www.discogs.com/release/1", notes="only version on the record; genre toss-up: Acid / Techno"))
        with open(p("list.md"), "w", encoding="utf-8") as f:
            f.write("# Review\n\n- **Unknown One - Mystery**: not on Discogs\n")
        os.makedirs(p("cache", "discogs"))
        self.collection = webapp.Collection(dict(library=p("lib"), review=p("review"), review_list=p("list.md"), inbox=[p("inbox")], log=p("log.csv"),
                                                 cache=p("cache"), decisions=p("decisions.json")))
        self.snap = self.collection.snapshot()
        self.by_title = {s["title"]: s for s in self.snap["songs"]}

    def serve(self):
        server = webapp.Server(("127.0.0.1", 0), webapp.Handler)
        port = server.server_address[1]
        server.collection, server.hosts = self.collection, {f"127.0.0.1:{port}"}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return port

    def request(self, port, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def test_joins_files_with_the_log_and_review_list(self):
        s = self.by_title["Song"]
        self.assertEqual((s["area"], s["trust"], s["was"]), ("library", "verified", "old name.mp3"))
        self.assertEqual(s["checks"], [dict(kind="genre", why="Discogs lists Acid, Techno. Which one fits?", options=["Acid", "Techno"])])
        self.assertEqual(self.by_title["Mystery"]["checks"], [dict(kind="waiting", why="not on Discogs")])
        self.assertEqual((self.by_title["Fresh"]["area"], self.by_title["Fresh"]["checks"]), ("inbox", []))
        self.assertNotIn("_path", s)
        self.assertNotIn(self.tmp.name, json.dumps(self.snap))  # full paths stay on the server

    def test_decisions_persist_and_undo(self):
        sid = self.by_title["Song"]["id"]
        self.assertIsNone(self.collection.decide(dict(id=sid, kind="genre", action="genre", genre="Techno")))
        again = webapp.Collection(self.collection.paths)
        self.assertEqual(again.decisions["genre:library/Acid/Artist - Song.mp3"]["genre"], "Techno")
        self.assertIsNone(self.collection.decide(dict(id=sid, kind="genre", action=None)))
        self.assertEqual(self.collection.decisions, {})
        self.assertEqual(self.collection.decide(dict(id=sid, kind="genre", action="delete-everything")), "bad action")
        self.assertEqual(self.collection.decide(dict(id="nope", kind="genre", action="keep")), "unknown song")

    def test_http_pages_audio_and_guards(self):
        port = self.serve()
        sid = self.by_title["Song"]["id"]
        r, body = self.request(port, "GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn(b"<title>Pressling</title>", body)
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))
        r, body = self.request(port, "GET", f"/audio/{sid}", headers={"Range": "bytes=0-9"})
        self.assertEqual((r.status, r.getheader("Content-Range"), len(body)), (206, f"bytes 0-9/{os.path.getsize(self.song)}", 10))
        r, body = self.request(port, "GET", f"/cover/{sid}")
        self.assertEqual((r.status, body), (200, JPEG))
        self.assertEqual(self.request(port, "GET", "/audio/000000000000")[0].status, 404)
        self.assertEqual(self.request(port, "GET", "/api/all", headers={"Host": "evil.example"})[0].status, 403)
        good = json.dumps(dict(id=sid, kind="genre", action="keep"))
        json_header = {"Content-Type": "application/json"}
        self.assertEqual(self.request(port, "POST", "/api/decision", good, {**json_header, "Origin": "https://evil.example"})[0].status, 403)
        self.assertEqual(self.request(port, "POST", "/api/decision", good, {"Content-Type": "text/plain"})[0].status, 415)
        self.assertEqual(self.request(port, "POST", "/api/decision", good, json_header)[0].status, 200)
        self.assertEqual(len(self.collection.decisions), 1)


class Extras(unittest.TestCase):
    """The app is a music player; the Curator and Spotify are extras. Until they're turned on or off in Settings, each is on only when there's something for it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(webapp.config.load, None)
        self.p = lambda *a: os.path.join(self.tmp.name, *a)
        self.settings = self.p("settings", "curator.toml")
        webapp.config.load(self.settings)
        write_mp3(self.p("lib", "Acid", "Artist - Song.mp3"))
        os.makedirs(self.p("cache", "discogs"))
        os.makedirs(self.p("downloads"))

    def collection(self, inbox=()):
        c = webapp.Collection(dict(library=self.p("lib"), review="", review_list="", inbox=list(inbox), log=self.p("log.csv"), cache=self.p("cache"), decisions=self.p("decisions.json")))
        c.refresh()
        return c

    def test_a_player_on_its_own_has_neither(self):
        self.assertEqual(webapp.extras_state(self.collection()), dict(curator=False, spotify=False))
        self.assertEqual(self.collection().snapshot()["meta"]["extras"], dict(curator=False, spotify=False))

    def test_a_downloads_folder_or_a_log_brings_the_curator(self):
        self.assertTrue(webapp.extras_state(self.collection([self.p("downloads")]))["curator"])
        with open(self.p("log.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=tagwriter.LOG_FIELDS)
            w.writeheader()
            w.writerow(dict(date="2026-01-02T10:00:00", status="ok", src="x.mp3", dest=self.p("lib", "Acid", "Artist - Song.mp3"), artist="Artist", title="Song"))
        self.assertTrue(webapp.extras_state(self.collection())["curator"])

    def test_spotify_once_it_is_set_up_or_was_checked(self):
        with open(self.p("spotify_playlists.json"), "w", encoding="utf-8") as f:
            json.dump(dict(p1=dict(name="Mine", snapshot_id="s", entries=[])), f)
        self.assertTrue(webapp.extras_state(self.collection())["spotify"])
        os.remove(self.p("spotify_playlists.json"))
        os.makedirs(os.path.dirname(self.settings))
        with open(self.settings, "w", encoding="utf-8") as f:
            f.write('[spotify]\nclient_id = "abc123"\n')
        webapp.config.load(self.settings)
        self.assertTrue(webapp.extras_state(self.collection())["spotify"])

    def test_a_choice_in_settings_stands(self):
        c = self.collection([self.p("downloads")])
        self.assertIsNone(webapp.save_extras(dict(curator=False)))
        self.assertEqual(webapp.extras_state(c), dict(curator=False, spotify=False))
        with open(self.settings, encoding="utf-8") as f:
            self.assertIn("[extras]\ncurator = false", f.read())
        self.assertIsNone(webapp.save_extras(dict(curator=True, spotify=True)))
        self.assertEqual(webapp.extras_state(self.collection()), dict(curator=True, spotify=True))

    def test_only_true_or_false_for_the_two_of_them(self):
        for body in ({}, dict(curator="no"), dict(curator=1), dict(curator=True, other=True)):
            self.assertEqual(webapp.save_extras(body), "bad request", body)
        self.assertFalse(os.path.exists(self.settings))

    def test_through_the_page_and_only_from_the_computer(self):
        self.assertIn("/api/settings/extras", webapp.PC_ONLY)
        server = webapp.Server(("127.0.0.1", 0), webapp.Handler)
        port = server.server_address[1]
        server.collection, server.hosts = self.collection([self.p("downloads")]), {f"127.0.0.1:{port}"}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("POST", "/api/settings/extras", body=json.dumps(dict(curator=False)), headers={"Content-Type": "application/json"})
        r = c.getresponse()
        self.assertEqual((r.status, json.loads(r.read())), (200, dict(extras=dict(curator=False, spotify=False))))
        c.close()


class SecondStart(unittest.TestCase):
    """Starting the app while it is already running shows the copy that is there, instead of starting a second one on the next port."""

    real_server = webapp.Server
    NO_PAGE = b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(webapp.config.load, None)
        self.settings = os.path.join(self.tmp.name, "settings", "curator.toml")
        webapp.config.load(self.settings)
        self.asked = []  # the addresses serve() asked to take, where a test notes them

    def has_the_port(self, answer, wait=0.0, drip=0.0, heard=None):
        """A program holding a port on this computer. It answers every call with `answer` after `wait` seconds (`answer` can be a function of the path asked for,
        `wait` a function of how many calls came so far; None for an answer takes the call and never answers), a byte every `drip` seconds if that is given,
        and notes what it was asked in `heard`. Returns its port."""
        over, calls = threading.Event(), []

        class Talk(socketserver.StreamRequestHandler):
            def handle(self):
                asked = line = self.rfile.readline()
                while heard is not None and line not in (b"\r\n", b"\n", b""):
                    line = self.rfile.readline()
                    asked += line
                if heard is not None:
                    heard.append(asked)
                calls.append(1)
                if answer is None:
                    over.wait(20)
                    return
                time.sleep(wait(len(calls)) if callable(wait) else wait)
                words = asked.split(b" ")
                reply = answer(words[1].decode("latin-1") if len(words) > 1 else "") if callable(answer) else answer
                try:
                    for i in range(len(reply) if drip else 0):
                        if over.wait(drip):
                            return
                        self.wfile.write(reply[i:i + 1])
                        self.wfile.flush()
                    if not drip:
                        self.wfile.write(reply)
                except OSError:  # the one who asked has hung up
                    pass

        there = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Talk)
        there.daemon_threads = True
        threading.Thread(target=there.serve_forever, args=(0.02,), daemon=True).start()  # (looking often whether to stop: the tests end sooner)
        self.addCleanup(there.server_close)
        self.addCleanup(there.shutdown)
        self.addCleanup(over.set)
        return there.server_address[1]

    @staticmethod
    def free_port():
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        return port

    @staticmethod
    def says(told):
        body = json.dumps(told).encode()
        return b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body

    def me(self, **more):
        """What a copy running with these same settings says about itself."""
        return dict(dict(app="pressling", config_file=os.path.abspath(self.settings)), **more)

    def older(self, config_file=None):
        """A copy from before the question existed: it only knows its settings page."""
        page = self.says(dict(reason="first", config_file=config_file or os.path.abspath(self.settings), picker=True, library="", inbox=[], review="", app_folder=""))
        return lambda path: page if path == "/api/settings" else self.NO_PAGE

    def on_this_computer_only(self, address, handler):
        """In place of webapp.Server: the address asked for is noted, and the port is taken on this computer only (the tests never open a port to the network)."""
        self.asked.append(address)
        return self.real_server(("127.0.0.1", address[1]), handler)

    def home(self):
        """As on a home network at 192.168.1.20, with nothing really opened to it and no call really made to it."""
        return mock.patch.multiple(webapp, lan_address=mock.Mock(return_value="192.168.1.20"), lan_key=mock.Mock(return_value="0123456789"),
                                   Server=self.on_this_computer_only, _taken=mock.Mock(return_value=False))

    def start(self, port, ask=20, **how):
        """serve(), with the browser never really opened, the library never really read and the server never really left running: what it returned, what it printed, and the addresses it opened.
        ask: how long whatever has the port is given to answer (long, so that a busy computer doesn't change what a test sees)."""
        opened, said = [], io.StringIO()
        with mock.patch.object(webapp.webbrowser, "open", opened.append), mock.patch.object(webapp.threading, "Timer"), mock.patch.object(webapp, "ASK_S", ask), \
                mock.patch.object(webapp, "Collection", side_effect=AssertionError("the library was read")), \
                mock.patch.object(self.real_server, "serve_forever", lambda self: None), contextlib.redirect_stdout(said):
            code = webapp.serve(port, **how)
        return code, said.getvalue(), opened

    # ---- who is on a port ----

    def test_the_same_app_with_the_same_settings_is_known(self):
        who, told = webapp.who_is_on(self.has_the_port(self.says(self.me(up=12.5, opens=True))), 20)
        self.assertEqual((who, told["up"], told["opens"]), ("same", 12.5, True))
        who, told = webapp.who_is_on(self.has_the_port(self.older()), 20)
        self.assertEqual((who, told.get("up"), told.get("opens")), ("same", None, None))

    def test_two_spellings_of_one_settings_file_are_the_same(self):
        folder = os.path.dirname(os.path.abspath(self.settings))
        spelled = os.path.join(folder, "..", os.path.basename(folder), "curator.toml")
        self.assertEqual(webapp._spelled(spelled), webapp._spelled(os.path.abspath(self.settings)))
        self.assertEqual(webapp.who_is_on(self.has_the_port(self.says(self.me(config_file=spelled))), 20)[0], "same")
        elsewhere = os.path.join(self.tmp.name, "seen-through-a-link", "curator.toml")  # the running copy also says what the system makes of its path
        self.assertEqual(webapp.who_is_on(self.has_the_port(self.says(self.me(config_file=elsewhere, config_real=os.path.abspath(self.settings)))), 20)[0], "same")

    @unittest.skipUnless(sys.platform == "win32", "only on Windows does the case of a path's letters count for nothing")
    def test_other_letter_case_is_the_same_file_on_windows(self):
        self.assertEqual(webapp.who_is_on(self.has_the_port(self.says(self.me(config_file=os.path.abspath(self.settings).swapcase()))), 20)[0], "same")

    def test_other_settings_or_another_program_is_not_it(self):
        elsewhere, deep = os.path.join(self.tmp.name, "other", "curator.toml"), b"[" * 60000
        for answer in (self.says(self.me(config_file=elsewhere)), self.older(elsewhere),
                       self.says(self.me(config_file="curator.toml")), self.older("curator.toml"),  # a path from its own folder: no telling which file that is
                       self.says(dict(hello="world")), self.says([1, 2]), self.says(dict(app="pressling", config_file=7)), self.says(dict(config_file=os.path.abspath(self.settings))),
                       self.NO_PAGE, b"HTTP/1.1 500 Oops\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", b"SSH-2.0-not-a-web-server\r\n", b"",
                       b"HTTP/1.1 200 OK\r\nContent-Length: 60000\r\nConnection: close\r\n\r\n" + deep,  # nested far deeper than anything can read
                       b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + b"x" * 200000):  # and far more than this app would ever say
            self.assertEqual(webapp.who_is_on(self.has_the_port(answer), 20), ("other", None), answer[:60] if isinstance(answer, bytes) else answer)

    def test_far_more_than_the_app_would_say_is_not_read_to_the_end(self):
        over = threading.Event()

        class Endless(socketserver.StreamRequestHandler):
            def handle(self):
                self.rfile.readline()
                try:
                    self.wfile.write(b"HTTP/1.1 200 OK\r\n\r\n")
                    while not over.is_set():
                        self.wfile.write(b"x" * 65536)
                except OSError:
                    pass

        there = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Endless)
        there.daemon_threads = True
        threading.Thread(target=there.serve_forever, args=(0.02,), daemon=True).start()
        self.addCleanup(there.server_close)
        self.addCleanup(there.shutdown)
        self.addCleanup(over.set)
        began = time.monotonic()
        self.assertEqual(webapp.who_is_on(there.server_address[1], 30), ("other", None))
        self.assertLess(time.monotonic() - began, 15)

    def test_a_path_that_is_only_told_is_not_looked_at_in_any_way(self):
        looked = []

        def watch(owner, name):
            real = getattr(owner, name)

            def watched(path, *more, **how):
                looked.append(os.fspath(path) if isinstance(path, (str, os.PathLike)) else path)
                return real(path, *more, **how)
            return mock.patch.object(owner, name, watched)

        told = os.path.join(self.tmp.name, "other", "curator.toml")  # (on Windows, looking a path up can mean calling another computer: here only one on this computer is told)
        port = self.has_the_port(self.says(self.me(config_file=told, config_real=told)))
        with watch(os, "stat"), watch(os, "lstat"), watch(os.path, "exists"), watch(os.path, "isfile"), watch(os.path, "realpath"), watch(os.path, "samefile"):
            self.assertEqual(webapp.who_is_on(port, 20), ("other", None))
        self.assertNotIn(told, looked)

    def test_a_port_nobody_listens_on(self):
        began = time.monotonic()
        self.assertEqual(webapp.who_is_on(self.free_port(), 30), ("none", None))
        self.assertLess(time.monotonic() - began, 5)  # found out at once, however long an answer would be waited for

    def test_one_that_takes_the_call_and_says_nothing_is_silent(self):
        self.assertEqual(webapp.who_is_on(self.has_the_port(None), 0.3), ("silent", None))
        slow = self.has_the_port(self.says(self.me()), wait=0.9)
        self.assertEqual(webapp.who_is_on(slow, 0.3), ("silent", None))  # too slow for a quick question
        self.assertEqual(webapp.who_is_on(slow, 20)[0], "same")  # and it is the app after all, when waited for

    def test_one_that_answers_a_little_at_a_time_is_not_waited_for_longer(self):
        began = time.monotonic()
        self.assertEqual(webapp.who_is_on(self.has_the_port(self.says(self.me()), drip=0.05), 0.5), ("silent", None))
        self.assertLess(time.monotonic() - began, 4)  # the wait is for the whole answer, not for each piece of it

    def test_one_cut_off_before_a_word_is_a_program_that_has_gone(self):
        class Line:
            """A call that is taken and then cut: after `said` has come, if anything."""
            def __init__(self, said):
                self.said = [said] if said else []

            def settimeout(self, seconds): pass
            def connect(self, where): pass
            def sendall(self, what): pass
            def close(self): pass

            def recv(self, most):
                if self.said:
                    return self.said.pop()
                raise ConnectionResetError("cut off")

        with mock.patch.object(webapp.socket, "socket", lambda *how: Line(b"")):
            self.assertEqual(webapp._ask(8765, "/api/running", 5), ("none", b""))  # (its port is looked at again, not left for the next one)
        with mock.patch.object(webapp.socket, "socket", lambda *how: Line(b"HTTP/1.1 200 OK\r\nContent-Le")):
            self.assertEqual(webapp._ask(8765, "/api/running", 5), ("other", b""))

    def test_the_question_says_nothing_about_the_person_and_goes_straight_there(self):
        heard = []
        port = self.has_the_port(self.says(self.me()), heard=heard)
        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9"}):
            self.assertEqual(webapp.who_is_on(port, 20)[0], "same")
        self.assertEqual(heard, [f"GET /api/running HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: application/json\r\nConnection: close\r\n\r\n".encode()])

    def test_held_knows_a_port_that_is_taken(self):
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))  # taken, and not even listening yet
        port = holder.getsockname()[1]
        self.assertTrue(webapp._held(port))
        holder.close()
        self.assertFalse(webapp._held(port))
        everywhere = socket.socket()
        everywhere.bind(("0.0.0.0", 0))  # taken for every address, as a copy started with --lan has it (never listening here: nothing is opened to the network)
        self.addCleanup(everywhere.close)
        self.assertTrue(webapp._held(everywhere.getsockname()[1]))

    # ---- what a running copy says ----

    def test_a_running_copy_says_which_it_is_and_how_long_it_has_been_up(self):
        server = webapp.Server(("127.0.0.1", 0), webapp.Handler)
        port = server.server_address[1]
        server.collection, server.hosts = None, {f"127.0.0.1:{port}"}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        mine = os.path.abspath(self.settings)
        who, told = webapp.who_is_on(port, 20)
        self.assertEqual((who, told), ("same", dict(app="pressling", config_file=mine, config_real=os.path.realpath(mine), starting=False, up=None, opens=False, lan=None)))
        server.began, server.opens, server.lan_at = time.monotonic() - 7, True, f"192.168.1.20:{port}"
        who, told = webapp.who_is_on(port, 20)
        self.assertEqual((who, told["opens"], told["lan"]), ("same", True, f"192.168.1.20:{port}"))
        self.assertTrue(6.5 < told["up"] < 30, told["up"])
        with mock.patch.object(webapp.os.path, "realpath", lambda path, **how: os.path.join(os.path.dirname(path), "seen-through-a-link.toml")):
            told = json.loads(webapp._ask(port, "/api/running", 20)[1])  # it also says what the system makes of its path
        self.assertEqual((told["config_file"], os.path.basename(told["config_real"])), (mine, "seen-through-a-link.toml"))
        with mock.patch.object(webapp.Handler, "_client_ip", lambda self: "192.168.1.9"):  # only this computer may ask
            self.assertEqual(webapp._ask(port, "/api/running", 20)[0], 404)

    def test_the_name_of_this_computer_is_not_looked_up(self):
        with mock.patch.object(webapp.socket, "getfqdn", side_effect=AssertionError("this computer's name was looked up")):  # (it can take a second or more, with the port held and not yet listening)
            webapp.Server(("127.0.0.1", 0), webapp.Handler).server_close()

    def test_a_copy_still_reading_its_library_says_who_it_is_while_everything_else_waits(self):
        port, reading, made, real = self.free_port(), threading.Event(), [], webapp.Server.serve_forever

        def a_long_read():
            reading.wait(30)
            return "first"

        def noted(server, *more):
            made.append(server)
            return real(server, *more)

        with mock.patch.object(webapp.folders, "reason", a_long_read), mock.patch.object(webapp.Server, "serve_forever", noted), contextlib.redirect_stdout(io.StringIO()):
            run = threading.Thread(target=webapp.serve, args=(port,), kwargs=dict(open_browser=False), daemon=True)
            run.start()
            try:
                until = time.monotonic() + 20
                while not made and time.monotonic() < until:
                    time.sleep(0.02)
                who, told = webapp.who_is_on(port, 20)
                self.assertEqual((who, told["starting"], told["up"], told["opens"]), ("same", True, None, False))
                self.assertEqual(webapp._ask(port, "/", 0.5), ("silent", b""))  # the page itself waits for the library
                reading.set()
                self.assertEqual(webapp._ask(port, "/", 20)[0], 200)
                who, told = webapp.who_is_on(port, 20)
                self.assertEqual((who, told["starting"]), ("same", False))
                self.assertIsNotNone(told["up"])
            finally:
                reading.set()
                if made:
                    made[0].shutdown()
                run.join(20)
        self.assertFalse(run.is_alive())
        webapp.Server(("127.0.0.1", port), webapp.Handler).server_close()  # and the next start can take the port at once. (On Linux and macOS the calls just made
        # leave the port looking held for about a minute after it is let go; a start sees through that: nobody answers there, so it takes the port.)

    def test_while_the_library_is_read_the_network_gets_nothing_without_the_link(self):
        server = webapp.Server(("127.0.0.1", 0), webapp.Handler)
        port = server.server_address[1]
        server.collection, server.hosts, server.ready = None, {f"127.0.0.1:{port}", f"192.168.1.20:{port}"}, threading.Event()
        server.lan = dict(network=ipaddress.ip_network("192.168.1.0/24"), key="0123456789")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.ready.set)

        def call(method, path, key=False, host=None, wait=20):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=wait)
            try:
                c.request(method, path, body="{}" if method == "POST" else None,
                          headers={"Host": host or f"192.168.1.20:{port}", "Content-Type": "application/json", **({"Cookie": f"{webapp.LAN_COOKIE}=0123456789"} if key else {})})
                return c.getresponse().status
            except TimeoutError:
                return "waits"
            finally:
                c.close()

        with mock.patch.object(webapp.Handler, "_client_ip", lambda self: "192.168.1.9"):
            for method, path in (("GET", "/"), ("HEAD", "/"), ("GET", "/api/all"), ("POST", "/api/played"), ("POST", "/api/settings"), ("GET", "/api/running")):
                self.assertEqual(call(method, path), 403, (method, path))  # refused at once: it never waits for the library
            self.assertEqual(call("GET", "/api/running", key=True), 404)  # with the link: still only this computer may ask
            self.assertEqual(call("POST", "/api/played", key=True, wait=0.7), "waits")
            self.assertEqual(call("GET", "/api/all", key=True, wait=0.7), "waits")
        self.assertEqual(call("GET", "/api/running", host="evil.example"), 403)  # this computer, under another site's name
        self.assertEqual(call("POST", "/api/played", host=f"127.0.0.1:{port}", wait=0.7), "waits")  # this computer waits too

    def test_the_port_is_taken_before_the_library_is_read_and_given_back_if_that_fails(self):
        port, held = self.free_port(), []

        def no_library():
            held.append(webapp._held(port))  # by now the port is this copy's
            raise RuntimeError("the library can't be read")

        with mock.patch.object(webapp.folders, "reason", no_library), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            webapp.serve(port, open_browser=False)
        self.assertEqual(held, [True])
        self.assertFalse(webapp._held(port))

    def test_ctrl_c_leaves_quietly_and_gives_the_port_back(self):
        port, said = self.free_port(), io.StringIO()
        with mock.patch.object(webapp.folders, "reason", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(said):  # while the library is read
            self.assertIsNone(webapp.serve(port, open_browser=False))
        self.assertEqual(said.getvalue(), "")
        self.assertFalse(webapp._held(port))
        with mock.patch.object(webapp, "_held", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(said):  # and while it is still looking for a port
            self.assertIsNone(webapp.serve(port, open_browser=False))
        self.assertEqual(said.getvalue(), "")

    def test_what_goes_wrong_while_answering_ends_the_run(self):
        def broken(server):
            raise RuntimeError("the answering broke")

        port = self.free_port()
        with mock.patch.object(webapp.Server, "serve_forever", broken), mock.patch.object(webapp.threading, "Timer"), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
            webapp.serve(port, open_browser=False)
        self.assertFalse(webapp._held(port))

    def test_it_notes_when_it_was_ready_and_whether_it_opens_the_browser(self):
        for opens in (True, False):
            made, said = [], io.StringIO()
            with mock.patch.object(webapp.webbrowser, "open"), mock.patch.object(webapp.threading, "Timer") as later, \
                    mock.patch.object(webapp.Server, "serve_forever", lambda server: made.append(server)), contextlib.redirect_stdout(said):
                self.assertIsNone(webapp.serve(self.free_port(), open_browser=opens))
            server = made[0]
            self.assertEqual((server.opens, server.ready.is_set(), later.called), (opens, True, opens))
            self.assertIsNotNone(server.began)
            self.assertIn("to choose your folders", said.getvalue())

    # ---- a second start ----

    def test_a_second_start_opens_the_copy_that_is_running(self):
        port = self.has_the_port(self.says(self.me(up=300.0, opens=True, starting=False, lan=None)))
        code, said, opened = self.start(port)
        self.assertEqual((code, opened), (0, [f"http://127.0.0.1:{port}/"]))
        self.assertIn(f"already running at http://127.0.0.1:{port}/", said)
        self.assertNotIn("Reading your library", said)
        self.assertNotIn("home network", said)

    def test_a_port_held_for_every_address_is_asked_about_and_never_shared(self):
        port = self.has_the_port(self.says(self.me(up=300.0, opens=True)))  # as a copy started with --lan is: on Windows this computer's own address on its port can still be taken beside it
        with mock.patch.object(webapp, "Server") as take:
            code, said, opened = self.start(port)
        self.assertEqual((code, opened, take.called), (0, [f"http://127.0.0.1:{port}/"], False))

    def test_not_asked_for_the_home_network_it_takes_this_computers_own_address_only(self):
        with mock.patch.object(webapp, "Server", self.on_this_computer_only):
            self.start(self.free_port(), open_browser=False)
        self.assertEqual([address[0] for address in self.asked], ["127.0.0.1"])

    def test_an_older_copy_is_opened_too(self):
        port = self.has_the_port(self.older())
        self.assertEqual(self.start(port)[::2], (0, [f"http://127.0.0.1:{port}/"]))

    def test_no_second_tab_for_a_copy_that_opens_one_itself(self):
        port = self.has_the_port(self.says(self.me(up=0.2, opens=True)))
        self.assertEqual(self.start(port)[::2], (0, []))
        port = self.has_the_port(self.says(self.me(up=0.2, opens=False)))  # it has only just started, but without the browser: this start opens it
        self.assertEqual(self.start(port)[::2], (0, [f"http://127.0.0.1:{port}/"]))
        port = self.has_the_port(self.says(self.me(up=True, opens=True)))  # nonsense for a time is not "just now"
        self.assertEqual(self.start(port)[::2], (0, [f"http://127.0.0.1:{port}/"]))

    def test_a_copy_that_is_still_starting_is_not_started_again(self):
        port = self.has_the_port(self.says(self.me(starting=True, up=None, opens=True)))
        code, said, opened = self.start(port)
        self.assertEqual((code, opened), (0, []))  # (it opens the browser itself when it is ready)
        self.assertEqual(said.splitlines(), [f"Pressling is already starting at http://127.0.0.1:{port}/", "It opens in your browser by itself once it has read your library."])
        port = self.has_the_port(self.says(self.me(starting=True, up=None, opens=False)))  # started without the browser: this start opens it, and the page waits there
        code, said, opened = self.start(port)
        self.assertEqual((code, opened), (0, [f"http://127.0.0.1:{port}/"]))
        self.assertEqual(said.splitlines(), [f"Pressling is already starting at http://127.0.0.1:{port}/", "The page appears once it has read your library."])
        self.assertEqual(self.start(port, open_browser=False)[1].splitlines(), [f"Pressling is already starting at http://127.0.0.1:{port}/"])

    def test_no_browser_when_asked_not_to(self):
        port = self.has_the_port(self.says(self.me(up=300.0, opens=True)))
        code, said, opened = self.start(port, open_browser=False)
        self.assertEqual((code, opened), (0, []))
        self.assertIn("already running", said)

    def test_a_copy_too_busy_to_answer_at_once_is_asked_again(self):
        heard = []
        port = self.has_the_port(self.says(self.me(up=300.0, opens=True)), wait=lambda calls: 1.0 if calls == 1 else 0, heard=heard)
        code, said, opened = self.start(port, ask=0.3)
        self.assertEqual((code, opened, len(heard)), (0, [f"http://127.0.0.1:{port}/"], 2))

    def test_another_program_on_the_port_does_not_stop_it(self):
        port = self.has_the_port(self.says(dict(hello="world")))
        code, said, opened = self.start(port, open_browser=False)
        self.assertIsNone(code)
        self.assertNotIn("already", said)
        self.assertRegex(said, r"Open http://127\.0\.0\.1:\d+/ to choose your folders")
        self.assertNotIn(f":{port}/", said)

    def test_a_program_that_never_answers_is_asked_three_times_and_no_more(self):
        heard = []
        port = self.has_the_port(None, heard=heard)
        began = time.monotonic()
        code, said, opened = self.start(port, ask=0.3, open_browser=False)
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual((code, len(heard)), (None, 3))
        self.assertNotIn("already", said)
        self.assertRegex(said, r"Open http://127\.0\.0\.1:\d+/ to choose your folders")

    def test_a_port_let_go_a_moment_later_is_still_taken(self):
        holder = socket.socket()
        holder.bind(("127.0.0.1", 0))  # held and not listening, as a copy in its very first moment is
        port = holder.getsockname()[1]
        threading.Timer(0.25, holder.close).start()
        self.assertIn(f"http://127.0.0.1:{port}/", self.start(port, open_browser=False)[1])

    def test_a_port_the_system_keeps_for_itself_is_passed_by_at_once(self):
        port, tried = self.free_port(), []

        def kept_back(address, handler):
            tried.append(address[1])
            if address[1] == port:
                raise PermissionError("the system keeps this port")
            return self.real_server(address, handler)

        with mock.patch.object(webapp, "Server", kept_back):
            said = self.start(port, open_browser=False)[1]
        self.assertEqual(tried.count(port), 1)  # (not tried again and again: twenty such ports in a row would take a long while)
        self.assertNotIn(f":{port}/", said)
        self.assertRegex(said, r"Open http://127\.0\.0\.1:\d+/ to choose your folders")

    def test_the_ports_there_are(self):
        with mock.patch.object(webapp, "_held", return_value=True), mock.patch.object(webapp, "who_is_on", return_value=("other", None)), \
                mock.patch.object(webapp, "Server", side_effect=AssertionError("a port was taken")), self.assertRaises(SystemExit) as stopped:
            webapp.serve(65530, open_browser=False)
        self.assertEqual(str(stopped.exception), "No free port between 65530 and 65535.")
        for nonsense in (0, -1, 70000):
            with mock.patch.object(webapp, "Server", side_effect=AssertionError("a port was taken")), self.assertRaises(SystemExit) as stopped:
                webapp.serve(nonsense, open_browser=False)
            self.assertEqual(str(stopped.exception), "A port is a number from 1 to 65535.")

    # ---- asked to open to the home network ----

    def test_asked_for_the_home_network_the_key_is_asked_for_from_the_very_first_answer(self):
        seen, port = [], self.free_port()

        def noted(server):
            seen.append((server.lan and str(server.lan["network"]), server.lan and server.lan["key"], sorted(server.hosts), server.lan_at))

        with self.home(), mock.patch.object(self.real_server, "serve_forever", noted), mock.patch.object(webapp.threading, "Timer"), contextlib.redirect_stdout(io.StringIO()):
            webapp.serve(port, open_browser=False, lan=True)
        self.assertEqual(seen, [("192.168.1.0/24", "0123456789", sorted([f"127.0.0.1:{port}", f"localhost:{port}", f"192.168.1.20:{port}"]), f"192.168.1.20:{port}")])
        self.assertEqual(self.asked, [("0.0.0.0", port)])

    def test_asked_for_the_home_network_a_copy_that_is_open_to_it_gives_the_link_again(self):
        port = self.has_the_port(lambda path: self.says(self.me(up=300.0, opens=True, lan=f"192.168.1.20:{port}")))
        with self.home():
            code, said, opened = self.start(port, lan=True)
        self.assertEqual((code, opened, self.asked), (0, [f"http://127.0.0.1:{port}/"], []))
        self.assertIn(f"On your home network: http://192.168.1.20:{port}/?key=0123456789", said)

    def test_the_link_is_only_printed_for_this_computers_own_address(self):
        for elsewhere in ("8.8.8.8:80", "3232235777:80", "192.168.1.20:80", "192.168.1.21:{port}", "evil.example/x?"):
            port = self.has_the_port(lambda path: self.says(self.me(up=300.0, opens=True, lan=elsewhere.replace("{port}", str(port)))))
            with self.home():
                said = self.start(port, lan=True)[1]
            self.assertNotIn("key=", said, elsewhere)
            self.assertNotIn(elsewhere.split(":")[0].split("/")[0], said.replace("127.0.0.1", ""), elsewhere)  # what the other program says is never printed
            self.assertIn("open to another network", said, elsewhere)

    def test_asked_for_the_home_network_a_copy_that_is_not_open_to_it_says_what_to_do(self):
        port = self.has_the_port(self.says(self.me(up=300.0, opens=True, lan=None)))
        with self.home():
            code, said, opened = self.start(port, lan=True)
        self.assertEqual((code, opened), (0, [f"http://127.0.0.1:{port}/"]))
        self.assertIn("That copy isn't open to your home network", said)
        self.assertNotIn("key=", said)
        port = self.has_the_port(self.older())  # an older copy can't say whether it is
        with self.home():
            said = self.start(port, lan=True)[1]
        self.assertIn("If that copy isn't open to your home network", said)

    def test_asked_for_the_home_network_it_never_shares_a_port_with_another_program(self):
        port = self.has_the_port(self.says(dict(hello="world")))
        with self.home():
            code, said, opened = self.start(port, lan=True, open_browser=False)
        self.assertIsNone(code)
        self.assertEqual({address[0] for address in self.asked}, {"0.0.0.0"})
        self.assertNotIn(port, [address[1] for address in self.asked])  # this computer's own address on that port is in use: the wider one is left alone too
        self.assertIn(f"On your home network: http://192.168.1.20:{self.asked[-1][1]}/?key=0123456789", said)  # (the port after it may be in use too on a busy computer: the last one asked is the one it took)

    def test_asked_for_the_home_network_a_port_taken_on_the_networks_address_only_is_left_alone(self):
        port = self.free_port()
        with self.home(), mock.patch.object(webapp, "_held", lambda candidate: candidate == port), mock.patch.object(webapp, "_taken", lambda host, candidate: (host, candidate) == ("192.168.1.20", port)):
            code, said, opened = self.start(port, lan=True, open_browser=False)
        self.assertNotIn(port, [address[1] for address in self.asked])
        self.assertTrue(self.asked)
        self.assertIn(f":{self.asked[-1][1]}/", said)  # it opened on a port of its own (the next one may be in use too on a busy computer)


class Command(unittest.TestCase):
    def test_it_says_which_version_it_is(self):
        from music_curator import __version__, cli
        said = io.StringIO()
        with contextlib.redirect_stdout(said), self.assertRaises(SystemExit) as done:
            cli.pressling(["--version"])
        self.assertEqual((done.exception.code, said.getvalue().strip()), (0, f"Pressling {__version__}"))

    def test_the_help_says_pressling(self):
        from music_curator import cli
        said = io.StringIO()
        with contextlib.redirect_stdout(said), self.assertRaises(SystemExit):
            cli.pressling(["--help"])
        self.assertTrue(said.getvalue().startswith("usage: pressling "), said.getvalue()[:40])

    def test_the_two_places_that_hold_the_version_agree(self):
        import tomllib
        from music_curator import __version__
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pyproject.toml"), "rb") as f:
            self.assertEqual(tomllib.load(f)["project"]["version"], __version__)  # a release needs both


if __name__ == "__main__":
    unittest.main()
