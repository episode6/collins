# SPDX-License-Identifier: MIT
# Copyright (c) 2026 episode6, Inc.
"""The fake's box: drawn as the appendix of docs/api/sessions.md describes it."""

import importlib.util
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(os.path.dirname(HERE), "fake_claude.py")

spec = importlib.util.spec_from_file_location("fake_claude", FAKE)
fake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake)


class Drawn:
    """What the fake writes, captured."""

    def __init__(self):
        self.chunks = []

    def __call__(self, text):
        self.chunks.append(text if isinstance(text, str) else text.decode("utf-8", "replace"))

    @property
    def text(self):
        return "".join(self.chunks)


class BoxDrawingTests(unittest.TestCase):
    def setUp(self):
        self.drawn = Drawn()
        self.saved = fake.write
        fake.write = self.drawn
        f = fake.Fake.__new__(fake.Fake)
        f.screen = fake.Screen.__new__(fake.Screen)
        f.screen.cols, f.screen.rows, f.screen.out_row = 120, 40, 1
        f.box = fake.Box()
        f.hint = fake.STARTUP_HINT
        f.permission_mode, f.effort = "default", "high"
        f.mode_note = ""
        self.fake = f

    def tearDown(self):
        fake.write = self.saved

    def test_an_empty_box_is_two_rules_a_marked_row_and_the_cursor_in_the_third_column(self):
        self.fake.draw_box()
        text = self.drawn.text
        self.assertEqual(text.count("─" * 120), 2, "a full-width rule above and below")
        self.assertIn("❯ ", text, "the mark and a no-break space")
        self.assertNotIn("❯ ", text, "never the mark and an ordinary space")
        # The cursor ends in the third column of the prompt row.
        last_move = re.findall(r"\x1b\[(\d+);(\d+)H", text)[-1]
        self.assertEqual(last_move[1], "3")
        self.assertEqual(int(last_move[0]), 40 - 1 - 1)  # the prompt row: above the lower rule

    def test_the_hint_is_dim_and_drawn_in_pieces_with_cursor_movement_between_the_words(self):
        self.fake.draw_box()
        text = self.drawn.text
        hint = text[text.index("\x1b[2m") : text.index("\x1b[0m", text.index("\x1b[2m"))]
        self.assertIn("Try", hint)
        self.assertIn("\x1b[1C", hint, "a cursor movement between words")
        self.assertNotIn(" ", hint, "no space written: the movement stands in for it")
        self.assertTrue(fake.STARTUP_HINT.startswith('Try "'))

    def test_text_in_the_box_shows_no_hint_and_the_cursor_follows_it(self):
        self.fake.box.insert("hello")
        self.fake.draw_box()
        text = self.drawn.text
        self.assertIn("❯ hello", text)
        self.assertNotIn("\x1b[2m", text)
        last_move = re.findall(r"\x1b\[(\d+);(\d+)H", text)[-1]
        self.assertEqual(last_move[1], str(3 + 5))

    def test_the_box_is_columns_minus_four_wide_and_wraps_a_word_whole_onto_a_two_space_row(self):
        words = ("word " * 30).strip()  # 149 characters: past one row of 116
        self.fake.box.insert(words)
        self.fake.draw_box()
        text = self.drawn.text
        rows = fake.wrap_box(words, 116)
        self.assertEqual(len(rows), 2)
        first = words[rows[0][0] : rows[0][1]]
        self.assertLessEqual(len(first), 116)
        self.assertTrue(first.endswith("word"), "a word is moved down whole")
        # Two prompt rows above the lower rule (row 39): 37 and 38.
        self.assertIn("\x1b[38;1H  " + words[rows[1][0] : rows[1][1]], text)

    def test_a_typed_line_break_ends_a_row(self):
        rows = fake.wrap_box("one\ntwo", 116)
        self.assertEqual(rows, [(0, 3), (4, 7)])

    def test_a_word_longer_than_the_row_fills_it_before_breaking(self):
        rows = fake.wrap_box("x" * 200, 116)
        self.assertEqual(rows, [(0, 116), (116, 200)])


class PasteTests(unittest.TestCase):
    def test_a_paste_of_800_characters_or_two_breaks_lands_as_text(self):
        self.assertIsNone(fake.fold_paste("a" * 800, 1))
        self.assertIsNone(fake.fold_paste("a\nb\nc", 1))

    def test_a_longer_paste_or_three_breaks_is_a_stand_in(self):
        self.assertEqual(fake.fold_paste("a" * 801, 1), "[Pasted text #1]")
        self.assertEqual(fake.fold_paste("a\nb\r\nc\rd", 2), "[Pasted text #2 +3 lines]")

    def test_a_backspace_after_a_stand_in_removes_the_whole_of_it(self):
        box = fake.Box()
        box.insert("x ")
        box.paste("a" * 900)
        self.assertEqual(box.text, "x [Pasted text #1]")
        box.backspace()
        self.assertEqual(box.text, "x ")

    def test_the_editing_keys(self):
        box = fake.Box()
        box.insert("hello world")
        box.home()
        self.assertEqual(box.cursor, 0)
        box.row_end(116)
        self.assertEqual(box.cursor, 11)
        box.backspace()
        self.assertEqual(box.text, "hello worl")


class TranscriptPathTests(unittest.TestCase):
    def test_the_directory_name_replaces_every_character_but_letters_and_digits(self):
        self.assertEqual(fake.encode_dir("/home/me/src/app"), "-home-me-src-app")
        self.assertEqual(fake.encode_dir("/a.b/c_d"), "-a-b-c-d")


if __name__ == "__main__":
    unittest.main()
