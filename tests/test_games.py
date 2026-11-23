"""Game and film songs: Curate reads what the sources say and leaves a card on To check that says how the song would go in. It never adds one
by itself, a track's credit only comes from a source that lists that track, and Wikipedia only gives the game's name, year and cover.
Nothing here reaches the network: Wikipedia, Discogs and YouTube are stand-ins."""
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _no_real_settings  # noqa: E402,E702,F401  first of all: keeps the tests away from your own settings, music and token
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from music_curator import curate, games, library, ost, webapp  # noqa: E402
from test_curate import KEY, LENGTH, WithDiscogs  # noqa: E402
from test_webapp import JPEG  # noqa: E402

NFS_BOX = """{{Infobox video game
| title = Need for Speed II
| image = NFS II (PC, US) cover art.jpg
| composer = [[Saki Kaskas]]<br />[[Jeff van Dyck]]<br />Alistair Hirst
| released = '''PlayStation'''<br />{{Video game release|NA|March 31, 1997<ref>{{Cite magazine |date=1999-03-02 |title=x}}</ref>|EU|1997}}
| genre = [[Racing game|Racing]]
}}
'''''Need for Speed II''''' is a 1997 racing video game."""
SERIES = "{{Infobox video game series\n| first release = Doom\n}}"
DOOM_1993 = "{{Infobox video game\n| released = December 10, 1993\n}}"
DOOM_2016 = "{{Infobox video game\n| released = {{Video game release|WW|May 13, 2016}}\n}}"
SOUNDTRACK = "Excessive Speed! The Music from Need For Speed II"
NFS_SONG = "Need for Speed II Soundtrack - Main Menu"


class FakeWikipedia:
    """Answers like Wikipedia's API for the pages it's given, {title: (wikitext, image)}: a search finds the pages that share a word with it."""

    def __init__(self, pages, redirects=None):
        self.pages, self.redirects, self.asked = dict(pages), dict(redirects or {}), []

    def __call__(self, params, cache=""):
        self.asked.append(params)
        if params.get("list") == "search":
            words = set(params["srsearch"].lower().replace("video game", " ").split())
            hits = [t for t in self.pages if words & set(t.lower().replace("(", " ").replace(")", " ").split())]
            return {"query": {"search": [{"title": t} for t in hits]}}
        pages, steps = [], []
        for title in params["titles"].split("|"):
            if title in self.redirects:
                steps.append({"from": title, "to": self.redirects[title]})
                title = self.redirects[title]
            if title not in self.pages:
                pages.append({"title": title, "missing": True})
                continue
            text, image = self.pages[title]
            pages.append({"title": title, "fullurl": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
                          "revisions": [{"slots": {"main": {"content": text}}}], **({"original": {"source": image + "?utm_source=x"}} if image else {})})
        return {"query": {"pages": pages, **({"redirects": steps} if steps else {})}}


