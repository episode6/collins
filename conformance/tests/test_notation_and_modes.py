# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
"""The page's notation and the expected-modes reading."""

import json
import os
import unittest

from conformance.kit import modes, notation

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class NotationTests(unittest.TestCase):
    def test_render(self):
        self.assertEqual(notation.render(notation.parse("CSI ?61;1;21;22;28c"), {}), b"\x1b[?61;1;21;22;28c")
        self.assertEqual(notation.render(notation.parse("DCS !|7E565445 ST"), {}), b"\x1bP!|7E565445\x1b\\")
        self.assertEqual(
            notation.render(notation.parse("OSC 4 ; <i> ; ? ST"), {"i": "9"}), b"\x1b]4;9;?\x1b\\"
        )
        self.assertEqual(notation.render(notation.parse("DCS $q SP q ST"), {}), b"\x1bP$q q\x1b\\")
        self.assertEqual(notation.render(notation.parse("ENQ"), {}), b"\x05")

    def test_pattern_fills_known_values_and_classes_the_rest(self):
        pattern = notation.pattern(notation.parse("CSI ?<n>;<s>$y"), {"n": "2004"})
        self.assertTrue(pattern.fullmatch(b"\x1b[?2004;2$y"))
        self.assertFalse(pattern.fullmatch(b"\x1b[?2005;2$y"))
        self.assertFalse(pattern.fullmatch(b"\x1b[?2004;7$y"))
        pattern = notation.pattern(notation.parse("CSI 4;<height>;<width>t"), {})
        match = pattern.fullmatch(b"\x1b[4;720;1080t")
        self.assertEqual(match.group("height"), b"720")

    def test_placeholders_and_errors(self):
        self.assertEqual(
            notation.placeholders(notation.parse("OSC 10;rgb:<rrrr>/<gggg>/<bbbb> ST")),
            ["rrrr", "gggg", "bbbb"],
        )
        for bad in ("CSI <nope> c", "", "OSC ü"):
            with self.assertRaises(notation.NotationError):
                notation.parse(bad)
        with self.assertRaises(notation.NotationError):
            notation.render(notation.parse("CSI <n> $ p"), {})

    def test_describe(self):
        self.assertEqual(notation.describe(b"\x1b[?61c"), "CSI ?61c")
        self.assertEqual(notation.describe(b""), "(nothing)")

    def test_the_shipped_query_table_parses(self):
        with open(os.path.join(DATA, "queries.json"), encoding="utf-8") as handle:
            table = json.load(handle)
        for row in table["answered"]:
            query, reply = notation.parse(row["query"]), notation.parse(row["reply"])
            self.assertTrue(set(notation.placeholders(query)) <= set(notation.placeholders(reply)), row)
        for section in ("silent", "passed"):
            for row in table[section]:
                notation.render(notation.parse(row["query"]), {})


class ModeTests(unittest.TestCase):
    DECRQM = {"private": {"7": 1, "25": 1, "1004": 2, "2004": 2, "6": 2, "1049": 2}, "ansi": {"4": 2}}

    def expected(self, data: bytes) -> set:
        return modes.expected_modes(data, self.DECRQM)

    def test_a_mode_set_off_its_default_is_listed_and_one_at_its_default_is_not(self):
        self.assertEqual(self.expected(b"\x1b[?2004h\x1b[?1004h\x1b[?7h"), {"?2004h", "?1004h"})
        self.assertEqual(self.expected(b"\x1b[?7l"), {"?7l"})
        self.assertEqual(self.expected(b"\x1b[?2004h\x1b[?2004l"), set())
        self.assertEqual(self.expected(b"\x1b[4h"), {"4h"})

    def test_the_modes_the_redraw_handles_itself_are_left_out(self):
        self.assertEqual(self.expected(b"\x1b[?25l\x1b[?1049h\x1b[?3h\x1b[?1048h"), set())

    def test_a_mode_vte_does_not_know_is_listed_only_when_set(self):
        self.assertEqual(self.expected(b"\x1b[?4242h"), {"?4242h"})
        self.assertEqual(self.expected(b"\x1b[?4242l"), set())

    def test_ris_clears_and_decstr_resets_the_modes(self):
        self.assertEqual(self.expected(b"\x1b[?2004h\x1bc"), set())
        self.assertEqual(self.expected(b"\x1b[?2004h\x1b[!p"), set())

    def test_the_kitty_stack_and_modify_other_keys(self):
        self.assertEqual(self.expected(b"\x1b[>5u"), {">5u"})
        self.assertEqual(self.expected(b"\x1b[>5u\x1b[<u"), set())
        self.assertEqual(self.expected(b"\x1b[=1;1u"), {"=1u"})
        self.assertEqual(self.expected(b"\x1b[>5u\x1b[=3u"), {">3u"})
        self.assertEqual(self.expected(b"\x1b[>4;2m"), {">4;2m"})
        self.assertEqual(self.expected(b"\x1b[>5u\x1b[>1u"), {">5u", ">1u"})

    def test_the_region_top_for_a_cursor_report_in_origin_mode(self):
        tracker = modes.ModeTracker(self.DECRQM)
        tracker.feed(b"\x1b[5;10r\x1b[?6h")
        self.assertEqual(tracker.region_top, 5)
        self.assertTrue(tracker.private.get(6))
        tracker.feed(b"\x1b[r")
        self.assertEqual(tracker.region_top, 1)


if __name__ == "__main__":
    unittest.main()
