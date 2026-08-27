# New in the ghackett fork of agent-session-manager (GPL-3.0).

"""What a key press means inside the composer's text box, what opens one,
and what a new session opens it as.

The composer sends on Enter by default, with a setting that swaps sending to
Ctrl+Enter (leaving bare Enter a newline, for people who write prompts like
GitHub comments). Shift+Enter is a newline in both modes -- it is the
terminal's own "literal newline" chord, so it must never send. Everything
about that decision is a pure function of the key, its modifiers and the
setting, so it lives here where the tests can reach it: CI has no GTK
typelibs, which is also why the keyvals and modifier bits below are spelled
as integers instead of Gdk constants (they are ABI, fixed by X11's keysymdef
and GDK's ModifierType, not values that drift).

The composer_new_sessions setting is here for the same reason: its three
words are shared by the preference row that writes them and the tab that
acts on them, and reading one back is likewise pure. So are the two rules
for the draft stash -- what a close keeps when it can't type the text back
into the CLI's box, and when a reopening composer is seeded with it -- and
the rules for how a closing composer's text is typed back so that the CLI
shows it in full rather than folding it into a "[Pasted text #1 +12 lines]"
stand-in, and for reading such a stand-in back out when it does.
"""

from __future__ import annotations

import re

# GDK_KEY_Return, GDK_KEY_KP_Enter, GDK_KEY_ISO_Enter.
_RETURN_KEYVALS = frozenset({0xFF0D, 0xFF8D, 0xFE34})

# Gdk.ModifierType bit positions.
_SHIFT_MASK = 1 << 0
_CONTROL_MASK = 1 << 2
_ALT_MASK = 1 << 3  # Mod1
_SUPER_MASK = 1 << 26
_HYPER_MASK = 1 << 27
_META_MASK = 1 << 28

SEND = "send"
NEWLINE = "newline"
PASS = "pass"


def enter_action(keyval: int, state: int, enter_sends: bool) -> str:
    """``"send"``, ``"newline"`` or ``"pass"`` for a composer key press.

    Only Return-family keyvals answer anything but ``"pass"``. Shift+Enter
    is always a newline; with *enter_sends* a bare Enter sends and
    Ctrl+Enter is a newline escape hatch, without it those two swap.
    """
    if keyval not in _RETURN_KEYVALS:
        return PASS
    shift = bool(state & _SHIFT_MASK)
    ctrl = bool(state & _CONTROL_MASK)
    if shift:
        return NEWLINE
    if enter_sends:
        return NEWLINE if ctrl else SEND
    return SEND if ctrl else NEWLINE


# Modifiers that make a key a chord rather than a character. Shift and the
# lock/level bits are deliberately absent: Shift is how a capital arrives,
# and AltGr (Mod2..Mod5, by layout) is how half of Europe types one.
_CHORD_MASK = _CONTROL_MASK | _ALT_MASK | _SUPER_MASK | _HYPER_MASK | _META_MASK

# First characters the CLI's own input box hears as a mode switch, not as
# text: Claude Code opens its slash-command menu on "/", bash mode on "!",
# memory mode on "#" and its file picker on "@", each with completion the
# composer has no answer for. Typed into an empty box they belong to the
# box, so the composer leaves them there -- an opener is exactly the
# keystroke whose menu the user is after. Only as the first character:
# once the composer is up they are ordinary text in it, and a mention
# written there parses out of the submitted text just as well.
_PROMPT_OPENERS = frozenset("/!#@")


def typing_opens_composer(char: str, state: int) -> bool:
    """Whether typing *char* should raise the composer and take it along.

    The composer_on_typing setting's half of the decision that can be made
    from the keyboard alone (the other half is the screen: only an empty
    agent input box is ever typed away from -- see
    TerminalTab._typing_opens_composer). *char* is the character the key
    would produce, "" for a key that produces none.

    A character is anything the user could have meant as text: printable,
    unmodified but for Shift and the level shifts a layout needs. Chords
    belong to the terminal, and so do the box's own openers.
    """
    if len(char) != 1 or char < " " or char == "\x7f":
        return False
    if state & _CHORD_MASK:
        return False
    return char not in _PROMPT_OPENERS