class ReadingTheName(unittest.TestCase):
    def test_the_side_with_an_ost_word_is_the_game(self):
        cases = {
            NFS_SONG: ("Need for Speed II", "Main Menu", None),
            "Main Menu - Need for Speed II OST": ("Need for Speed II", "Main Menu", None),
            "[OST] Chrono Trigger - Corridors of Time": ("Chrono Trigger", "Corridors of Time", None),
            "Main Menu (Need for Speed II OST)": ("Need for Speed II", "Main Menu", None),
            "Crash Bandicoot 2 OST: Snow Go": ("Crash Bandicoot 2", "Snow Go", None),
            "Final Fantasy VII OST - 01 - The Prelude": ("Final Fantasy VII", "The Prelude", None),
            "Sonic Adventure Original Game Soundtrack - Open Your Heart (Main Theme)": ("Sonic Adventure", "Open Your Heart (Main Theme)", None),
            "OST - Need for Speed II - Main Menu": ("Need for Speed II", "Main Menu", None),
        }
        for name, want in cases.items():
            with self.subTest(name):
                self.assertEqual(games.split(name), want)

    def test_platforms_and_quality_go_and_a_year_is_a_hint(self):
        self.assertEqual(games.split("Doom (1993) OST - At Doom's Gate"), ("Doom", "At Doom's Gate", 1993))
        self.assertEqual(games.split("Chrono Trigger OST (SNES) - Corridors of Time [HQ]"), ("Chrono Trigger", "Corridors of Time", None))
        self.assertEqual(games.split("Unreal Tournament 1999 OST - Run"), ("Unreal Tournament 1999", "Run", 1999))

    def test_with_the_game_you_said_the_rest_is_the_track(self):
        self.assertEqual(games.split("Jet Stream from n.Gen Racing", "n.Gen Racing"), ("n.Gen Racing", "Jet Stream", None))
        self.assertEqual(games.split("Jet Stream from NGEN Racing", "n.Gen Racing"), ("n.Gen Racing", "Jet Stream", None))
        self.assertEqual(games.split("Unreal Tournament 1999 - Run (Impulse Tracker)", "Unreal Tournament 1999"),
                         ("Unreal Tournament 1999", "Run (Impulse Tracker)", 1999))
        self.assertEqual(games.split("Doom OST - At Doom's Gate", "Doom 1993"), ("Doom 1993", "At Doom's Gate", 1993))  # "Doom" is in the name, the year isn't

    def test_a_name_that_doesnt_say_is_none(self):
        self.assertIsNone(games.split("Rom Di Prisco - Menu"))
        self.assertIsNone(games.split("n.Gen Racing", "n.Gen Racing"))  # nothing left to be the track
        self.assertIsNone(games.split("Need for Speed II OST"))


class ReadingWikipedia(unittest.TestCase):
    def test_the_info_box_says_what_the_page_is_about_and_its_first_year(self):
        kind, fields = games.infobox(NFS_BOX)
        self.assertEqual(kind, "video game")
        self.assertEqual(games.first_year(fields["released"]), 1997)  # not 1999, the date of a source quoted in it
        self.assertIn("Saki Kaskas", fields["composer"])

    def test_a_series_a_person_or_a_page_with_no_box_is_not_a_game(self):
        for text in (SERIES, "{{Infobox person\n| name = X\n}}", "Just text."):
            with self.subTest(text):
                self.assertIsNone(games._page({"title": "X", "revisions": [{"slots": {"main": {"content": text}}}]}))

    def ask(self, pages, game, year=None):
        with mock.patch.object(games, "_wiki", FakeWikipedia(pages)):
            return games.wikipedia(game, year)

    def test_the_games_page_is_found_and_one_with_another_number_isnt(self):
        page = self.ask({"Need for Speed II": (NFS_BOX, "https://upload.wikimedia.org/nfs2.jpg"), "The Need for Speed": ("{{Infobox video game\n| released = 1994\n}}", "")},
                        "Need for Speed II")
        self.assertEqual((page["title"], page["year"], page["kind"]), ("Need for Speed II", 1997, "game"))
        self.assertEqual((page["url"], page["image"]), ("https://en.wikipedia.org/wiki/Need_for_Speed_II", "https://upload.wikimedia.org/nfs2.jpg"))
        self.assertIsNone(self.ask({"Halo 2": ("{{Infobox video game\n| released = 2004\n}}", "")}, "Halo 3"))

    def test_spaces_and_dots_in_a_name_dont_matter(self):
        page = self.ask({"NGEN Racing": ("{{Infobox video game\n| released = 2000\n}}", "")}, "n.Gen Racing")
        self.assertEqual((page["title"], page["year"]), ("NGEN Racing", 2000))

    def test_with_two_games_of_one_name_and_no_year_it_asks(self):
        doom = {"Doom (franchise)": (SERIES, ""), "Doom (1993 video game)": (DOOM_1993, ""), "Doom (2016 video game)": (DOOM_2016, "")}
        page = self.ask(doom, "Doom")
        self.assertEqual(sorted([page["year"]] + [p["year"] for p in page["others"]]), [1993, 2016])
        self.assertEqual(self.ask(doom, "Doom", 2016)["year"], 2016)
        self.assertEqual(self.ask(doom, "Doom 1993", 1993)["title"], "Doom")
        card = games.propose("Doom", "At Doom's Gate", None, {}, page, None)
        self.assertEqual(card["options"], [])
        self.assertIn("More than one game is called Doom (1993, 2016)", card["why"])

    def test_a_name_wikipedia_leads_on_from_is_the_game_it_leads_to(self):
        fake = FakeWikipedia({"Need for Speed: High Stakes": ("{{Infobox video game\n| released = March 1999\n}}", "")}, {"Need for Speed IV": "Need for Speed: High Stakes"})
        with mock.patch.object(games, "_wiki", fake):
            page = games.wikipedia("Need for Speed IV")
        self.assertEqual((page["title"], page["year"]), ("Need for Speed: High Stakes", 1999))

    def test_a_film_is_found_as_a_film(self):
        page = self.ask({"Interstellar (film)": ("{{Infobox film\n| released = {{Film date|2014|10|26}}\n}}", "")}, "Interstellar")
        self.assertEqual((page["title"], page["kind"], page["year"]), ("Interstellar", "film", 2014))


class TheCard(unittest.TestCase):
    NFS = dict(title="Need for Speed II", page="Need for Speed II", kind="game", year=1997, url="https://en.wikipedia.org/wiki/Need_for_Speed_II",
               image="https://upload.wikimedia.org/nfs2.jpg")
    DISC = dict(status="ready", release=1, release_title=SOUNDTRACK, discogs="https://www.discogs.com/master/9", artist="Rom Di Prisco", title="Menu", dur_diff=1,
                year=1997, cover_url="https://i.discogs.com/1.jpg", reasons=[], notes=[])

    def ways_in(self, card):
        return [(o["artist"], o["title"]) for o in card["options"]]

    def test_a_credit_on_discogs_at_your_length_comes_first(self):
        card = games.propose("Need for Speed II", "Main Menu", None, self.DISC, self.NFS, None)
        self.assertEqual(self.ways_in(card), [("Rom Di Prisco", "Menu"), ("Need for Speed II OST", "Main Menu")])
        self.assertEqual((card["album"], card["year"], card["genre"], card["folder"]), ("Need for Speed II", 1997, "Video Game Music", "Game OST/Need for Speed II"))
        self.assertEqual((card["cover_url"], card["discogs"]), (self.DISC["cover_url"], self.DISC["discogs"]))
        self.assertIn("credited to Rom Di Prisco, and its length matches yours", card["why"])
        self.assertEqual(card["options"][0]["note"], "credited on Discogs")

    def test_wikipedias_composers_never_make_a_credit(self):
        card = games.propose("Need for Speed II", "Main Menu", None, {}, self.NFS, None)
        self.assertEqual(self.ways_in(card), [("Need for Speed II OST", "Main Menu")])
        self.assertIn("Nothing confirms who made this track", card["why"])
        self.assertEqual((card["cover_url"], card["discogs"]), (self.NFS["image"], self.NFS["url"]))

    def test_a_track_of_another_length_is_no_credit(self):
        card = games.propose("Need for Speed II", "Main Menu", None, dict(self.DISC, dur_diff=40), self.NFS, None)
        self.assertEqual(self.ways_in(card), [("Need for Speed II OST", "Main Menu")])
        self.assertIn("so it may be another version", card["why"])

    def test_a_soundtrack_that_credits_nobody_is_no_credit(self):
        card = games.propose("Need for Speed II", "Main Menu", None, dict(self.DISC, artist="Various"), self.NFS, None)
        self.assertEqual(len(card["options"]), 1)
        self.assertIn("credits nobody for it", card["why"])

    def test_discogs_and_the_official_upload_agreeing_are_one_way_in(self):
        upload = dict(artist="Rom Di Prisco", title="Menu", album="Need for Speed II (Original Soundtrack)", link="https://www.youtube.com/watch?v=abcdefghijk", year=1997)
        card = games.propose("Need for Speed II", "Main Menu", None, self.DISC, self.NFS, upload)
        self.assertEqual(len(card["options"]), 2)
        self.assertEqual(card["options"][0]["note"], "credited on Discogs and in the official upload")

    def test_without_a_page_the_name_and_year_come_from_the_file(self):
        card = games.propose("n.Gen Racing", "Jet Stream", 2000, {}, None, None)
        self.assertEqual((card["album"], card["year"], card["folder"], card["cover_url"]), ("n.Gen Racing", 2000, "Game OST/n.Gen Racing", ""))
        self.assertIn("Wikipedia has no page that fits", card["why"])

    def test_a_film_goes_with_the_films(self):
        card = games.propose("Interstellar", "Cornfield Chase", None, {}, dict(self.NFS, title="Interstellar", kind="film", year=2014), None)
        self.assertEqual((card["genre"], card["folder"]), ("Soundtrack", "Movie & TV OST/Interstellar"))

    def test_one_already_in_your_library_says_so(self):
        card = games.propose("Need for Speed II", "Main Menu", None, dict(self.DISC, status="dup", reasons=["already in library: X/Rom Di Prisco - Menu.mp3"]), self.NFS, None)
        self.assertEqual((card["options"], card["why"]), ([], "Already in your library: Rom Di Prisco - Menu.mp3"))