# What a session Collins starts fresh opens its composer as (the
# composer_new_sessions setting; see TerminalTab.autoshow_composer).
OFF = "off"
FLOAT = "float"  # raised over the agent terminal, as Ctrl+. does
DOCK = "dock"  # its own panel page below the terminal
AUTOSHOW_MODES = (OFF, FLOAT, DOCK)


def autoshow_mode(setting) -> str:
    """The composer a new session should open with, read off a saved setting.

    Anything unrecognized -- a hand-edited settings file, a value some later
    Collins wrote and this one doesn't know -- reads as ``OFF``. Showing the
    composer is the opt-in half of this setting, so an answer we can't read
    must never conjure one.
    """
    return setting if setting in AUTOSHOW_MODES else OFF


def restore_text(text: str) -> str:
    """The composer text as it should be typed back into the CLI's box.

    Interior newlines ride along fine -- the text goes back as pastes, and a
    paste's newlines are line breaks in the box (see `paste_pieces`) -- but
    trailing ones are dropped: they would leave the cursor on an empty
    continuation row, one stray Enter away from submitting what the user
    chose to put back rather than send. Carriage returns are newlines too:
    a bare one typed into the box is an Enter.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


# The CLI folds a paste it deems large into a stand-in -- "[Pasted text #3
# +12 lines]" in its input box, the text itself kept aside and put back only
# when the prompt is sent -- and the rule for "large" (Claude Code 2.1.247,
# read out of its input handling) is more than 800 characters or more than
# two line breaks. A stand-in is what a closing composer's draft turned into
# whenever it was typed back as one chunk, and it is unreadable from the
# screen: the next open cut the stand-in, and the draft behind it was gone.
# So the text goes back as *pieces*, each its own bracketed paste and each
# under both limits, all in one write (the CLI reads every bracketed paste as
# its own event however the bytes arrive, so no timing is involved). The
# character cap is in code points rather than the UTF-16 units the CLI
# counts, and set so that a piece of nothing but astral characters (two
# units each) still sits a fifth under the CLI's limit rather than on it.
_PIECE_CHARS = 320
_PIECE_NEWLINES = 2
# A paste ending in "[I" or "[O" loses those two characters: the CLI trims
# what it takes for a focus-report tail off every paste it receives.
_PASTE_TAILS = ("[I", "[O")


def paste_pieces(text: str) -> list[str]:
    """Split *text* into the pastes that put it back in the CLI's box
    verbatim -- each under the stand-in limits, in order, concatenating to
    the whole text. A piece ends short of a newline that would be its
    third, so the next one opens with the break; a line longer than the
    cap is simply cut, and a cut never lands on a trailing "[I" / "[O".
    """
    pieces: list[str] = []
    i, n = 0, len(text)
    while i < n:
        end = min(n, i + _PIECE_CHARS)
        newlines, j = 0, i
        while (k := text.find("\n", j, end)) >= 0:
            newlines += 1
            if newlines > _PIECE_NEWLINES:
                end = k
                break
            j = k + 1
        if text[i:end].endswith(_PASTE_TAILS):
            end -= 1
        pieces.append(text[i:end])
        i = end
    return pieces


# Every stand-in the CLI's box can show in place of content -- pasted text,
# a pasted image or audio clip, a truncated paste -- as the CLI itself
# matches them, with room for the whitespace a screen read can add where a
# long line wraps. Only the pasted-text one is ever Collins's own doing.
_STAND_IN_RE = re.compile(
    r"\[\s*(?:"
    r"Pasted\s+text\s+#(?P<id>\d+)(?:\s+\+(?P<lines>\d+)\s+lines)?"
    r"|Image\s+#\d+"
    r"|Audio\s+#\d+"
    r"|\.\.\.\s*Truncated\s+text\s+#\d+\s+\+\d+\s+lines\s*\.\.\."
    r")\s*\]"
)


def _stand_in(match: re.Match) -> str:
    """The stand-in *match* names, spelled the one way the CLI draws it --
    the key a screen read and a paste-back record meet on."""
    if match.group("id") is None:
        return re.sub(r"\s+", " ", match.group()).replace("[ ", "[").replace(" ]", "]")
    lines = match.group("lines")
    suffix = f" +{int(lines)} lines" if lines else ""
    return f"[Pasted text #{int(match.group('id'))}{suffix}]"


def _skip_space(text: str, i: int) -> int:
    while i < len(text) and text[i].isspace():
        i += 1
    return i


def _match_ignoring_space(text: str, i: int, piece: str) -> int:
    """Where *piece* ends in *text* when matched from *i* with all
    whitespace on both sides ignored, or -1 when it isn't there. A screen
    read is exact about everything but spacing: it drops the spaces a row
    ends in and guesses at the ones a wrap ate, and the CLI itself widens
    a pasted tab to spaces."""
    for ch in piece:
        if ch.isspace():
            continue
        i = _skip_space(text, i)
        if i >= len(text) or text[i] != ch:
            return -1
        i += 1
    return i


def pasted_back(screen: str, pieces: list[str]) -> dict[str, str] | None:
    """How the *pieces* a close pasted back landed, read off *screen* (the
    box as `entered_prompt` reads it): each stand-in the CLI folded one
    into, keyed by the stand-in as drawn, mapped to the piece's own text.
    An empty dict means every piece is showing in full. None means the
    screen doesn't start with the pieces at all -- the box held something
    else, or the read is broken -- and nothing about it is known.

    A stand-in claims the piece it stands for by position and line count
    (the CLI's "+N lines" is the number of breaks in what was pasted);
    anything typed after the pieces is left unread.
    """
    record: dict[str, str] = {}
    pos = 0
    for piece in pieces:
        match = _STAND_IN_RE.match(screen, _skip_space(screen, pos))
        if match is not None and match.group("id") is not None:
            folded = int(match.group("lines") or 0)
            if folded == piece.count("\n"):
                record[_stand_in(match)] = piece
                pos = match.end()
                continue
        pos = _match_ignoring_space(screen, pos, piece)
        if pos < 0:
            return None
    return record


def expand_pasted_back(text: str, record: dict[str, str]) -> str | None:
    """*text* (a box read) with every stand-in Collins put there replaced by
    what it stands for (*record*, from `pasted_back`), or None when the box
    holds a stand-in that isn't in the record -- a paste of the user's own,
    an image -- whose content no screen read can recover. A composer opened
    over such a box would cut it and lose that content, so None is the
    signal not to open one."""
    out: list[str] = []
    pos = 0
    for match in _STAND_IN_RE.finditer(text):
        piece = record.get(_stand_in(match))
        if piece is None:
            return None
        out.append(text[pos : match.start()])
        out.append(piece)
        pos = match.end()
    out.append(text[pos:])
    return "".join(out)


def stashable_draft(text: str) -> str:
    """The composer text worth keeping when a close can't type it back.

    A close whose paste-back is refused -- the agent has left the terminal,
    where a pasted draft would be commands rather than a prompt -- hands the
    text here instead of dropping it (TerminalTab._stash_draft). Only text
    with something in it is kept: a box holding nothing but whitespace is a
    box the user emptied, and re-seeding that into the next composer would
    just be a stray space to delete.
    """
    return text if text.strip() else ""


def draft_to_restore(stashed: str, current: str) -> str:
    """The stashed draft a reopening composer should be seeded with, or "".

    A draft is only ever put back into a box with nothing in it: whatever is
    there now was written after the draft was set aside -- the CLI prompt an
    open cut out of the input box, the keystroke that raised the composer --
    and it must not be typed over. Whitespace alone counts as empty, the
    same way `stashable_draft` reads it.
    """
    return stashed if stashed and not current.strip() else ""


def spell_click_moves_caret(offset: int, selection: tuple[int, int] | None) -> bool:
    """Whether a right-click at *offset* should move the insertion cursor.

    libspelling offers corrections for the word under the *insertion
    cursor* and nowhere else -- its menu is rebuilt from
    ``gtk_text_buffer_get_insert()``, with no way to aim it at a position --
    while GTK4's text view pops its context menu without moving that cursor
    at all. Right-clicking a squiggle therefore lists corrections for
    wherever the caret was parked, which is usually the end of a
    correctly-spelled line, which is usually nothing. The composer moves
    the caret itself so the menu is about the word that was clicked (see
    ComposerView._on_secondary_press).

    *selection* is the current selection as buffer offsets, or None when
    there is none. A click inside a selection leaves the caret alone: every
    other editor keeps a selection you right-click on, and taking it away
    to spell-check one word would be a poor trade. Offsets on the boundary
    count as inside, so a click at the edge of a selection can't destroy
    it by a pixel.
    """
    if selection is None:
        return True
    start, end = sorted(selection)
    if start == end:
        return True
    return not start <= offset <= end