class OfficialUploads(unittest.TestCase):
    INFO = dict(id="abcdefghijk", channel="Jonathan Colling - Topic", duration=250,
                description="Provided to YouTube by Label\n\nJet Stream · Jonathan Colling\n\nn.Gen Racing (Original Soundtrack)\n\n℗ 2000 Label\n\nReleased on: 2020-01-01\n\nAuto-generated by YouTube.")

    def test_its_credit_counts_on_the_games_album_at_your_length(self):
        hit = games.provided(self.INFO, ["n.Gen Racing"], "Jet Stream", 251)
        self.assertEqual((hit["artist"], hit["title"], hit["year"], hit["link"]), ("Jonathan Colling", "Jet Stream", 2000, "https://www.youtube.com/watch?v=abcdefghijk"))

    def test_not_on_another_album_at_another_length_or_from_another_channel(self):
        other = self.INFO["description"].replace("n.Gen Racing (Original Soundtrack)", "Greatest Hits")
        self.assertIsNone(games.provided(dict(self.INFO, description=other), ["n.Gen Racing"], "Jet Stream", 251))
        self.assertIsNone(games.provided(self.INFO, ["n.Gen Racing"], "Jet Stream", 300))
        self.assertIsNone(games.provided(dict(self.INFO, channel="Somebody"), ["n.Gen Racing"], "Jet Stream", 251))
        self.assertIsNone(games.provided(self.INFO, ["n.Gen Racing"], "Another Track", 250))

    def test_its_own_title_can_name_the_game(self):  # the composer's own release, on an album of their own
        info = dict(self.INFO, description=self.INFO["description"].replace("Jet Stream · ", "Jet Stream from n.Gen Racing · ").replace("n.Gen Racing (Original Soundtrack)", "N.Gen Techno"))
        hit = games.provided(info, ["n.Gen Racing"], "Jet Stream", 250)
        self.assertEqual((hit["artist"], hit["title"], hit["album"]), ("Jonathan Colling", "Jet Stream from n.Gen Racing", "N.Gen Techno"))

    def test_the_video_a_download_came_from_is_asked_first(self):
        answers = lambda args, cache="", stopping=None: self.INFO if args[0] == "info" else []  # noqa: E731
        with mock.patch.object(games, "_can_ask_youtube", return_value=True), mock.patch.object(games, "_source_video", return_value="abcdefghijk"), \
                mock.patch.object(games, "_yt", side_effect=answers) as yt:
            hit = games.official_upload("x.mp3", ["n.Gen Racing"], "Jet Stream", 250)
        self.assertEqual(hit["artist"], "Jonathan Colling")
        self.assertEqual([c.args[0] for c in yt.call_args_list], [["info", "abcdefghijk"]])  # no search needed

    def test_without_yt_dlp_nothing_is_asked(self):
        with mock.patch.object(games, "_can_ask_youtube", return_value=False), mock.patch.object(games, "_yt") as yt:
            self.assertIsNone(games.official_upload("x.mp3", ["n.Gen Racing"], "Jet Stream", 250))
        yt.assert_not_called()


class LooseTrackNames(WithDiscogs):
    def look(self, length, loose):
        src = self.file(NFS_SONG)
        self.fake.add("Rom Di Prisco", SOUNDTRACK, [("22", "Menu", length), ("23", "Race", "3:00")])
        with mock.patch.object(curate.discogs, "search", self.fake.everything):
            return ost.resolve(dict(key="1", src=src, game="Need for Speed II", title="Main Menu"), {}, {}, loose=loose)

    def test_menu_is_main_menu_when_the_length_agrees(self):
        found = self.look(LENGTH, loose=True)
        self.assertEqual((found["status"], found["artist"], found["title"]), ("ready", "Rom Di Prisco", "Menu"))
        self.assertIn('listed on Discogs as "Menu"', found["notes"])

    def test_not_without_loose(self):
        self.assertEqual(self.look(LENGTH, loose=False)["status"], "notfound")

    def resolve(self, search=None):
        src = self.file(NFS_SONG) if not os.path.exists(os.path.join(self.inbox, NFS_SONG + ".mp3")) else os.path.join(self.inbox, NFS_SONG + ".mp3")
        with mock.patch.object(curate.discogs, "search", search or self.fake.everything):
            return ost.resolve(dict(key="1", src=src, game="Need for Speed II", title="Main Menu"), {}, {}, loose=True)

    def test_the_soundtrack_is_searched_for_by_the_games_name_alone_too(self):
        self.fake.add("Rom Di Prisco", SOUNDTRACK, [("22", "Menu", LENGTH)])
        only_by_name = lambda **kw: self.fake.everything(**kw) if set(kw) == {"release_title"} else []  # noqa: E731  as for a track listed under another name
        self.assertEqual(self.resolve(only_by_name)["artist"], "Rom Di Prisco")

    def test_a_tracks_own_composer_is_its_credit_on_a_various_artists_soundtrack(self):
        rid = self.fake.add("Various", SOUNDTRACK, [("22", "Main Menu", LENGTH)])
        self.fake.releases[rid]["tracklist"][0]["extraartists"] = [dict(name="Rom Di Prisco", role="Created By")]
        self.assertEqual((self.resolve()["artist"], self.resolve()["status"]), ("Rom Di Prisco", "ready"))

    def test_or_the_one_person_the_whole_soundtrack_credits(self):
        rid = self.fake.add("Various", SOUNDTRACK, [("22", "Main Menu", LENGTH)])
        self.fake.releases[rid]["extraartists"] = [dict(name="Rom Di Prisco", role="Composed By", tracks="")]
        self.assertEqual(self.resolve()["artist"], "Rom Di Prisco")
        self.fake.releases[rid]["extraartists"].append(dict(name="Saki Kaskas", role="Composed By", tracks=""))
        self.assertEqual(self.resolve()["status"], "review")  # two people: nothing says which made this one

    def test_a_near_name_with_no_length_is_only_a_maybe(self):  # "Menu" on the real Need for Speed II soundtrack has no length
        self.fake.add("Rom Di Prisco", SOUNDTRACK, [("22", "Menu", "")])
        found = self.resolve()
        self.assertEqual((found["status"], found["maybe"]), ("notfound", dict(title="Menu", release_title=SOUNDTRACK, artist="Rom Di Prisco")))
        card = games.propose("Need for Speed II", "Main Menu", None, found, None, None)
        self.assertEqual([o["artist"] for o in card["options"]], ["Need for Speed II OST"])
        self.assertIn('"Menu" by Rom Di Prisco', card["why"])

    def test_a_pressing_with_no_length_doesnt_count_when_another_pressing_gives_one(self):  # the real Unreal Tournament remix album
        for length in ("", "3:23"):  # (the one with no length first: the length is said all the same)
            rid = self.fake.add("Remixers", "Need for Speed II Remixed", [("E3", "Main Menu", length)], year=2025)
            self.fake.releases[rid]["master_id"] = 77
        same_record = lambda **kw: [dict(r, master_id=self.fake.releases[r["id"]]["master_id"]) for r in self.fake.everything(**kw)]  # noqa: E731
        found = self.resolve(same_record)
        self.assertGreater(abs(found["dur_diff"]), ost.NEAR_S)  # said as another version, not taken for this one

    def test_a_record_with_this_tracks_length_beats_an_older_one_with_another(self):
        self.fake.add("Remixer", "Need for Speed II Remixed", [("1", "Main Menu", "6:00")], year=1990)
        self.fake.add("Rom Di Prisco", SOUNDTRACK, [("22", "Main Menu", LENGTH)], year=1997)
        found = self.resolve()
        self.assertEqual((found["release_title"], abs(found["dur_diff"]) <= ost.NEAR_S), (SOUNDTRACK, True))

    def test_not_at_another_length(self):
        self.assertEqual(self.look("4:00", loose=True)["status"], "notfound")


class InCurate(WithDiscogs):
    def setUp(self):
        super().setUp()
        self.wiki, self.images = FakeWikipedia({"Need for Speed II": (NFS_BOX, "https://upload.wikimedia.org/nfs2.jpg")}), []
        for target, name, value in ((games, "_wiki", self.wiki), (games, "_can_ask_youtube", lambda: False), (curate.discogs, "search", self.fake.everything),
                                    (games, "image", lambda url: self.images.append(url) or JPEG)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def card(self, name=NFS_SONG):
        return self.waiting()[KEY.format(name)]

    def test_a_game_song_waits_on_to_check_with_the_ways_it_can_go_in(self):
        self.file(NFS_SONG)
        self.fake.add("Rom Di Prisco", SOUNDTRACK, [("22", "Menu", LENGTH)])
        _, done = self.run_it()
        self.assertEqual((done["added"], done["waiting"], self.in_library()), (0, 1, []))
        card = self.card()
        self.assertEqual([o["id"] for o in card["options"]], ["game|Rom Di Prisco|Menu", "game|Need for Speed II OST|Main Menu"])
        self.assertEqual({k: card["record"][k] for k in ("folder", "genre", "album", "year", "game")},
                         dict(folder="Game OST/Need for Speed II", genre="Video Game Music", album="Need for Speed II", year=1997, game="Need for Speed II"))

    def test_picking_one_adds_it_into_the_games_folder(self):
        self.file(NFS_SONG)
        self.fake.add("Rom Di Prisco", SOUNDTRACK, [("22", "Menu", LENGTH)])
        self.run_it()
        self.answer_for(NFS_SONG, "pick", "game|Rom Di Prisco|Menu")
        _, done = self.run_it()
        self.assertEqual(done["added"], 1)
        self.assertEqual(self.in_library(), ["Game OST/Need for Speed II/Rom Di Prisco - Menu.mp3"])
        written = library.read_song(os.path.join(self.lib, "Game OST", "Need for Speed II", "Rom Di Prisco - Menu.mp3"))
        self.assertEqual((written["artist"], written["title"], written["album"], written["year"], written["genre"]),
                         ("Rom Di Prisco", "Menu", "Need for Speed II", "1997", "Video Game Music"))
        self.assertTrue(written["cover"])
        row = self.log_rows()[-1]
        self.assertTrue(row["notes"].startswith("game song: "), row["notes"])
        self.assertIn(curate.PICK_NOTE, row["notes"])
        self.assertEqual(self.saved(), {}, "the answer was carried out")

    def test_with_nothing_confirming_it_the_rule_names_it_after_the_game(self):
        self.file(NFS_SONG)
        self.run_it()
        self.assertEqual([o["id"] for o in self.card()["options"]], ["game|Need for Speed II OST|Main Menu"])
        self.answer_for(NFS_SONG, "pick", "game|Need for Speed II OST|Main Menu")
        self.run_it()
        self.assertEqual(self.in_library(), ["Game OST/Need for Speed II/Need for Speed II OST - Main Menu.mp3"])
        self.assertEqual(self.images, ["https://upload.wikimedia.org/nfs2.jpg"])  # the game's cover, with no soundtrack on Discogs

    def test_naming_it_on_its_card_puts_it_in_where_the_card_says(self):
        self.file(NFS_SONG)
        self.run_it()
        self.answer_for(NFS_SONG, "identify", "Rom Di Prisco - Menu")
        self.run_it()
        self.assertEqual(self.in_library(), ["Game OST/Need for Speed II/Rom Di Prisco - Menu.mp3"])

    def test_saying_which_game_looks_it_up_as_music_from_it_and_is_remembered(self):
        name = "Jet Stream from n.Gen Racing"
        self.file(name)
        self.wiki.pages["NGEN Racing"] = ("{{Infobox video game\n| released = 2000\n}}", "")
        self.run_it()  # an ordinary song at first: not found
        self.answer_for(name, "game", "n.Gen Racing")
        self.run_it()
        card = self.card(name)
        self.assertEqual((card["said"], card["record"]["folder"]), ("game: n.Gen Racing", "Game OST/NGEN Racing"))
        self.assertEqual([o["id"] for o in card["options"]], ["game|NGEN Racing OST|Jet Stream"])
        self.assertEqual(self.saved(), {}, "the answer was carried out, and what it said stays with the song")
        self.run_it()
        self.assertEqual(self.card(name)["record"]["folder"], "Game OST/NGEN Racing")

    def test_two_games_of_one_name_ask_which(self):
        self.wiki.pages.update({"Doom (1993 video game)": (DOOM_1993, ""), "Doom (2016 video game)": (DOOM_2016, "")})
        name = "Doom OST - At Doom's Gate"
        self.file(name)
        self.run_it()
        self.assertNotIn("options", self.card(name))
        self.assertIn("More than one game is called Doom", self.card(name)["why"])
        self.answer_for(name, "game", "Doom 1993")
        self.run_it()
        self.assertEqual((self.card(name)["record"]["year"], self.card(name)["record"]["folder"]), (1993, "Game OST/Doom"))

    def test_wikipedia_out_of_reach_is_asked_again_next_time(self):
        self.file(NFS_SONG)
        with mock.patch.object(games, "_wiki", side_effect=OSError("no network")):
            self.run_it()
        self.assertTrue(self.card().get("retry"))
        self.assertIn("Wikipedia couldn't be reached", self.card()["why"])

    def test_the_page_shows_the_card(self):
        self.file(NFS_SONG)
        self.fake.add("Rom Di Prisco", SOUNDTRACK, [("22", "Menu", LENGTH)])
        self.run_it()
        check = next(c for c in self.page_song(NFS_SONG + ".mp3")["checks"] if c["kind"] == "waiting")
        self.assertEqual(check["game"], dict(name="Need for Speed II", year="1997", folder="Game OST/Need for Speed II", genre="Video Game Music", kind="game",
                                             wiki="https://en.wikipedia.org/wiki/Need_for_Speed_II"))
        self.assertEqual(check["options"][0]["note"], "credited on Discogs")
        self.assertIn("Rom Di Prisco", check["options"][0]["why"])
        self.assertIn("game", webapp.ACTIONS)

    def test_the_command_prints_the_card(self):
        src = self.file(NFS_SONG)
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch.object(curate.discogs, "CACHE_DIR", curate.discogs.CACHE_DIR):  # (the command sets it: put back after)
            games.main([src, "--cache", os.path.join(self.root, "cache")])
        self.assertEqual(json.loads(out.getvalue())["folder"], "Game OST/Need for Speed II")


if __name__ == "__main__":
    unittest.main()
