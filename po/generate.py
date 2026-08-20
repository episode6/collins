#!/usr/bin/env python3
# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-19. Full change history: git log for this file.
"""Generate per-language .po files and compile .mo into the package.

Run from the repo root:  python3 po/generate.py
Re-run after `xgettext` updates po/collins.pot with new strings;
fill any new msgids in TRANSLATIONS below (missing ones fall back to English).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCALE = ROOT / "collins" / "locale"
DOMAIN = "collins"

TRANSLATIONS: dict[str, dict[str, str]] = {
    "hu": {
        "── restored panel history ──": "── visszaállított panelelőzmények ──",
        "Rename session": "Munkamenet átnevezése",
        "Custom name": "Egyéni név",
        "Cancel": "Mégse",
        "Session list": "Munkamenet-lista",
        "Show folder path": "Mappa elérési útjának megjelenítése",
        "Show each session's project folder path in the sidebar":
            "Az egyes munkamenetek projektmappájának elérési útja az oldalsávon",
        "Startup": "Indítás",
        "Reopen the last session": "Utolsó munkamenet újranyitása",
        "Open the session that was active when the app was last closed. "
        "Off, the app launches with no session open":
            "Az alkalmazás legutóbbi bezárásakor aktív munkamenet megnyitása. "
            "Kikapcsolva az alkalmazás nyitott munkamenet nélkül indul",
        "Show Claude usage": "Claude-használat megjelenítése",
        "Show subscription usage limits below the session list":
            "Az előfizetés használati korlátainak megjelenítése a munkamenetlista alatt",
        "Claude usage": "Claude-használat",
        "Refresh usage": "Használat frissítése",
        "Checking usage…": "Használat lekérdezése…",
        "Not logged in to Claude": "Nincs bejelentkezve a Claude-ba",
        "Claude login expired — run claude to refresh":
            "A Claude-bejelentkezés lejárt — futtasd a claude-ot a frissítéshez",
        "Usage unavailable (offline)": "A használat nem érhető el (offline)",
        "Usage unavailable": "A használat nem érhető el",
        "Couldn't refresh usage": "Nem sikerült frissíteni a használatot",
        "Dismiss": "Bezárás",
        "Session (5h)": "Munkamenet (5 óra)",
        "Week — all models": "Hét — minden modell",
        "Week — {model}": "Hét — {model}",
        "Week — model": "Hét — modell",
        "Resets in {t}": "Visszaáll: {t}",
        "As of {n}m ago": "{n} perce frissítve",
        "Extra usage": "Extra használat",
        "{used} of {limit}": "{used} / {limit}",
        "Extra usage: {used}": "Extra használat: {used}",
        " — limit reached": " — elérte a korlátot",
        "Open session file…": "Munkamenetfájl megnyitása…",
        "Open session transcript": "Munkamenet-átirat megnyitása",
        "Session transcripts (*.jsonl)": "Munkamenet-átiratok (*.jsonl)",
        "Could not open transcript": "Az átirat nem nyitható meg",
        "The file couldn't be read as a session transcript.":
            "A fájl nem olvasható be munkamenet-átiratként.",
        "Delete permanently…": "Végleges törlés…",
        "Could not delete transcript": "Az átirat nem törölhető",
        "Delete session permanently?": "Véglegesen törlöd a munkamenetet?",
        "“{name}” and its transcript file will be permanently deleted. This cannot be undone.":
            "A(z) „{name}” és az átiratfájlja véglegesen törlődik. Ez nem vonható vissza.",
        "Delete permanently": "Végleges törlés",
        "New {name} session (advanced)…": "Új {name} munkamenet (haladó)…",
        "Continue last {name} session…": "Utolsó {name} munkamenet folytatása…",
        "New {name} session": "Új {name} munkamenet",
        "Optional flags for this session.": "Választható kapcsolók ehhez a munkamenethez.",
        "Model": "Modell",
        "Default": "Alapértelmezett",
        "Permission mode": "Engedélyezési mód",
        "Extra directory": "További könyvtár",
        "Choose…": "Választás…",
        "Choose a directory": "Válassz egy könyvtárat",
        "Start": "Indítás",
        "Save": "Mentés",
        "Set tab emoji": "Lap emodzsi beállítása",
        "Shown before the tab title. Leave empty to remove.":
            "A lap címe előtt jelenik meg. Hagyd üresen az eltávolításhoz.",
        "e.g. 🚀": "pl. 🚀",
        "OK": "OK",
        "No MCP servers configured": "Nincs konfigurált MCP-kiszolgáló",
        "Global": "Globális",
        "Available to every project": "Minden projekthez elérhető",
        "MCP Servers": "MCP-kiszolgálók",
        "Read-only": "Csak olvasható",
        "Reading transcript…": "Átirat olvasása…",
        "Session details": "Munkamenet részletei",
        "Session ID": "Munkamenet-azonosító",
        "Directory": "Könyvtár",
        "unknown": "ismeretlen",
        "Created": "Létrehozva",
        "Last activity": "Utolsó tevékenység",
        "Messages": "Üzenetek",
        "Tool calls": "Eszközhívások",
        "Models": "Modellek",
        "Tokens": "Tokenek",
        "Transcript size": "Átirat mérete",
        "MCP": "MCP",
        "Available to this project": "Ehhez a projekthez elérhető",
        "Tools used in this session": "A munkamenetben használt eszközök",
        "Recent activity": "Legutóbbi tevékenység",
        "You": "Te",
        "Claude": "Claude",
        "Follow system": "Rendszer követése",
        "Light": "Világos",
        "Dark": "Sötét",
        "Preferences": "Beállítások",
        "General": "Általános",
        "Terminal": "Terminál",
        "Font": "Betűtípus",
        "Applies to all terminal tabs": "Minden terminállapra érvényes",
        "New terminal tab": "Új terminállap",
        "Terminal {number}": "{number}. terminál",
        "A command is still running in this terminal tab and will be terminated.":
            "Ezen a terminállapon még fut egy parancs, amely le lesz állítva.",
        "Reset to default font": "Visszaállítás az alapértelmezett betűtípusra",
        "Scrollback lines": "Visszagörgetési sorok",
        "Easy copy & paste": "Egyszerű másolás és beillesztés",
        "Ctrl+C copies selected text (otherwise interrupts as usual), Ctrl+V pastes, and right-click opens a copy/paste menu":
            "A Ctrl+C másolja a kijelölt szöveget (különben szokás szerint megszakít), a Ctrl+V beilleszt, a jobb kattintás pedig másolás/beillesztés menüt nyit",
        "Copy": "Másolás",
        "Paste": "Beillesztés",
        "Select All": "Összes kijelölése",
        "Color theme": "Színtéma",
        "Appearance": "Megjelenés",
        "Color scheme": "Színséma",
        "Language": "Nyelv",
        "Restart to apply": "Újraindítás szükséges",
        "Running sessions": "Futó munkamenetek",
        "Ask keeps the confirmation dialog; the other choices skip it and exit the "
        "session(s) cleanly or keep them running detached":
            "A „Rákérdezés” megtartja a megerősítő párbeszédablakot; a többi lehetőség "
            "kihagyja, és tisztán kilép a munkamenet(ek)ből, vagy leválasztva futni hagyja őket",
        "When archiving a running session": "Futó munkamenet archiválásakor",
        "Archiving a session that is still running also closes its tab":
            "A még futó munkamenet archiválása a lapját is bezárja",
        "When quitting with running sessions": "Kilépéskor futó munkamenetekkel",
        "Closing a window while agent sessions are still running":
            "Ablak bezárása, miközben ügynök-munkamenetek még futnak",
        "Archiving": "Archiválás",
        "Archive on claude.ai too": "Archiválás a claude.ai-n is",
        "A session that also appears on claude.ai is archived and restored "
        "there along with the toggle here; best-effort, archiving locally "
        "never waits on it":
            "A claude.ai-n is megjelenő munkamenet ott is archiválódik és "
            "visszaáll az itteni kapcsolóval együtt; csak kísérlet — a helyi "
            "archiválás sosem vár rá",
        "Ask": "Rákérdezés",
        "Remove from favorites": "Eltávolítás a kedvencekből",
        "Add to favorites": "Hozzáadás a kedvencekhez",
        "Sessions": "Munkamenetek",
        "Select multiple sessions": "Több munkamenet kijelölése",
        "Show archived sessions": "Archivált munkamenetek megjelenítése",
        "MCP servers": "MCP-kiszolgálók",
        "About Collins": "A Collins névjegye",
        "Refresh session list": "Munkamenetlista frissítése",
        "Search sessions…": "Munkamenetek keresése…",
        "Search sessions": "Munkamenetek keresése",
        "Close search": "Keresés bezárása",
        "A session is working": "Egy munkamenet dolgozik",
        "Collapse all groups": "Összes csoport összecsukása",
        "Expand all groups": "Összes csoport kibontása",
        "No sessions found": "Nincs találat",
        "{n} sessions": "{n} munkamenet",
        "{n} projects": "{n} projekt",
        "{n} open": "{n} megnyitva",
        "Favorites": "Kedvencek",
        "Open": "Megnyitás",
        "Open in Ghostty": "Megnyitás Ghosttyban",
        "Fork session": "Munkamenet elágaztatása",
        "Rename…": "Átnevezés…",
        "Details…": "Részletek…",
        "Copy session ID": "Munkamenet-azonosító másolása",
        "Export as Markdown…": "Exportálás Markdownként…",
        "Reveal transcript": "Átirat megjelenítése",
        "Restore session": "Munkamenet visszaállítása",
        "Archive session": "Munkamenet archiválása",
        "Restore project": "Projekt visszaállítása",
        "Archive project": "Projekt archiválása",
        "New session here": "Új munkamenet itt",
        "Open in {name}": "Megnyitás ezzel: {name}",
        "Open In…": "Megnyitás ezzel…",
        "File Manager": "Fájlkezelő",
        "Right-click to open this folder in your terminal":
            "Jobb kattintással megnyithatod ezt a mappát a termináljában",
        "Open this folder in your file manager":
            "A mappa megnyitása a fájlkezelőben",
        "No terminal application found": "Nem található terminálalkalmazás",
        "Set $TERMINAL, or install a terminal emulator, to open folders here.":
            "Állítsd be a $TERMINAL változót, vagy telepíts egy terminálemulátort, "
            "hogy itt nyithass meg mappákat.",
        "Move transcript to trash…": "Átirat kukába helyezése…",
        "All": "Mind",
        "Select all (filtered) sessions": "Az összes (szűrt) munkamenet kijelölése",
        "None": "Egyik sem",
        "Clear selection": "Kijelölés törlése",
        "Move selected transcripts to trash…": "Kijelölt átiratok kukába helyezése…",
        "Archive selected": "Kijelöltek archiválása",
        "Remove selected from favorites": "Kijelöltek eltávolítása a kedvencekből",
        "Add selected to favorites": "Kijelöltek hozzáadása a kedvencekhez",
        "Open selected in tabs": "Kijelöltek megnyitása lapokon",
        "warning: project dir {cwd} no longer exists, starting in {fallback}":
            "figyelmeztetés: a(z) {cwd} projektkönyvtár már nem létezik, indítás itt: {fallback}",
        "recreating removed worktree {path}":
            "az eltávolított {path} munkafa újralétrehozása",
        "couldn't create a worktree — starting the session in {cwd} instead":
            "nem sikerült munkafát létrehozni — a munkamenet indítása itt: {cwd}",
        "warning: `{cli}` not found in PATH — starting a plain shell":
            "figyelmeztetés: a `{cli}` nem található a PATH-ban — egyszerű parancsértelmező indítása",
        "failed to start shell: {msg}": "nem sikerült elindítani a parancsértelmezőt: {msg}",
        "Find in terminal…": "Keresés a terminálban…",
        "Previous match": "Előző találat",
        "Next match": "Következő találat",
        "Set emoji…": "Emodzsi beállítása…",
        "Close": "Bezárás",
        "Toggle sidebar (F9)": "Oldalsáv ki/be (F9)",
        "Show or hide the tab bar": "Lapsáv megjelenítése vagy elrejtése",
        "New {name} session…": "Új {name} munkamenet…",
        "New session (Ctrl+Shift+T)": "Új munkamenet (Ctrl+Shift+T)",
        "Agent": "Ügynök",
        "Question": "Kérdés",
        "New {name} native chat (experimental)…": "Új natív {name} csevegés (kísérleti)…",
        "Chat with {name} — every file edit and command asks your permission first.":
            "Csevegés a {name}-tal — minden fájlmódosítás és parancs előbb az engedélyedet kéri.",
        "New {name} native chat ({mode}) (experimental)": "Új natív {name} csevegés ({mode}) (kísérleti)",
        "Read-only chat with {name} — analyses and answers, never edits.":
            "Csak olvasható csevegés a {name}-tal — elemez és válaszol, soha nem szerkeszt.",
        "Chat with {name} — ⚠ runs edits and commands automatically, without asking.":
            "Csevegés a {name}-tal — ⚠ a módosításokat és parancsokat automatikusan, kérdés nélkül futtatja.",
        "{name} wants to use {tool}": "A {name} a(z) {tool} eszközt szeretné használni",
        "Allow once": "Egyszeri engedélyezés",
        "Always allow {tool}": "{tool} mindig engedélyezve",
        "Deny": "Elutasítás",
        "Allowed {tool}.": "{tool} engedélyezve.",
        "Always allowing {tool}.": "{tool} mostantól mindig engedélyezve.",
        "Denied {tool}.": "{tool} elutasítva.",
        "Auto-allowed {tool}": "{tool} automatikusan engedélyezve",
        "Ask {name}…": "Kérdezd a {name}-t…",
        "Stop": "Leállítás",
        "{name} is thinking…": "A {name} gondolkodik…",
        "Rate limited — try again later.": "Korlátozva — próbáld újra később.",
        "Error: {msg}": "Hiba: {msg}",
        "Session ended.": "A munkamenet véget ért.",
        "Replay…": "Visszajátszás…",
        "Replay — {name}": "Visszajátszás — {name}",
        "Previous": "Előző",
        "Play": "Lejátszás",
        "Next": "Következő",
        "Show all": "Összes megjelenítése",
        "Nothing to replay yet.": "Még nincs mit visszajátszani.",
        "Chat — {dir}": "Csevegés — {dir}",
        "Chat — {name}": "Csevegés — {name}",
        "Continue in native chat (experimental)": "Folytatás natív csevegésben (kísérleti)",
        "Continue in native chat ({mode}) (experimental)": "Folytatás natív csevegésben ({mode}) (kísérleti)",
        "Continuing the previous session — earlier messages aren't shown here.":
            "A korábbi munkamenet folytatása — a régebbi üzenetek itt nem jelennek meg.",
        "Exit session and close tab": "Kilépés a munkamenetből és a lap bezárása",
        "Background session and close tab": "Munkamenet a háttérbe és a lap bezárása",
        "Exit Session": "Kilépés a munkamenetből",
        "Exit Sessions": "Kilépés a munkamenetekből",
        "Background Session": "Munkamenet a háttérbe",
        "Background Sessions": "Munkamenetek a háttérbe",
        "Backgrounding instead keeps the agent running detached — reopen the "
        "session later to re-attach.":
            "A háttérbe küldéssel az ügynök leválasztva tovább fut — a munkamenet "
            "későbbi megnyitásával újra csatlakozhatsz.",
        "Agents are asked to exit cleanly first; other running commands will be "
        "terminated. Backgrounding instead keeps the agents running detached — "
        "reopen a session later to re-attach.":
            "Az ügynököket először tiszta kilépésre kérjük; a többi futó parancs "
            "leáll. A háttérbe küldéssel az ügynökök leválasztva tovább futnak — "
            "egy munkamenet későbbi megnyitásával újra csatlakozhatsz.",
        "No session open": "Nincs megnyitott munkamenet",
        "Pick a session from the sidebar, or start a new one.":
            "Válassz egy munkamenetet az oldalsávból, vagy indíts egy újat.",
        "Some transcripts could not be trashed": "Néhány átiratot nem sikerült a kukába helyezni",
        "Move {n} transcript(s) to trash?": "{n} átirat kukába helyezése?",
        "The files are moved to the trash and can be restored.":
            "A fájlok a kukába kerülnek, és visszaállíthatók.",
        "Move to Trash": "Kukába",
        "New window": "Új ablak",
        "Quit": "Kilépés",
        "Show Collins": "Collins megjelenítése",
        "Show Collins (Hidden)": "Collins megjelenítése (rejtett)",
        "Collins is still running": "A Collins továbbra is fut",
        "Find it in the top bar.": "A felső sávban találod.",
        "Reopen it by relaunching Collins, or from a session's notification.":
            "Nyisd meg újra a Collins elindításával vagy egy munkamenet értesítéséből.",
        "no sessions open": "nincs nyitott munkamenet",
        "1 session": "1 munkamenet",
        "{n} sessions": "{n} munkamenet",
        "1 working": "1 dolgozik",
        "{n} working": "{n} dolgozik",
        "1 unread": "1 olvasatlan",
        "{n} unread": "{n} olvasatlan",
        "You interrupted Claude here": "Itt szakítottad meg a Claude-ot",
        "Restart now": "Újraindítás most",
        "Choose project directory": "Válassz projektkönyvtárat",
        "Tab name": "Lap neve",
        "Export session as Markdown": "Munkamenet exportálása Markdownként",
        "Export failed": "Az exportálás sikertelen",
        "Could not trash transcript": "Az átiratot nem sikerült a kukába helyezni",
        "Move transcript to trash?": "Átirat kukába helyezése?",
        "“{name}” will be removed from Claude's history.":
            "A(z) „{name}” eltávolításra kerül a Claude előzményeiből.",
        "The file is moved to the trash and can be restored.":
            "A fájl a kukába kerül, és visszaállítható.",
        "Click to copy": "Kattintson a másoláshoz",
        "Click to open": "Kattintson a megnyitáshoz",
        "Click for actions": "Kattintson a műveletekhez",
        "Right-click to open": "Jobb kattintás a megnyitáshoz",
        "Right-click to copy the link": "Jobb kattintás a hivatkozás másolásához",
        "Click to view in Collins": "Kattintson a Collinsban való megtekintéshez",
        "Click to view #{number} in Collins":
            "Kattintson a(z) #{number} Collinsban való megtekintéshez",
        "Right-click for actions": "Jobb kattintás a műveletekhez",
        "Open on GitHub": "Megnyitás GitHubon",
        "Git pull failed": "A git pull sikertelen",
        "git was not found on PATH.": "A git nem található a PATH-on.",
        "git exited with status {code}": "A git {code} státuszkóddal lépett ki",
        "Pulled {project} — {summary}": "{project} frissítve — {summary}",
        "Pulled {project}": "{project} frissítve",
        "Rebase / resolve conflicts": "Rebase / konfliktusok feloldása",
        "Has merge conflicts": "Beolvasztási konfliktusai vannak",
        "Has unresolved comments": "Megválaszolatlan hozzászólásai vannak",
        # pull request footer chip
        "Open pull request": "Nyitott pull request",
        "Draft pull request": "Piszkozat pull request",
        "Merged pull request": "Egyesített pull request",
        "Closed pull request": "Lezárt pull request",
        "{n} passed": "{n} sikeres",
        "{n} failed": "{n} sikertelen",
        "{n} pending": "{n} folyamatban",
        "Couldn't open {name}: {message}": "A(z) „{name}” nem nyitható meg: {message}",
        "{name} is too large to open in the editor.":
            "A(z) „{name}” túl nagy a szerkesztőben való megnyitáshoz.",
        "{name} looks like a binary file and can't be opened here.":
            "A(z) „{name}” bináris fájlnak tűnik, itt nem nyitható meg.",
        "{name} is not a file.": "A(z) „{name}” nem fájl.",
        "{name} is outside this project and can't be opened here.":
            "A(z) „{name}” a projekten kívül található, itt nem nyitható meg.",
        "Couldn't open {name}.": "A(z) „{name}” nem nyitható meg.",
        "{name} changed on disk": "A(z) „{name}” megváltozott a lemezen",
        "Overwrite it with the changes you made here?":
            "Felülírod az itt végzett módosításokkal?",
        "Overwrite": "Felülírás",
        "Couldn't save {name}: {message}": "A(z) „{name}” nem menthető: {message}",
        "{name} was deleted.": "A(z) „{name}” törölve lett.",
        "{name} changed on disk.": "A(z) „{name}” megváltozott a lemezen.",
        "Reload": "Újratöltés",
        "Couldn't reload {name}: {message}": "A(z) „{name}” nem tölthető újra: {message}",
        "Save Changes?": "Mentsük a módosításokat?",
        "Don't Save": "Ne mentsd",
        "“{name}” contains unsaved changes. Changes which are not saved will be permanently lost.":
            "A(z) „{name}” mentetlen módosításokat tartalmaz. A nem mentett "
            "módosítások véglegesen elvesznek.",
        "{n} files contain unsaved changes. Changes which are not saved will be permanently lost.":
            "{n} fájl mentetlen módosításokat tartalmaz. A nem mentett "
            "módosítások véglegesen elvesznek.",
        "Find in file…": "Keresés a fájlban…",
        "Save (Ctrl+S)": "Mentés (Ctrl+S)",
        "Plain Text": "Egyszerű szöveg",
        "Show editor panel": "Szerkesztőpanel megjelenítése",
        "Hide editor panel": "Szerkesztőpanel elrejtése",
        "Move editor to its own window": "Szerkesztő áthelyezése saját ablakba",
        "Bring editor back into this tab": "Szerkesztő visszahozása erre a lapra",
        "Move editor back into its tab": "Szerkesztő visszahelyezése a lapjára",
        "Editor": "Szerkesztő",
        "Follow app theme": "Alkalmazás témájának követése",
        "Applies to the editor panel": "A szerkesztőpanelre vonatkozik",
        "Reset to system monospace": "Visszaállítás a rendszer monospace betűtípusára",
        "Show line numbers": "Sorszámok megjelenítése",
        "Show hidden files": "Rejtett fájlok megjelenítése",
        "Show dotfiles in the editor's file tree":
            "Rejtett fájlok megjelenítése a szerkesztő fájlfájában",
        "Open File": "Fájl megnyitása",
        "Open a file…": "Fájl megnyitása…",
        "Indexing project files…": "Projektfájlok indexelése…",
        "No files found in this project.": "Nem található fájl ebben a projektben.",
        "Project is large — only the first {count} files are searchable.":
            "A projekt nagy — csak az első {count} fájl kereshető.",
        "Agent files": "Ügynökfájlok",
        "Open {name} in the editor": "A(z) {name} megnyitása a szerkesztőben",
        "Session tools": "Munkamenet-eszközök",
        "Tools a session can call to drive Collins. Turning one off takes "
        "effect immediately; sessions already running are only offered the "
        "tool again once they restart":
            "Eszközök, amelyeket egy munkamenet meghívhat a Collins vezérléséhez. "
            "A kikapcsolás azonnal érvénybe lép; a már futó munkamenetek csak "
            "újraindításuk után kapják vissza az eszközt",
        "Name its own session": "Elnevezheti a saját munkamenetét",
        "set_session_title — the session titles its own tab and sidebar row":
            "set_session_title — a munkamenet elnevezi a saját lapját és oldalsávsorát",
        "Open files in the editor": "Fájlok megnyitása a szerkesztőben",
        "open_in_editor — put a file from the project on screen, at a line":
            "open_in_editor — a projekt egy fájlját a képernyőre teszi, adott sornál",
        "Show images": "Képek megjelenítése",
        "show_image — a screenshot, plot, render, or image URL in the in-app lightbox":
            "show_image — képernyőkép, diagram, render vagy képhivatkozás az alkalmazás képnézegetőjében",
        "Send desktop notifications": "Asztali értesítések küldése",
        "notify_user — a notification titled with the session; clicking it opens the tab":
            "notify_user — a munkamenet nevét viselő értesítés; rákattintva megnyílik a lap",
        "Attach pull requests": "Pull requestek csatolása",
        "attach_pr — put a pull request on the session's own footer and sidebar row":
            "attach_pr — pull request elhelyezése a munkamenet saját láblécén "
            "és oldalsávsorán",
        "Open new pull requests automatically":
            "Új pull requestek automatikus megnyitása",
        "Open a pull request's panel beside its session as soon as "
        "the session picks the PR up. Once per pull request, so one "
        "you close again stays closed":
            "Egy pull request paneljének megnyitása a munkamenete mellett, "
            "amint a munkamenet felveszi a PR-t. Pull requestenként egyszer, "
            "így a bezárt panel zárva marad",
        "Show the attachments panel automatically":
            "A mellékletek panel automatikus megjelenítése",
        "Dock a session's attachments panel beside it the first "
        "time it shows an image — only in a tab wide enough to "
        "spare the column, past the terminal's maximum width. Once "
        "per session tab, so one you close again stays closed":
            "A munkamenet mellékletek paneljének dokkolása a munkamenet "
            "mellé, amint az először mutat képet — de csak akkor, ha a lap "
            "elég széles egy külön oszlophoz a terminál legnagyobb "
            "szélességén túl. Munkamenetlaponként egyszer, így a bezárt "
            "panel zárva marad",
        "Open composer (Ctrl+.)": "Üzenetszerkesztő megnyitása (Ctrl+.)",
        "Attach file": "Fájl csatolása",
        "Remove image": "Kép eltávolítása",
        "Close composer and keep the text in the terminal":
            "Üzenetszerkesztő bezárása, a szöveg a terminálban marad",
        "Composer: the agent isn't running in this tab":
            "Üzenetszerkesztő: ezen a lapon nem fut az ügynök",
        "Model switch: the agent isn't running in this tab":
            "Modellváltás: ezen a lapon nem fut az ügynök",
        "Click to switch the model": "Kattintson a modell váltásához",
        "Switch the model for this session":
            "Modell váltása ehhez a munkamenethez",
        "Loading models…": "Modellek betöltése…",
        "Copy model id": "Modellazonosító másolása",
        "Composer": "Üzenetszerkesztő",
        "Dock the composer below the terminal":
            "Üzenetszerkesztő dokkolása a terminál alá",
        "Float the composer over the terminal":
            "Üzenetszerkesztő lebegtetése a terminál felett",
        "Floating composer button": "Lebegő üzenetszerkesztő gomb",
        "Overlay a semi-transparent button on the corner of each agent "
        "terminal that opens the composer, a spell-checked prompt box":
            "Félig átlátszó gomb az ügynökterminálok sarkában, amely "
            "megnyitja az üzenetszerkesztőt, egy helyesírás-ellenőrzős "
            "beviteli mezőt",
        "Enter sends composer text": "Az Enter elküldi az üzenetszerkesztő szövegét",
        "Off: Enter inserts a newline and Ctrl+Enter sends. "
        "Shift+Enter always inserts a newline":
            "Kikapcsolva: az Enter új sort kezd, a Ctrl+Enter küld. "
            "A Shift+Enter mindig új sort kezd",
        "Composer in new sessions": "Üzenetszerkesztő új munkamenetekben",
        "Open the composer as soon as a new session starts — floating "
        "over the agent terminal, or docked as a panel below it, where "
        "it stays for the session's later visits":
            "Az üzenetszerkesztő megnyitása, amint egy új munkamenet "
            "elindul — az ügynökterminál felett lebegve, vagy alatta "
            "panelként dokkolva, ahol a munkamenet későbbi megnyitásaikor "
            "is ott marad",
        "Never": "Soha",
        "Floating": "Lebegő",
        "Docked": "Dokkolt",
        # the attachments panel (collins/attachpanel.py)
        "Attachments": "Mellékletek",
        "Close the attachments panel": "A mellékletek panel bezárása",
        "Dock the attachments panel beside the terminal":
            "A mellékletek panel dokkolása a terminál mellé",
        "Float the attachments panel over the terminal":
            "A mellékletek panel lebegtetése a terminál felett",
        "Images and files this session has seen":
            "A munkamenet által látott képek és fájlok",
        "Images and files this session has seen ({n} new)":
            "A munkamenet által látott képek és fájlok ({n} új)",
        "Open With…": "Megnyitás ezzel…",
        "Show in Folder": "Megjelenítés a mappában",
        "Copy Path": "Elérési út másolása",
        "Copy Address": "Cím másolása",
        "Remove From List": "Eltávolítás a listáról",
        "No longer on disk": "Már nincs a lemezen",
        "Couldn't be downloaded": "Nem sikerült letölteni",
        "No attachments yet": "Még nincsenek mellékletek",
        "Pictures and files this session shares collect here.": "A munkamenet által megosztott képek és fájlok itt gyűlnek össze.",
        "that image isn't on disk any more: {path}": "ez a kép már nincs a lemezen: {path}",
        "that file isn't on disk any more: {path}": "ez a fájl már nincs a lemezen: {path}",
        "couldn't download that image: {reason}": "nem sikerült letölteni a képet: {reason}",
        "Keep Running hides the window and leaves every session exactly as it is.":
            "A Futás folytatása elrejti az ablakot, és minden munkamenetet "
            "pontosan úgy hagy, ahogy van.",
        "Keep Running (Hide Window)": "Futás folytatása (ablak elrejtése)",
        "Hide Window": "Ablak elrejtése",
        "Without a status icon, a hidden window comes back by relaunching "
        "Collins or clicking a session notification":
            "Állapotikon nélkül az elrejtett ablak a Collins újraindításával "
            "vagy egy munkamenet-értesítésre kattintva tér vissza",
        "Install the desktop entry, app icon and metainfo for the current user":
            "Az asztali indító, az alkalmazásikon és a metaadatok telepítése a jelenlegi felhasználónak",
        "Install desktop icon":
            "Asztali ikon telepítése",
        "Couldn't install the desktop icon":
            "Nem sikerült telepíteni az asztali ikont",
        "Collins is in your applications now":
            "A Collins mostantól megtalálható az alkalmazások között",
    },
    "de": {
        "── restored panel history ──": "── wiederhergestellter Panel-Verlauf ──",
        "Rename session": "Sitzung umbenennen",
        "Custom name": "Eigener Name",
        "Cancel": "Abbrechen",
        "Session list": "Sitzungsliste",
        "Show folder path": "Ordnerpfad anzeigen",
        "Show each session's project folder path in the sidebar":
            "Den Projektordnerpfad jeder Sitzung in der Seitenleiste anzeigen",
        "Startup": "Programmstart",
        "Reopen the last session": "Letzte Sitzung wieder öffnen",
        "Open the session that was active when the app was last closed. "
        "Off, the app launches with no session open":
            "Öffnet die Sitzung, die beim letzten Schließen der App aktiv war. "
            "Ausgeschaltet startet die App ohne offene Sitzung",
        "Show Claude usage": "Claude-Nutzung anzeigen",
        "Show subscription usage limits below the session list":
            "Nutzungslimits des Abos unter der Sitzungsliste anzeigen",
        "Claude usage": "Claude-Nutzung",
        "Refresh usage": "Nutzung aktualisieren",
        "Checking usage…": "Nutzung wird geprüft…",
        "Not logged in to Claude": "Nicht bei Claude angemeldet",
        "Claude login expired — run claude to refresh":
            "Claude-Anmeldung abgelaufen — führe claude aus, um sie zu erneuern",
        "Usage unavailable (offline)": "Nutzung nicht verfügbar (offline)",
        "Usage unavailable": "Nutzung nicht verfügbar",
        "Couldn't refresh usage": "Nutzung konnte nicht aktualisiert werden",
        "Dismiss": "Schließen",
        "Session (5h)": "Sitzung (5 h)",
        "Week — all models": "Woche — alle Modelle",
        "Week — {model}": "Woche — {model}",
        "Week — model": "Woche — Modell",
        "Resets in {t}": "Zurücksetzung in {t}",
        "As of {n}m ago": "Stand: vor {n} min",
        "Extra usage": "Zusätzliche Nutzung",
        "{used} of {limit}": "{used} von {limit}",
        "Extra usage: {used}": "Zusätzliche Nutzung: {used}",
        " — limit reached": " — Limit erreicht",
        "Open session file…": "Sitzungsdatei öffnen…",
        "Open session transcript": "Sitzungsprotokoll öffnen",
        "Session transcripts (*.jsonl)": "Sitzungsprotokolle (*.jsonl)",
        "Could not open transcript": "Protokoll konnte nicht geöffnet werden",
        "The file couldn't be read as a session transcript.":
            "Die Datei konnte nicht als Sitzungsprotokoll gelesen werden.",
        "Delete permanently…": "Endgültig löschen…",
        "Could not delete transcript": "Protokoll konnte nicht gelöscht werden",
        "Delete session permanently?": "Sitzung endgültig löschen?",
        "“{name}” and its transcript file will be permanently deleted. This cannot be undone.":
            "„{name}“ und die zugehörige Protokolldatei werden endgültig gelöscht. "
            "Dies kann nicht rückgängig gemacht werden.",
        "Delete permanently": "Endgültig löschen",
        "New {name} session (advanced)…": "Neue {name}-Sitzung (erweitert)…",
        "Continue last {name} session…": "Letzte {name}-Sitzung fortsetzen…",
        "New {name} session": "Neue {name}-Sitzung",
        "Optional flags for this session.": "Optionale Flags für diese Sitzung.",
        "Model": "Modell",
        "Default": "Standard",
        "Permission mode": "Berechtigungsmodus",
        "Extra directory": "Zusätzliches Verzeichnis",
        "Choose…": "Auswählen…",
        "Choose a directory": "Verzeichnis auswählen",
        "Start": "Starten",
        "Save": "Speichern",
        "Set tab emoji": "Tab-Emoji festlegen",
        "Shown before the tab title. Leave empty to remove.":
            "Wird vor dem Tab-Titel angezeigt. Zum Entfernen leer lassen.",
        "e.g. 🚀": "z. B. 🚀",
        "OK": "OK",
        "No MCP servers configured": "Keine MCP-Server konfiguriert",
        "Global": "Global",
        "Available to every project": "Für jedes Projekt verfügbar",
        "MCP Servers": "MCP-Server",
        "Read-only": "Schreibgeschützt",
        "Reading transcript…": "Transkript wird gelesen…",
        "Session details": "Sitzungsdetails",
        "Session ID": "Sitzungs-ID",
        "Directory": "Verzeichnis",
        "unknown": "unbekannt",
        "Created": "Erstellt",
        "Last activity": "Letzte Aktivität",
        "Messages": "Nachrichten",
        "Tool calls": "Tool-Aufrufe",
        "Models": "Modelle",
        "Tokens": "Tokens",
        "Transcript size": "Transkriptgröße",
        "MCP": "MCP",
        "Available to this project": "Für dieses Projekt verfügbar",
        "Tools used in this session": "In dieser Sitzung verwendete Tools",
        "Recent activity": "Letzte Aktivität",
        "You": "Du",
        "Claude": "Claude",
        "Follow system": "System folgen",
        "Light": "Hell",
        "Dark": "Dunkel",
        "Preferences": "Einstellungen",
        "General": "Allgemein",
        "Terminal": "Terminal",
        "Font": "Schriftart",
        "Applies to all terminal tabs": "Gilt für alle Terminal-Tabs",
        "New terminal tab": "Neuer Terminal-Tab",
        "Terminal {number}": "Terminal {number}",
        "A command is still running in this terminal tab and will be terminated.":
            "In diesem Terminal-Tab läuft noch ein Befehl, der beendet wird.",
        "Reset to default font": "Auf Standardschriftart zurücksetzen",
        "Scrollback lines": "Scrollback-Zeilen",
        "Easy copy & paste": "Einfaches Kopieren & Einfügen",
        "Ctrl+C copies selected text (otherwise interrupts as usual), Ctrl+V pastes, and right-click opens a copy/paste menu":
            "Strg+C kopiert markierten Text (unterbricht sonst wie üblich), Strg+V fügt ein, und Rechtsklick öffnet ein Kopieren/Einfügen-Menü",
        "Copy": "Kopieren",
        "Paste": "Einfügen",
        "Select All": "Alles auswählen",
        "Color theme": "Farbthema",
        "Appearance": "Erscheinungsbild",
        "Color scheme": "Farbschema",
        "Language": "Sprache",
        "Restart to apply": "Neustart erforderlich",
        "Running sessions": "Laufende Sitzungen",
        "Ask keeps the confirmation dialog; the other choices skip it and exit the "
        "session(s) cleanly or keep them running detached":
            "„Nachfragen“ behält den Bestätigungsdialog; die anderen Optionen überspringen "
            "ihn und beenden die Sitzung(en) sauber oder lassen sie abgekoppelt weiterlaufen",
        "When archiving a running session": "Beim Archivieren einer laufenden Sitzung",
        "Archiving a session that is still running also closes its tab":
            "Das Archivieren einer noch laufenden Sitzung schließt auch ihren Tab",
        "When quitting with running sessions": "Beim Beenden mit laufenden Sitzungen",
        "Closing a window while agent sessions are still running":
            "Schließen eines Fensters, während Agenten-Sitzungen noch laufen",
        "Archiving": "Archivierung",
        "Archive on claude.ai too": "Auch auf claude.ai archivieren",
        "A session that also appears on claude.ai is archived and restored "
        "there along with the toggle here; best-effort, archiving locally "
        "never waits on it":
            "Eine Sitzung, die auch auf claude.ai erscheint, wird dort mit dem "
            "Umschalter hier archiviert und wiederhergestellt; nach bestem "
            "Bemühen — das lokale Archivieren wartet nie darauf",
        "Ask": "Nachfragen",
        "Remove from favorites": "Aus Favoriten entfernen",
        "Add to favorites": "Zu Favoriten hinzufügen",
        "Sessions": "Sitzungen",
        "Select multiple sessions": "Mehrere Sitzungen auswählen",
        "Show archived sessions": "Archivierte Sitzungen anzeigen",
        "MCP servers": "MCP-Server",
        "About Collins": "Über Collins",
        "Refresh session list": "Sitzungsliste aktualisieren",
        "Search sessions…": "Sitzungen suchen…",
        "Search sessions": "Sitzungen suchen",
        "Close search": "Suche schließen",
        "A session is working": "Eine Sitzung arbeitet",
        "Collapse all groups": "Alle Gruppen einklappen",
        "Expand all groups": "Alle Gruppen ausklappen",
        "No sessions found": "Keine Sitzungen gefunden",
        "{n} sessions": "{n} Sitzungen",
        "{n} projects": "{n} Projekte",
        "{n} open": "{n} geöffnet",
        "Favorites": "Favoriten",
        "Open": "Öffnen",
        "Open in Ghostty": "In Ghostty öffnen",
        "Fork session": "Sitzung verzweigen",
        "Rename…": "Umbenennen…",
        "Details…": "Details…",
        "Copy session ID": "Sitzungs-ID kopieren",
        "Export as Markdown…": "Als Markdown exportieren…",
        "Reveal transcript": "Transkript anzeigen",
        "Restore session": "Sitzung wiederherstellen",
        "Archive session": "Sitzung archivieren",
        "Restore project": "Projekt wiederherstellen",
        "Archive project": "Projekt archivieren",
        "New session here": "Neue Sitzung hier",
        "Open in {name}": "In {name} öffnen",
        "Open In…": "Öffnen mit…",
        "File Manager": "Dateimanager",
        "Right-click to open this folder in your terminal":
            "Rechtsklick öffnet diesen Ordner in deinem Terminal",
        "Open this folder in your file manager":
            "Diesen Ordner in deinem Dateimanager öffnen",
        "No terminal application found": "Keine Terminal-Anwendung gefunden",
        "Set $TERMINAL, or install a terminal emulator, to open folders here.":
            "Setze $TERMINAL oder installiere einen Terminal-Emulator, "
            "um Ordner hier zu öffnen.",
        "Move transcript to trash…": "Transkript in den Papierkorb verschieben…",
        "All": "Alle",
        "Select all (filtered) sessions": "Alle (gefilterten) Sitzungen auswählen",
        "None": "Keine",
        "Clear selection": "Auswahl aufheben",
        "Move selected transcripts to trash…":
            "Ausgewählte Transkripte in den Papierkorb verschieben…",
        "Archive selected": "Ausgewählte archivieren",
        "Remove selected from favorites": "Ausgewählte aus Favoriten entfernen",
        "Add selected to favorites": "Ausgewählte zu Favoriten hinzufügen",
        "Open selected in tabs": "Ausgewählte in Tabs öffnen",
        "warning: project dir {cwd} no longer exists, starting in {fallback}":
            "Warnung: Projektverzeichnis {cwd} existiert nicht mehr, starte in {fallback}",
        "recreating removed worktree {path}":
            "entfernter Worktree {path} wird neu erstellt",
        "couldn't create a worktree — starting the session in {cwd} instead":
            "Worktree konnte nicht erstellt werden — Sitzung wird stattdessen in {cwd} gestartet",
        "warning: `{cli}` not found in PATH — starting a plain shell":
            "Warnung: `{cli}` nicht im PATH gefunden — starte eine einfache Shell",
        "failed to start shell: {msg}": "Shell konnte nicht gestartet werden: {msg}",
        "Find in terminal…": "Im Terminal suchen…",
        "Previous match": "Vorheriger Treffer",
        "Next match": "Nächster Treffer",
        "Set emoji…": "Emoji festlegen…",
        "Close": "Schließen",
        "Toggle sidebar (F9)": "Seitenleiste umschalten (F9)",
        "Show or hide the tab bar": "Tab-Leiste ein- oder ausblenden",
        "New {name} session…": "Neue {name}-Sitzung…",
        "New session (Ctrl+Shift+T)": "Neue Sitzung (Strg+Umschalt+T)",
        "Agent": "Agent",
        "Question": "Frage",
        "New {name} native chat (experimental)…": "Neuer nativer {name}-Chat (experimentell)…",
        "Chat with {name} — every file edit and command asks your permission first.":
            "Chat mit {name} — jede Dateiänderung und jeder Befehl fragt zuerst um Erlaubnis.",
        "New {name} native chat ({mode}) (experimental)": "Neuer nativer {name}-Chat ({mode}) (experimentell)",
        "Read-only chat with {name} — analyses and answers, never edits.":
            "Schreibgeschützter Chat mit {name} — analysiert und antwortet, bearbeitet nie.",
        "Chat with {name} — ⚠ runs edits and commands automatically, without asking.":
            "Chat mit {name} — ⚠ führt Änderungen und Befehle automatisch ohne Nachfrage aus.",
        "{name} wants to use {tool}": "{name} möchte {tool} verwenden",
        "Allow once": "Einmal erlauben",
        "Always allow {tool}": "{tool} immer erlauben",
        "Deny": "Ablehnen",
        "Allowed {tool}.": "{tool} erlaubt.",
        "Always allowing {tool}.": "{tool} wird ab jetzt immer erlaubt.",
        "Denied {tool}.": "{tool} abgelehnt.",
        "Auto-allowed {tool}": "{tool} automatisch erlaubt",
        "Ask {name}…": "{name} fragen…",
        "Stop": "Stopp",
        "{name} is thinking…": "{name} denkt nach…",
        "Rate limited — try again later.": "Ratenlimit erreicht — später erneut versuchen.",
        "Error: {msg}": "Fehler: {msg}",
        "Session ended.": "Sitzung beendet.",
        "Replay…": "Wiedergabe…",
        "Replay — {name}": "Wiedergabe — {name}",
        "Previous": "Zurück",
        "Play": "Abspielen",
        "Next": "Weiter",
        "Show all": "Alle anzeigen",
        "Nothing to replay yet.": "Noch nichts abzuspielen.",
        "Chat — {dir}": "Chat — {dir}",
        "Chat — {name}": "Chat — {name}",
        "Continue in native chat (experimental)": "Im nativen Chat fortsetzen (experimentell)",
        "Continue in native chat ({mode}) (experimental)": "Im nativen Chat fortsetzen ({mode}) (experimentell)",
        "Continuing the previous session — earlier messages aren't shown here.":
            "Frühere Sitzung wird fortgesetzt — ältere Nachrichten werden hier nicht angezeigt.",
        "Exit session and close tab": "Sitzung beenden und Tab schließen",
        "Background session and close tab": "Sitzung in den Hintergrund und Tab schließen",
        "Exit Session": "Sitzung beenden",
        "Exit Sessions": "Sitzungen beenden",
        "Background Session": "Sitzung in den Hintergrund",
        "Background Sessions": "Sitzungen in den Hintergrund",
        "Backgrounding instead keeps the agent running detached — reopen the "
        "session later to re-attach.":
            "Beim Verschieben in den Hintergrund läuft der Agent abgekoppelt "
            "weiter — öffne die Sitzung später erneut, um dich wieder zu verbinden.",
        "Agents are asked to exit cleanly first; other running commands will be "
        "terminated. Backgrounding instead keeps the agents running detached — "
        "reopen a session later to re-attach.":
            "Agenten werden zuerst gebeten, sich sauber zu beenden; andere laufende "
            "Befehle werden abgebrochen. Beim Verschieben in den Hintergrund laufen "
            "die Agenten abgekoppelt weiter — öffne eine Sitzung später erneut, um "
            "dich wieder zu verbinden.",
        "No session open": "Keine Sitzung geöffnet",
        "Pick a session from the sidebar, or start a new one.":
            "Wähle eine Sitzung in der Seitenleiste oder starte eine neue.",
        "Some transcripts could not be trashed":
            "Einige Transkripte konnten nicht in den Papierkorb verschoben werden",
        "Move {n} transcript(s) to trash?": "{n} Transkript(e) in den Papierkorb verschieben?",
        "The files are moved to the trash and can be restored.":
            "Die Dateien werden in den Papierkorb verschoben und können wiederhergestellt werden.",
        "Move to Trash": "In den Papierkorb",
        "New window": "Neues Fenster",
        "Quit": "Beenden",
        "Show Collins": "Collins anzeigen",
        "Show Collins (Hidden)": "Collins anzeigen (verborgen)",
        "Collins is still running": "Collins läuft weiterhin",
        "Find it in the top bar.": "Du findest Collins in der oberen Leiste.",
        "Reopen it by relaunching Collins, or from a session's notification.":
            "Öffne Collins erneut, indem du es neu startest oder über die "
            "Benachrichtigung einer Sitzung.",
        "no sessions open": "keine Sitzungen geöffnet",
        "1 session": "1 Sitzung",
        "{n} sessions": "{n} Sitzungen",
        "1 working": "1 arbeitet",
        "{n} working": "{n} arbeiten",
        "1 unread": "1 ungelesen",
        "{n} unread": "{n} ungelesen",
        "You interrupted Claude here": "Du hast Claude hier unterbrochen",
        "Restart now": "Jetzt neu starten",
        "Choose project directory": "Projektverzeichnis wählen",
        "Tab name": "Tab-Name",
        "Export session as Markdown": "Sitzung als Markdown exportieren",
        "Export failed": "Export fehlgeschlagen",
        "Could not trash transcript":
            "Transkript konnte nicht in den Papierkorb verschoben werden",
        "Move transcript to trash?": "Transkript in den Papierkorb verschieben?",
        "“{name}” will be removed from Claude's history.":
            "„{name}“ wird aus Claudes Verlauf entfernt.",
        "The file is moved to the trash and can be restored.":
            "Die Datei wird in den Papierkorb verschoben und kann wiederhergestellt werden.",
        "Click to copy": "Zum Kopieren klicken",
        "Click to open": "Zum Öffnen klicken",
        "Click for actions": "Für Aktionen klicken",
        "Right-click to open": "Zum Öffnen rechtsklicken",
        "Right-click to copy the link": "Zum Kopieren des Links rechtsklicken",
        "Click to view in Collins": "Zum Anzeigen in Collins klicken",
        "Click to view #{number} in Collins":
            "Zum Anzeigen von #{number} in Collins klicken",
        "Right-click for actions": "Für Aktionen rechtsklicken",
        "Open on GitHub": "Auf GitHub öffnen",
        "Git pull failed": "Git pull fehlgeschlagen",
        "git was not found on PATH.": "git wurde nicht im PATH gefunden.",
        "git exited with status {code}": "git wurde mit Status {code} beendet",
        "Pulled {project} — {summary}": "{project} aktualisiert — {summary}",
        "Pulled {project}": "{project} aktualisiert",
        "Rebase / resolve conflicts": "Rebase / Konflikte auflösen",
        "Has merge conflicts": "Hat Merge-Konflikte",
        "Has unresolved comments": "Hat unbeantwortete Kommentare",
        # pull request footer chip
        "Open pull request": "Offener Pull Request",
        "Draft pull request": "Pull-Request-Entwurf",
        "Merged pull request": "Zusammengeführter Pull Request",
        "Closed pull request": "Geschlossener Pull Request",
        "{n} passed": "{n} erfolgreich",
        "{n} failed": "{n} fehlgeschlagen",
        "{n} pending": "{n} ausstehend",
        "Couldn't open {name}: {message}": "„{name}“ konnte nicht geöffnet werden: {message}",
        "{name} is too large to open in the editor.":
            "„{name}“ ist zu groß, um im Editor geöffnet zu werden.",
        "{name} looks like a binary file and can't be opened here.":
            "„{name}“ sieht wie eine Binärdatei aus und kann hier nicht geöffnet werden.",
        "{name} is not a file.": "„{name}“ ist keine Datei.",
        "{name} is outside this project and can't be opened here.":
            "„{name}“ liegt außerhalb dieses Projekts und kann hier nicht geöffnet werden.",
        "Couldn't open {name}.": "„{name}“ konnte nicht geöffnet werden.",
        "{name} changed on disk": "„{name}“ wurde auf der Festplatte geändert",
        "Overwrite it with the changes you made here?":
            "Mit den hier vorgenommenen Änderungen überschreiben?",
        "Overwrite": "Überschreiben",
        "Couldn't save {name}: {message}": "„{name}“ konnte nicht gespeichert werden: {message}",
        "{name} was deleted.": "„{name}“ wurde gelöscht.",
        "{name} changed on disk.": "„{name}“ wurde auf der Festplatte geändert.",
        "Reload": "Neu laden",
        "Couldn't reload {name}: {message}":
            "„{name}“ konnte nicht neu geladen werden: {message}",
        "Save Changes?": "Änderungen speichern?",
        "Don't Save": "Nicht speichern",
        "“{name}” contains unsaved changes. Changes which are not saved will be permanently lost.":
            "„{name}“ enthält ungespeicherte Änderungen. Nicht gespeicherte "
            "Änderungen gehen dauerhaft verloren.",
        "{n} files contain unsaved changes. Changes which are not saved will be permanently lost.":
            "{n} Dateien enthalten ungespeicherte Änderungen. Nicht gespeicherte "
            "Änderungen gehen dauerhaft verloren.",
        "Find in file…": "In Datei suchen…",
        "Save (Ctrl+S)": "Speichern (Strg+S)",
        "Plain Text": "Reiner Text",
        "Show editor panel": "Editor-Panel anzeigen",
        "Hide editor panel": "Editor-Panel ausblenden",
        "Move editor to its own window": "Editor in eigenes Fenster verschieben",
        "Bring editor back into this tab": "Editor in diesen Tab zurückholen",
        "Move editor back into its tab": "Editor zurück in seinen Tab verschieben",
        "Editor": "Editor",
        "Follow app theme": "Dem App-Thema folgen",
        "Applies to the editor panel": "Gilt für das Editor-Panel",
        "Reset to system monospace": "Auf System-Monospace zurücksetzen",
        "Show line numbers": "Zeilennummern anzeigen",
        "Show hidden files": "Versteckte Dateien anzeigen",
        "Show dotfiles in the editor's file tree":
            "Versteckte Dateien im Dateibaum des Editors anzeigen",
        "Open File": "Datei öffnen",
        "Open a file…": "Datei öffnen…",
        "Indexing project files…": "Projektdateien werden indiziert…",
        "No files found in this project.": "Keine Dateien in diesem Projekt gefunden.",
        "Project is large — only the first {count} files are searchable.":
            "Großes Projekt — nur die ersten {count} Dateien sind durchsuchbar.",
        "Agent files": "Agent-Dateien",
        "Open {name} in the editor": "{name} im Editor öffnen",
        "Session tools": "Sitzungswerkzeuge",
        "Tools a session can call to drive Collins. Turning one off takes "
        "effect immediately; sessions already running are only offered the "
        "tool again once they restart":
            "Werkzeuge, die eine Sitzung aufrufen kann, um Collins zu steuern. "
            "Das Abschalten wirkt sofort; bereits laufende Sitzungen bekommen "
            "das Werkzeug erst nach einem Neustart wieder angeboten",
        "Name its own session": "Sitzung selbst benennen",
        "set_session_title — the session titles its own tab and sidebar row":
            "set_session_title — die Sitzung benennt ihren eigenen Tab und ihre "
            "Zeile in der Seitenleiste",
        "Open files in the editor": "Dateien im Editor öffnen",
        "open_in_editor — put a file from the project on screen, at a line":
            "open_in_editor — zeigt eine Datei des Projekts an, an einer bestimmten Zeile",
        "Show images": "Bilder anzeigen",
        "show_image — a screenshot, plot, render, or image URL in the in-app lightbox":
            "show_image — ein Screenshot, Diagramm, Render oder eine Bild-URL in der integrierten Lightbox",
        "Send desktop notifications": "Desktop-Benachrichtigungen senden",
        "notify_user — a notification titled with the session; clicking it opens the tab":
            "notify_user — eine Benachrichtigung mit dem Sitzungsnamen; ein Klick "
            "öffnet den Tab",
        "Attach pull requests": "Pull-Requests anheften",
        "attach_pr — put a pull request on the session's own footer and sidebar row":
            "attach_pr — setzt einen Pull-Request auf die Fußzeile und "
            "Seitenleistenzeile der Sitzung",
        "Open new pull requests automatically":
            "Neue Pull-Requests automatisch öffnen",
        "Open a pull request's panel beside its session as soon as "
        "the session picks the PR up. Once per pull request, so one "
        "you close again stays closed":
            "Das Panel eines Pull-Requests neben seiner Sitzung öffnen, "
            "sobald die Sitzung den Pull-Request aufnimmt. Einmal pro "
            "Pull-Request — ein wieder geschlossenes Panel bleibt also zu",
        "Show the attachments panel automatically":
            "Das Anhänge-Panel automatisch anzeigen",
        "Dock a session's attachments panel beside it the first "
        "time it shows an image — only in a tab wide enough to "
        "spare the column, past the terminal's maximum width. Once "
        "per session tab, so one you close again stays closed":
            "Das Anhänge-Panel einer Sitzung neben ihr andocken, sobald sie "
            "das erste Bild zeigt — nur in einem Tab, der breit genug für "
            "eine eigene Spalte jenseits der maximalen Terminalbreite ist. "
            "Einmal pro Sitzungstab — ein wieder geschlossenes Panel bleibt "
            "also zu",
        "Open composer (Ctrl+.)": "Composer öffnen (Strg+.)",
        "Attach file": "Datei anhängen",
        "Remove image": "Bild entfernen",
        "Close composer and keep the text in the terminal":
            "Composer schließen und den Text im Terminal behalten",
        "Composer: the agent isn't running in this tab":
            "Composer: In diesem Tab läuft kein Agent",
        "Model switch: the agent isn't running in this tab":
            "Modellwechsel: In diesem Tab läuft kein Agent",
        "Click to switch the model": "Zum Wechseln des Modells klicken",
        "Switch the model for this session":
            "Das Modell für diese Sitzung wechseln",
        "Loading models…": "Modelle werden geladen…",
        "Copy model id": "Modell-ID kopieren",
        "Composer": "Composer",
        "Dock the composer below the terminal":
            "Composer unter dem Terminal andocken",
        "Float the composer over the terminal":
            "Composer über dem Terminal schweben lassen",
        "Floating composer button": "Schwebende Composer-Schaltfläche",
        "Overlay a semi-transparent button on the corner of each agent "
        "terminal that opens the composer, a spell-checked prompt box":
            "Halbtransparente Schaltfläche in der Ecke jedes Agent-Terminals, "
            "die den Composer öffnet — ein Eingabefeld mit Rechtschreibprüfung",
        "Enter sends composer text": "Enter sendet den Composer-Text",
        "Off: Enter inserts a newline and Ctrl+Enter sends. "
        "Shift+Enter always inserts a newline":
            "Aus: Enter fügt eine neue Zeile ein und Strg+Enter sendet. "
            "Umschalt+Enter fügt immer eine neue Zeile ein",
        "Composer in new sessions": "Composer in neuen Sitzungen",
        "Open the composer as soon as a new session starts — floating "
        "over the agent terminal, or docked as a panel below it, where "
        "it stays for the session's later visits":
            "Den Composer öffnen, sobald eine neue Sitzung startet — "
            "schwebend über dem Terminal oder als Panel darunter "
            "angedockt, wo er auch bei späteren Besuchen bleibt",
        "Never": "Nie",
        "Floating": "Schwebend",
        "Docked": "Angedockt",
        # the attachments panel (collins/attachpanel.py)
        "Attachments": "Anhänge",
        "Close the attachments panel": "Anhänge-Panel schließen",
        "Dock the attachments panel beside the terminal":
            "Anhänge-Panel neben dem Terminal andocken",
        "Float the attachments panel over the terminal":
            "Anhänge-Panel über dem Terminal schweben lassen",
        "Images and files this session has seen":
            "Bilder und Dateien, die diese Sitzung gesehen hat",
        "Images and files this session has seen ({n} new)":
            "Bilder und Dateien, die diese Sitzung gesehen hat ({n} neu)",
        "Open With…": "Öffnen mit…",
        "Show in Folder": "Im Ordner anzeigen",
        "Copy Path": "Pfad kopieren",
        "Copy Address": "Adresse kopieren",
        "Remove From List": "Aus der Liste entfernen",
        "No longer on disk": "Nicht mehr auf der Festplatte",
        "Couldn't be downloaded": "Konnte nicht heruntergeladen werden",
        "No attachments yet": "Noch keine Anhänge",
        "Pictures and files this session shares collect here.": "Bilder und Dateien, die diese Sitzung teilt, sammeln sich hier.",
        "that image isn't on disk any more: {path}": "dieses Bild ist nicht mehr auf der Festplatte: {path}",
        "that file isn't on disk any more: {path}": "diese Datei ist nicht mehr auf der Festplatte: {path}",
        "couldn't download that image: {reason}": "das Bild konnte nicht heruntergeladen werden: {reason}",
        "Keep Running hides the window and leaves every session exactly as it is.":
            "Weiterlaufen lassen blendet das Fenster aus und lässt jede "
            "Sitzung genau so, wie sie ist.",
        "Keep Running (Hide Window)": "Weiterlaufen lassen (Fenster ausblenden)",
        "Hide Window": "Fenster ausblenden",
        "Without a status icon, a hidden window comes back by relaunching "
        "Collins or clicking a session notification":
            "Ohne Statussymbol kommt ein ausgeblendetes Fenster durch einen "
            "Neustart von Collins oder einen Klick auf eine "
            "Sitzungsbenachrichtigung zurück",
        "Install the desktop entry, app icon and metainfo for the current user":
            "Desktop-Eintrag, App-Symbol und Metainfo für den aktuellen Benutzer installieren",
        "Install desktop icon":
            "Desktop-Symbol installieren",
        "Couldn't install the desktop icon":
            "Desktop-Symbol konnte nicht installiert werden",
        "Collins is in your applications now":
            "Collins ist jetzt in deinen Anwendungen",
    },
    "es": {
        "── restored panel history ──": "── historial del panel restaurado ──",
        "Rename session": "Renombrar sesión",
        "Custom name": "Nombre personalizado",
        "Cancel": "Cancelar",
        "Session list": "Lista de sesiones",
        "Show folder path": "Mostrar ruta de carpeta",
        "Show each session's project folder path in the sidebar":
            "Mostrar la ruta de la carpeta del proyecto de cada sesión en la barra lateral",
        "Startup": "Inicio",
        "Reopen the last session": "Reabrir la última sesión",
        "Open the session that was active when the app was last closed. "
        "Off, the app launches with no session open":
            "Abre la sesión que estaba activa cuando la aplicación se cerró por "
            "última vez. Desactivado, la aplicación se inicia sin ninguna sesión abierta",
        "Show Claude usage": "Mostrar el uso de Claude",
        "Show subscription usage limits below the session list":
            "Mostrar los límites de uso de la suscripción bajo la lista de sesiones",
        "Claude usage": "Uso de Claude",
        "Refresh usage": "Actualizar uso",
        "Checking usage…": "Comprobando el uso…",
        "Not logged in to Claude": "No has iniciado sesión en Claude",
        "Claude login expired — run claude to refresh":
            "La sesión de Claude ha caducado — ejecuta claude para renovarla",
        "Usage unavailable (offline)": "Uso no disponible (sin conexión)",
        "Usage unavailable": "Uso no disponible",
        "Couldn't refresh usage": "No se pudo actualizar el uso",
        "Dismiss": "Descartar",
        "Session (5h)": "Sesión (5 h)",
        "Week — all models": "Semana — todos los modelos",
        "Week — {model}": "Semana — {model}",
        "Week — model": "Semana — modelo",
        "Resets in {t}": "Se restablece en {t}",
        "As of {n}m ago": "De hace {n} min",
        "Extra usage": "Uso adicional",
        "{used} of {limit}": "{used} de {limit}",
        "Extra usage: {used}": "Uso adicional: {used}",
        " — limit reached": " — límite alcanzado",
        "Open session file…": "Abrir archivo de sesión…",
        "Open session transcript": "Abrir transcripción de sesión",
        "Session transcripts (*.jsonl)": "Transcripciones de sesión (*.jsonl)",
        "Could not open transcript": "No se pudo abrir la transcripción",
        "The file couldn't be read as a session transcript.":
            "No se pudo leer el archivo como transcripción de sesión.",
        "Delete permanently…": "Eliminar permanentemente…",
        "Could not delete transcript": "No se pudo eliminar la transcripción",
        "Delete session permanently?": "¿Eliminar la sesión permanentemente?",
        "“{name}” and its transcript file will be permanently deleted. This cannot be undone.":
            "«{name}» y su archivo de transcripción se eliminarán permanentemente. "
            "Esto no se puede deshacer.",
        "Delete permanently": "Eliminar permanentemente",
        "New {name} session (advanced)…": "Nueva sesión de {name} (avanzada)…",
        "Continue last {name} session…": "Continuar la última sesión de {name}…",
        "New {name} session": "Nueva sesión de {name}",
        "Optional flags for this session.": "Opciones para esta sesión.",
        "Model": "Modelo",
        "Default": "Predeterminado",
        "Permission mode": "Modo de permisos",
        "Extra directory": "Directorio adicional",
        "Choose…": "Elegir…",
        "Choose a directory": "Elige un directorio",
        "Start": "Iniciar",
        "Save": "Guardar",
        "Set tab emoji": "Establecer emoji de pestaña",
        "Shown before the tab title. Leave empty to remove.":
            "Se muestra antes del título de la pestaña. Déjalo vacío para quitarlo.",
        "e.g. 🚀": "p. ej. 🚀",
        "OK": "Aceptar",
        "No MCP servers configured": "No hay servidores MCP configurados",
        "Global": "Global",
        "Available to every project": "Disponible para todos los proyectos",
        "MCP Servers": "Servidores MCP",
        "Read-only": "Solo lectura",
        "Reading transcript…": "Leyendo transcripción…",
        "Session details": "Detalles de la sesión",
        "Session ID": "ID de sesión",
        "Directory": "Directorio",
        "unknown": "desconocido",
        "Created": "Creada",
        "Last activity": "Última actividad",
        "Messages": "Mensajes",
        "Tool calls": "Llamadas a herramientas",
        "Models": "Modelos",
        "Tokens": "Tokens",
        "Transcript size": "Tamaño de la transcripción",
        "MCP": "MCP",
        "Available to this project": "Disponible para este proyecto",
        "Tools used in this session": "Herramientas usadas en esta sesión",
        "Recent activity": "Actividad reciente",
        "You": "Tú",
        "Claude": "Claude",
        "Follow system": "Seguir el sistema",
        "Light": "Claro",
        "Dark": "Oscuro",
        "Preferences": "Preferencias",
        "General": "General",
        "Terminal": "Terminal",
        "Font": "Fuente",
        "Applies to all terminal tabs": "Se aplica a todas las pestañas de terminal",
        "New terminal tab": "Nueva pestaña de terminal",
        "Terminal {number}": "Terminal {number}",
        "A command is still running in this terminal tab and will be terminated.":
            "Todavía se está ejecutando un comando en esta pestaña de terminal "
            "y se terminará.",
        "Reset to default font": "Restablecer la fuente predeterminada",
        "Scrollback lines": "Líneas de historial",
        "Easy copy & paste": "Copiado y pegado fáciles",
        "Ctrl+C copies selected text (otherwise interrupts as usual), Ctrl+V pastes, and right-click opens a copy/paste menu":
            "Ctrl+C copia el texto seleccionado (si no, interrumpe como de costumbre), Ctrl+V pega y el clic derecho abre un menú de copiar/pegar",
        "Copy": "Copiar",
        "Paste": "Pegar",
        "Select All": "Seleccionar todo",
        "Color theme": "Tema de color",
        "Appearance": "Apariencia",
        "Color scheme": "Esquema de color",
        "Language": "Idioma",
        "Restart to apply": "Reinicia para aplicar",
        "Running sessions": "Sesiones en ejecución",
        "Ask keeps the confirmation dialog; the other choices skip it and exit the "
        "session(s) cleanly or keep them running detached":
            "«Preguntar» mantiene el diálogo de confirmación; las demás opciones lo omiten "
            "y salen limpiamente de las sesiones o las mantienen en ejecución desacopladas",
        "When archiving a running session": "Al archivar una sesión en ejecución",
        "Archiving a session that is still running also closes its tab":
            "Archivar una sesión que sigue en ejecución también cierra su pestaña",
        "When quitting with running sessions": "Al salir con sesiones en ejecución",
        "Closing a window while agent sessions are still running":
            "Cerrar una ventana mientras las sesiones del agente siguen en ejecución",
        "Archiving": "Archivado",
        "Archive on claude.ai too": "Archivar también en claude.ai",
        "A session that also appears on claude.ai is archived and restored "
        "there along with the toggle here; best-effort, archiving locally "
        "never waits on it":
            "Una sesión que también aparece en claude.ai se archiva y se "
            "restaura allí junto con el conmutador de aquí; según lo posible — "
            "archivar localmente nunca espera a ello",
        "Ask": "Preguntar",
        "Remove from favorites": "Quitar de favoritos",
        "Add to favorites": "Añadir a favoritos",
        "Sessions": "Sesiones",
        "Select multiple sessions": "Seleccionar varias sesiones",
        "Show archived sessions": "Mostrar sesiones archivadas",
        "MCP servers": "Servidores MCP",
        "About Collins": "Acerca de Collins",
        "Refresh session list": "Actualizar la lista de sesiones",
        "Search sessions…": "Buscar sesiones…",
        "Search sessions": "Buscar sesiones",
        "Close search": "Cerrar la búsqueda",
        "A session is working": "Una sesión está trabajando",
        "Collapse all groups": "Contraer todos los grupos",
        "Expand all groups": "Expandir todos los grupos",
        "No sessions found": "No se encontraron sesiones",
        "{n} sessions": "{n} sesiones",
        "{n} projects": "{n} proyectos",
        "{n} open": "{n} abiertas",
        "Favorites": "Favoritos",
        "Open": "Abrir",
        "Open in Ghostty": "Abrir en Ghostty",
        "Fork session": "Bifurcar sesión",
        "Rename…": "Renombrar…",
        "Details…": "Detalles…",
        "Copy session ID": "Copiar ID de sesión",
        "Export as Markdown…": "Exportar como Markdown…",
        "Reveal transcript": "Mostrar transcripción",
        "Restore session": "Restaurar sesión",
        "Archive session": "Archivar sesión",
        "Restore project": "Restaurar proyecto",
        "Archive project": "Archivar proyecto",
        "New session here": "Nueva sesión aquí",
        "Open in {name}": "Abrir en {name}",
        "Open In…": "Abrir en…",
        "File Manager": "Gestor de archivos",
        "Right-click to open this folder in your terminal":
            "Clic derecho para abrir esta carpeta en tu terminal",
        "Open this folder in your file manager":
            "Abrir esta carpeta en tu gestor de archivos",
        "No terminal application found": "No se encontró ninguna aplicación de terminal",
        "Set $TERMINAL, or install a terminal emulator, to open folders here.":
            "Define $TERMINAL o instala un emulador de terminal "
            "para abrir carpetas aquí.",
        "Move transcript to trash…": "Mover transcripción a la papelera…",
        "All": "Todas",
        "Select all (filtered) sessions": "Seleccionar todas las sesiones (filtradas)",
        "None": "Ninguna",
        "Clear selection": "Borrar selección",
        "Move selected transcripts to trash…":
            "Mover las transcripciones seleccionadas a la papelera…",
        "Archive selected": "Archivar seleccionadas",
        "Remove selected from favorites": "Quitar seleccionadas de favoritos",
        "Add selected to favorites": "Añadir seleccionadas a favoritos",
        "Open selected in tabs": "Abrir seleccionadas en pestañas",
        "warning: project dir {cwd} no longer exists, starting in {fallback}":
            "advertencia: el directorio del proyecto {cwd} ya no existe, iniciando en {fallback}",
        "recreating removed worktree {path}":
            "recreando el worktree eliminado {path}",
        "couldn't create a worktree — starting the session in {cwd} instead":
            "no se pudo crear un worktree — iniciando la sesión en {cwd}",
        "warning: `{cli}` not found in PATH — starting a plain shell":
            "advertencia: no se encontró `{cli}` en PATH — iniciando una shell simple",
        "failed to start shell: {msg}": "no se pudo iniciar la shell: {msg}",
        "Find in terminal…": "Buscar en la terminal…",
        "Previous match": "Coincidencia anterior",
        "Next match": "Coincidencia siguiente",
        "Set emoji…": "Establecer emoji…",
        "Close": "Cerrar",
        "Toggle sidebar (F9)": "Mostrar u ocultar la barra lateral (F9)",
        "Show or hide the tab bar": "Mostrar u ocultar la barra de pestañas",
        "New {name} session…": "Nueva sesión de {name}…",
        "New session (Ctrl+Shift+T)": "Nueva sesión (Ctrl+Mayús+T)",
        "Agent": "Agente",
        "Question": "Pregunta",
        "New {name} native chat (experimental)…": "Nuevo chat nativo de {name} (experimental)…",
        "Chat with {name} — every file edit and command asks your permission first.":
            "Chat con {name}: cada edición de archivo y comando pide tu permiso primero.",
        "New {name} native chat ({mode}) (experimental)": "Nuevo chat nativo de {name} ({mode}) (experimental)",
        "Read-only chat with {name} — analyses and answers, never edits.":
            "Chat de solo lectura con {name}: analiza y responde, nunca edita.",
        "Chat with {name} — ⚠ runs edits and commands automatically, without asking.":
            "Chat con {name}: ⚠ ejecuta ediciones y comandos automáticamente, sin preguntar.",
        "{name} wants to use {tool}": "{name} quiere usar {tool}",
        "Allow once": "Permitir una vez",
        "Always allow {tool}": "Permitir siempre {tool}",
        "Deny": "Denegar",
        "Allowed {tool}.": "{tool} permitido.",
        "Always allowing {tool}.": "Permitiendo siempre {tool}.",
        "Denied {tool}.": "{tool} denegado.",
        "Auto-allowed {tool}": "{tool} permitido automáticamente",
        "Ask {name}…": "Pregunta a {name}…",
        "Stop": "Detener",
        "{name} is thinking…": "{name} está pensando…",
        "Rate limited — try again later.": "Límite alcanzado — inténtalo más tarde.",
        "Error: {msg}": "Error: {msg}",
        "Session ended.": "Sesión finalizada.",
        "Replay…": "Reproducir…",
        "Replay — {name}": "Reproducción — {name}",
        "Previous": "Anterior",
        "Play": "Reproducir",
        "Next": "Siguiente",
        "Show all": "Mostrar todo",
        "Nothing to replay yet.": "Aún no hay nada que reproducir.",
        "Chat — {dir}": "Chat — {dir}",
        "Chat — {name}": "Chat — {name}",
        "Continue in native chat (experimental)": "Continuar en el chat nativo (experimental)",
        "Continue in native chat ({mode}) (experimental)": "Continuar en el chat nativo ({mode}) (experimental)",
        "Continuing the previous session — earlier messages aren't shown here.":
            "Continuando la sesión anterior: los mensajes anteriores no se muestran aquí.",
        "Exit session and close tab": "Salir de la sesión y cerrar la pestaña",
        "Background session and close tab": "Sesión a segundo plano y cerrar la pestaña",
        "Exit Session": "Salir de la sesión",
        "Exit Sessions": "Salir de las sesiones",
        "Background Session": "Sesión a segundo plano",
        "Background Sessions": "Sesiones a segundo plano",
        "Backgrounding instead keeps the agent running detached — reopen the "
        "session later to re-attach.":
            "Al pasarla a segundo plano, el agente sigue ejecutándose desacoplado — "
            "vuelve a abrir la sesión más tarde para reconectarte.",
        "Agents are asked to exit cleanly first; other running commands will be "
        "terminated. Backgrounding instead keeps the agents running detached — "
        "reopen a session later to re-attach.":
            "Primero se pide a los agentes que salgan limpiamente; los demás comandos "
            "en ejecución se terminarán. Al pasarlas a segundo plano, los agentes "
            "siguen ejecutándose desacoplados — vuelve a abrir una sesión más tarde "
            "para reconectarte.",
        "No session open": "Ninguna sesión abierta",
        "Pick a session from the sidebar, or start a new one.":
            "Elige una sesión en la barra lateral o inicia una nueva.",
        "Some transcripts could not be trashed":
            "No se pudieron enviar a la papelera algunas transcripciones",
        "Move {n} transcript(s) to trash?": "¿Mover {n} transcripción(es) a la papelera?",
        "The files are moved to the trash and can be restored.":
            "Los archivos se mueven a la papelera y se pueden restaurar.",
        "Move to Trash": "Mover a la papelera",
        "New window": "Nueva ventana",
        "Quit": "Salir",
        "Show Collins": "Mostrar Collins",
        "Show Collins (Hidden)": "Mostrar Collins (oculto)",
        "Collins is still running": "Collins sigue en ejecución",
        "Find it in the top bar.": "Encuéntralo en la barra superior.",
        "Reopen it by relaunching Collins, or from a session's notification.":
            "Vuelve a abrirlo iniciando Collins de nuevo o desde la "
            "notificación de una sesión.",
        "no sessions open": "no hay sesiones abiertas",
        "1 session": "1 sesión",
        "{n} sessions": "{n} sesiones",
        "1 working": "1 trabajando",
        "{n} working": "{n} trabajando",
        "1 unread": "1 sin leer",
        "{n} unread": "{n} sin leer",
        "You interrupted Claude here": "Interrumpiste a Claude aquí",
        "Restart now": "Reiniciar ahora",
        "Choose project directory": "Elegir directorio del proyecto",
        "Tab name": "Nombre de la pestaña",
        "Export session as Markdown": "Exportar sesión como Markdown",
        "Export failed": "Error al exportar",
        "Could not trash transcript": "No se pudo enviar la transcripción a la papelera",
        "Move transcript to trash?": "¿Mover la transcripción a la papelera?",
        "“{name}” will be removed from Claude's history.":
            "«{name}» se eliminará del historial de Claude.",
        "The file is moved to the trash and can be restored.":
            "El archivo se mueve a la papelera y se puede restaurar.",
        "Click to copy": "Haz clic para copiar",
        "Click to open": "Haz clic para abrir",
        "Click for actions": "Haz clic para ver las acciones",
        "Right-click to open": "Haz clic derecho para abrir",
        "Right-click to copy the link": "Haz clic derecho para copiar el enlace",
        "Click to view in Collins": "Haz clic para verlo en Collins",
        "Click to view #{number} in Collins":
            "Haz clic para ver #{number} en Collins",
        "Right-click for actions": "Haz clic derecho para ver las acciones",
        "Open on GitHub": "Abrir en GitHub",
        "Git pull failed": "Falló git pull",
        "git was not found on PATH.": "No se encontró git en el PATH.",
        "git exited with status {code}": "git terminó con estado {code}",
        "Pulled {project} — {summary}": "{project} actualizado — {summary}",
        "Pulled {project}": "{project} actualizado",
        "Rebase / resolve conflicts": "Rebase / resolver conflictos",
        "Has merge conflicts": "Tiene conflictos de fusión",
        "Has unresolved comments": "Tiene comentarios sin responder",
        # pull request footer chip
        "Open pull request": "Pull request abierto",
        "Draft pull request": "Pull request en borrador",
        "Merged pull request": "Pull request fusionado",
        "Closed pull request": "Pull request cerrado",
        "{n} passed": "{n} superada(s)",
        "{n} failed": "{n} fallida(s)",
        "{n} pending": "{n} pendiente(s)",
        "Couldn't open {name}: {message}": "No se pudo abrir «{name}»: {message}",
        "{name} is too large to open in the editor.":
            "«{name}» es demasiado grande para abrirlo en el editor.",
        "{name} looks like a binary file and can't be opened here.":
            "«{name}» parece un archivo binario y no se puede abrir aquí.",
        "{name} is not a file.": "«{name}» no es un archivo.",
        "{name} is outside this project and can't be opened here.":
            "«{name}» está fuera de este proyecto y no se puede abrir aquí.",
        "Couldn't open {name}.": "No se pudo abrir «{name}».",
        "{name} changed on disk": "«{name}» cambió en el disco",
        "Overwrite it with the changes you made here?":
            "¿Sobrescribirlo con los cambios que hiciste aquí?",
        "Overwrite": "Sobrescribir",
        "Couldn't save {name}: {message}": "No se pudo guardar «{name}»: {message}",
        "{name} was deleted.": "«{name}» se eliminó.",
        "{name} changed on disk.": "«{name}» cambió en el disco.",
        "Reload": "Recargar",
        "Couldn't reload {name}: {message}": "No se pudo recargar «{name}»: {message}",
        "Save Changes?": "¿Guardar los cambios?",
        "Don't Save": "No guardar",
        "“{name}” contains unsaved changes. Changes which are not saved will be permanently lost.":
            "«{name}» contiene cambios sin guardar. Los cambios que no se "
            "guarden se perderán permanentemente.",
        "{n} files contain unsaved changes. Changes which are not saved will be permanently lost.":
            "{n} archivos contienen cambios sin guardar. Los cambios que no se "
            "guarden se perderán permanentemente.",
        "Find in file…": "Buscar en el archivo…",
        "Save (Ctrl+S)": "Guardar (Ctrl+S)",
        "Plain Text": "Texto sin formato",
        "Show editor panel": "Mostrar el panel del editor",
        "Hide editor panel": "Ocultar el panel del editor",
        "Move editor to its own window": "Mover el editor a su propia ventana",
        "Bring editor back into this tab": "Devolver el editor a esta pestaña",
        "Move editor back into its tab": "Devolver el editor a su pestaña",
        "Editor": "Editor",
        "Follow app theme": "Seguir el tema de la aplicación",
        "Applies to the editor panel": "Se aplica al panel del editor",
        "Reset to system monospace": "Restablecer a la monoespaciada del sistema",
        "Show line numbers": "Mostrar números de línea",
        "Show hidden files": "Mostrar archivos ocultos",
        "Show dotfiles in the editor's file tree":
            "Mostrar archivos ocultos en el árbol de archivos del editor",
        "Open File": "Abrir archivo",
        "Open a file…": "Abrir un archivo…",
        "Indexing project files…": "Indexando archivos del proyecto…",
        "No files found in this project.": "No se encontraron archivos en este proyecto.",
        "Project is large — only the first {count} files are searchable.":
            "El proyecto es grande — solo se puede buscar entre los primeros {count} archivos.",
        "Agent files": "Archivos del agente",
        "Open {name} in the editor": "Abrir {name} en el editor",
        "Session tools": "Herramientas de sesión",
        "Tools a session can call to drive Collins. Turning one off takes "
        "effect immediately; sessions already running are only offered the "
        "tool again once they restart":
            "Herramientas que una sesión puede llamar para controlar Collins. "
            "Desactivar una surte efecto de inmediato; las sesiones ya en marcha "
            "solo vuelven a recibirla cuando se reinician",
        "Name its own session": "Ponerle nombre a su propia sesión",
        "set_session_title — the session titles its own tab and sidebar row":
            "set_session_title — la sesión titula su propia pestaña y su fila "
            "en la barra lateral",
        "Open files in the editor": "Abrir archivos en el editor",
        "open_in_editor — put a file from the project on screen, at a line":
            "open_in_editor — muestra en pantalla un archivo del proyecto, en una línea",
        "Show images": "Mostrar imágenes",
        "show_image — a screenshot, plot, render, or image URL in the in-app lightbox":
            "show_image — una captura, gráfico, render o URL de imagen en el visor de la aplicación",
        "Send desktop notifications": "Enviar notificaciones de escritorio",
        "notify_user — a notification titled with the session; clicking it opens the tab":
            "notify_user — una notificación titulada con la sesión; al pulsarla "
            "se abre la pestaña",
        "Attach pull requests": "Adjuntar pull requests",
        "attach_pr — put a pull request on the session's own footer and sidebar row":
            "attach_pr — coloca un pull request en el pie y la fila de la "
            "barra lateral de la sesión",
        "Open new pull requests automatically":
            "Abrir automáticamente los nuevos pull requests",
        "Open a pull request's panel beside its session as soon as "
        "the session picks the PR up. Once per pull request, so one "
        "you close again stays closed":
            "Abre el panel de un pull request junto a su sesión en cuanto "
            "la sesión lo adopta. Una vez por pull request, así que el que "
            "cierres seguirá cerrado",
        "Show the attachments panel automatically":
            "Mostrar automáticamente el panel de adjuntos",
        "Dock a session's attachments panel beside it the first "
        "time it shows an image — only in a tab wide enough to "
        "spare the column, past the terminal's maximum width. Once "
        "per session tab, so one you close again stays closed":
            "Acopla el panel de adjuntos de una sesión junto a ella la "
            "primera vez que muestra una imagen — solo en una pestaña lo "
            "bastante ancha para ceder la columna, más allá del ancho máximo "
            "de la terminal. Una vez por pestaña de sesión, así que el que "
            "cierres seguirá cerrado",
        "Open composer (Ctrl+.)": "Abrir el redactor (Ctrl+.)",
        "Attach file": "Adjuntar archivo",
        "Remove image": "Quitar imagen",
        "Close composer and keep the text in the terminal":
            "Cerrar el redactor y conservar el texto en la terminal",
        "Composer: the agent isn't running in this tab":
            "Redactor: el agente no se está ejecutando en esta pestaña",
        "Model switch: the agent isn't running in this tab":
            "Cambio de modelo: el agente no se está ejecutando en esta pestaña",
        "Click to switch the model": "Haz clic para cambiar el modelo",
        "Switch the model for this session": "Cambiar el modelo de esta sesión",
        "Loading models…": "Cargando modelos…",
        "Copy model id": "Copiar el id del modelo",
        "Composer": "Redactor",
        "Dock the composer below the terminal":
            "Acoplar el redactor debajo de la terminal",
        "Float the composer over the terminal":
            "Hacer flotar el redactor sobre la terminal",
        "Floating composer button": "Botón flotante del redactor",
        "Overlay a semi-transparent button on the corner of each agent "
        "terminal that opens the composer, a spell-checked prompt box":
            "Superpone un botón semitransparente en la esquina de cada "
            "terminal de agente que abre el redactor, un cuadro de texto "
            "con corrector ortográfico",
        "Enter sends composer text": "Enter envía el texto del redactor",
        "Off: Enter inserts a newline and Ctrl+Enter sends. "
        "Shift+Enter always inserts a newline":
            "Desactivado: Enter inserta una línea nueva y Ctrl+Enter envía. "
            "Mayús+Enter siempre inserta una línea nueva",
        "Composer in new sessions": "Redactor en sesiones nuevas",
        "Open the composer as soon as a new session starts — floating "
        "over the agent terminal, or docked as a panel below it, where "
        "it stays for the session's later visits":
            "Abrir el redactor en cuanto empiece una sesión nueva: "
            "flotando sobre la terminal del agente, o acoplado como panel "
            "debajo, donde permanece en las visitas posteriores a la sesión",
        "Never": "Nunca",
        "Floating": "Flotante",
        "Docked": "Acoplado",
        # the attachments panel (collins/attachpanel.py)
        "Attachments": "Adjuntos",
        "Close the attachments panel": "Cerrar el panel de adjuntos",
        "Dock the attachments panel beside the terminal":
            "Acoplar el panel de adjuntos junto a la terminal",
        "Float the attachments panel over the terminal":
            "Hacer flotar el panel de adjuntos sobre la terminal",
        "Images and files this session has seen":
            "Imágenes y archivos que ha visto esta sesión",
        "Images and files this session has seen ({n} new)":
            "Imágenes y archivos que ha visto esta sesión ({n} nuevos)",
        "Open With…": "Abrir con…",
        "Show in Folder": "Mostrar en la carpeta",
        "Copy Path": "Copiar la ruta",
        "Copy Address": "Copiar la dirección",
        "Remove From List": "Quitar de la lista",
        "No longer on disk": "Ya no está en el disco",
        "Couldn't be downloaded": "No se pudo descargar",
        "No attachments yet": "Todavía no hay adjuntos",
        "Pictures and files this session shares collect here.": "Las imágenes y archivos que esta sesión comparte se recogen aquí.",
        "that image isn't on disk any more: {path}": "esa imagen ya no está en el disco: {path}",
        "that file isn't on disk any more: {path}": "ese archivo ya no está en el disco: {path}",
        "couldn't download that image: {reason}": "no se pudo descargar esa imagen: {reason}",
        "Keep Running hides the window and leaves every session exactly as it is.":
            "Seguir en ejecución oculta la ventana y deja cada sesión "
            "exactamente como está.",
        "Keep Running (Hide Window)": "Seguir en ejecución (ocultar ventana)",
        "Hide Window": "Ocultar ventana",
        "Without a status icon, a hidden window comes back by relaunching "
        "Collins or clicking a session notification":
            "Sin icono de estado, una ventana oculta vuelve al reiniciar "
            "Collins o al pulsar una notificación de sesión",
        "Install the desktop entry, app icon and metainfo for the current user":
            "Instalar el lanzador de escritorio, el icono y los metadatos para el usuario actual",
        "Install desktop icon":
            "Instalar el icono de escritorio",
        "Couldn't install the desktop icon":
            "No se pudo instalar el icono de escritorio",
        "Collins is in your applications now":
            "Collins ya está en tus aplicaciones",
    },
    "fr": {
        "── restored panel history ──": "── historique du panneau restauré ──",
        "Rename session": "Renommer la session",
        "Custom name": "Nom personnalisé",
        "Cancel": "Annuler",
        "Session list": "Liste des sessions",
        "Show folder path": "Afficher le chemin du dossier",
        "Show each session's project folder path in the sidebar":
            "Afficher le chemin du dossier de projet de chaque session dans la barre latérale",
        "Startup": "Démarrage",
        "Reopen the last session": "Rouvrir la dernière session",
        "Open the session that was active when the app was last closed. "
        "Off, the app launches with no session open":
            "Ouvre la session qui était active à la dernière fermeture de "
            "l’application. Désactivé, l’application démarre sans session ouverte",
        "Show Claude usage": "Afficher l’utilisation de Claude",
        "Show subscription usage limits below the session list":
            "Afficher les limites d’utilisation de l’abonnement sous la liste des sessions",
        "Claude usage": "Utilisation de Claude",
        "Refresh usage": "Actualiser l’utilisation",
        "Checking usage…": "Vérification de l’utilisation…",
        "Not logged in to Claude": "Non connecté à Claude",
        "Claude login expired — run claude to refresh":
            "Connexion Claude expirée — lancez claude pour la renouveler",
        "Usage unavailable (offline)": "Utilisation indisponible (hors ligne)",
        "Usage unavailable": "Utilisation indisponible",
        "Couldn't refresh usage": "Impossible d’actualiser l’utilisation",
        "Dismiss": "Fermer",
        "Session (5h)": "Session (5 h)",
        "Week — all models": "Semaine — tous les modèles",
        "Week — {model}": "Semaine — {model}",
        "Week — model": "Semaine — modèle",
        "Resets in {t}": "Réinitialisation dans {t}",
        "As of {n}m ago": "Il y a {n} min",
        "Extra usage": "Utilisation supplémentaire",
        "{used} of {limit}": "{used} sur {limit}",
        "Extra usage: {used}": "Utilisation supplémentaire : {used}",
        " — limit reached": " — limite atteinte",
        "Open session file…": "Ouvrir un fichier de session…",
        "Open session transcript": "Ouvrir une transcription de session",
        "Session transcripts (*.jsonl)": "Transcriptions de session (*.jsonl)",
        "Could not open transcript": "Impossible d'ouvrir la transcription",
        "The file couldn't be read as a session transcript.":
            "Le fichier n'a pas pu être lu comme une transcription de session.",
        "Delete permanently…": "Supprimer définitivement…",
        "Could not delete transcript": "Impossible de supprimer la transcription",
        "Delete session permanently?": "Supprimer définitivement la session ?",
        "“{name}” and its transcript file will be permanently deleted. This cannot be undone.":
            "« {name} » et son fichier de transcription seront définitivement supprimés. "
            "Cette action est irréversible.",
        "Delete permanently": "Supprimer définitivement",
        "New {name} session (advanced)…": "Nouvelle session {name} (avancé)…",
        "Continue last {name} session…": "Continuer la dernière session {name}…",
        "New {name} session": "Nouvelle session {name}",
        "Optional flags for this session.": "Options facultatives pour cette session.",
        "Model": "Modèle",
        "Default": "Par défaut",
        "Permission mode": "Mode d'autorisation",
        "Extra directory": "Répertoire supplémentaire",
        "Choose…": "Choisir…",
        "Choose a directory": "Choisir un répertoire",
        "Start": "Démarrer",
        "Save": "Enregistrer",
        "Set tab emoji": "Définir l’emoji de l’onglet",
        "Shown before the tab title. Leave empty to remove.":
            "Affiché avant le titre de l’onglet. Laisser vide pour le retirer.",
        "e.g. 🚀": "p. ex. 🚀",
        "OK": "OK",
        "No MCP servers configured": "Aucun serveur MCP configuré",
        "Global": "Global",
        "Available to every project": "Disponible pour tous les projets",
        "MCP Servers": "Serveurs MCP",
        "Read-only": "Lecture seule",
        "Reading transcript…": "Lecture de la transcription…",
        "Session details": "Détails de la session",
        "Session ID": "ID de session",
        "Directory": "Répertoire",
        "unknown": "inconnu",
        "Created": "Créée",
        "Last activity": "Dernière activité",
        "Messages": "Messages",
        "Tool calls": "Appels d’outils",
        "Models": "Modèles",
        "Tokens": "Jetons",
        "Transcript size": "Taille de la transcription",
        "MCP": "MCP",
        "Available to this project": "Disponible pour ce projet",
        "Tools used in this session": "Outils utilisés dans cette session",
        "Recent activity": "Activité récente",
        "You": "Vous",
        "Claude": "Claude",
        "Follow system": "Suivre le système",
        "Light": "Clair",
        "Dark": "Sombre",
        "Preferences": "Préférences",
        "General": "Général",
        "Terminal": "Terminal",
        "Font": "Police",
        "Applies to all terminal tabs": "S’applique à tous les onglets de terminal",
        "New terminal tab": "Nouvel onglet de terminal",
        "Terminal {number}": "Terminal {number}",
        "A command is still running in this terminal tab and will be terminated.":
            "Une commande est encore en cours d’exécution dans cet onglet de "
            "terminal et sera interrompue.",
        "Reset to default font": "Réinitialiser la police par défaut",
        "Scrollback lines": "Lignes d’historique",
        "Easy copy & paste": "Copier-coller simplifié",
        "Ctrl+C copies selected text (otherwise interrupts as usual), Ctrl+V pastes, and right-click opens a copy/paste menu":
            "Ctrl+C copie le texte sélectionné (sinon interrompt comme d’habitude), Ctrl+V colle, et le clic droit ouvre un menu copier/coller",
        "Copy": "Copier",
        "Paste": "Coller",
        "Select All": "Tout sélectionner",
        "Color theme": "Thème de couleurs",
        "Appearance": "Apparence",
        "Color scheme": "Schéma de couleurs",
        "Language": "Langue",
        "Restart to apply": "Redémarrer pour appliquer",
        "Running sessions": "Sessions en cours",
        "Ask keeps the confirmation dialog; the other choices skip it and exit the "
        "session(s) cleanly or keep them running detached":
            "« Demander » conserve la boîte de dialogue de confirmation ; les autres choix "
            "la sautent et quittent proprement la ou les sessions, ou les laissent tourner détachées",
        "When archiving a running session": "Lors de l’archivage d’une session en cours",
        "Archiving a session that is still running also closes its tab":
            "Archiver une session encore en cours ferme aussi son onglet",
        "When quitting with running sessions": "À la fermeture avec des sessions en cours",
        "Closing a window while agent sessions are still running":
            "Fermer une fenêtre alors que des sessions d’agent sont encore en cours",
        "Archiving": "Archivage",
        "Archive on claude.ai too": "Archiver aussi sur claude.ai",
        "A session that also appears on claude.ai is archived and restored "
        "there along with the toggle here; best-effort, archiving locally "
        "never waits on it":
            "Une session qui apparaît aussi sur claude.ai y est archivée et "
            "restaurée avec la bascule d’ici ; au mieux — l’archivage local "
            "ne l’attend jamais",
        "Ask": "Demander",
        "Remove from favorites": "Retirer des favoris",
        "Add to favorites": "Ajouter aux favoris",
        "Sessions": "Sessions",
        "Select multiple sessions": "Sélectionner plusieurs sessions",
        "Show archived sessions": "Afficher les sessions archivées",
        "MCP servers": "Serveurs MCP",
        "About Collins": "À propos de Collins",
        "Refresh session list": "Actualiser la liste des sessions",
        "Search sessions…": "Rechercher des sessions…",
        "Search sessions": "Rechercher des sessions",
        "Close search": "Fermer la recherche",
        "A session is working": "Une session travaille",
        "Collapse all groups": "Réduire tous les groupes",
        "Expand all groups": "Développer tous les groupes",
        "No sessions found": "Aucune session trouvée",
        "{n} sessions": "{n} sessions",
        "{n} projects": "{n} projets",
        "{n} open": "{n} ouvertes",
        "Favorites": "Favoris",
        "Open": "Ouvrir",
        "Open in Ghostty": "Ouvrir dans Ghostty",
        "Fork session": "Bifurquer la session",
        "Rename…": "Renommer…",
        "Details…": "Détails…",
        "Copy session ID": "Copier l’ID de session",
        "Export as Markdown…": "Exporter en Markdown…",
        "Reveal transcript": "Afficher la transcription",
        "Restore session": "Restaurer la session",
        "Archive session": "Archiver la session",
        "Restore project": "Restaurer le projet",
        "Archive project": "Archiver le projet",
        "New session here": "Nouvelle session ici",
        "Open in {name}": "Ouvrir dans {name}",
        "Open In…": "Ouvrir dans…",
        "File Manager": "Gestionnaire de fichiers",
        "Right-click to open this folder in your terminal":
            "Clic droit pour ouvrir ce dossier dans votre terminal",
        "Open this folder in your file manager":
            "Ouvrir ce dossier dans votre gestionnaire de fichiers",
        "No terminal application found": "Aucune application de terminal trouvée",
        "Set $TERMINAL, or install a terminal emulator, to open folders here.":
            "Définissez $TERMINAL ou installez un émulateur de terminal "
            "pour ouvrir des dossiers ici.",
        "Move transcript to trash…": "Mettre la transcription à la corbeille…",
        "All": "Tout",
        "Select all (filtered) sessions": "Sélectionner toutes les sessions (filtrées)",
        "None": "Aucune",
        "Clear selection": "Effacer la sélection",
        "Move selected transcripts to trash…":
            "Mettre les transcriptions sélectionnées à la corbeille…",
        "Archive selected": "Archiver la sélection",
        "Remove selected from favorites": "Retirer la sélection des favoris",
        "Add selected to favorites": "Ajouter la sélection aux favoris",
        "Open selected in tabs": "Ouvrir la sélection dans des onglets",
        "warning: project dir {cwd} no longer exists, starting in {fallback}":
            "avertissement : le répertoire de projet {cwd} n’existe plus, démarrage dans {fallback}",
        "recreating removed worktree {path}":
            "recréation du worktree supprimé {path}",
        "couldn't create a worktree — starting the session in {cwd} instead":
            "impossible de créer un worktree — démarrage de la session dans {cwd}",
        "warning: `{cli}` not found in PATH — starting a plain shell":
            "avertissement : `{cli}` introuvable dans le PATH — démarrage d’un shell simple",
        "failed to start shell: {msg}": "échec du démarrage du shell : {msg}",
        "Find in terminal…": "Rechercher dans le terminal…",
        "Previous match": "Correspondance précédente",
        "Next match": "Correspondance suivante",
        "Set emoji…": "Définir un emoji…",
        "Close": "Fermer",
        "Toggle sidebar (F9)": "Afficher/masquer la barre latérale (F9)",
        "Show or hide the tab bar": "Afficher ou masquer la barre d’onglets",
        "New {name} session…": "Nouvelle session {name}…",
        "New session (Ctrl+Shift+T)": "Nouvelle session (Ctrl+Maj+T)",
        "Agent": "Agent",
        "Question": "Question",
        "New {name} native chat (experimental)…": "Nouvelle discussion native {name} (expérimental)…",
        "Chat with {name} — every file edit and command asks your permission first.":
            "Discussion avec {name} — chaque modification de fichier et commande "
            "demande d'abord votre permission.",
        "New {name} native chat ({mode}) (experimental)": "Nouvelle discussion native {name} ({mode}) (expérimental)",
        "Read-only chat with {name} — analyses and answers, never edits.":
            "Discussion en lecture seule avec {name} — analyse et répond, ne modifie jamais.",
        "Chat with {name} — ⚠ runs edits and commands automatically, without asking.":
            "Discussion avec {name} — ⚠ exécute les modifications et commandes "
            "automatiquement, sans demander.",
        "{name} wants to use {tool}": "{name} veut utiliser {tool}",
        "Allow once": "Autoriser une fois",
        "Always allow {tool}": "Toujours autoriser {tool}",
        "Deny": "Refuser",
        "Allowed {tool}.": "{tool} autorisé.",
        "Always allowing {tool}.": "{tool} toujours autorisé désormais.",
        "Denied {tool}.": "{tool} refusé.",
        "Auto-allowed {tool}": "{tool} autorisé automatiquement",
        "Ask {name}…": "Demander à {name}…",
        "Stop": "Arrêter",
        "{name} is thinking…": "{name} réfléchit…",
        "Rate limited — try again later.": "Limite atteinte — réessayez plus tard.",
        "Error: {msg}": "Erreur : {msg}",
        "Session ended.": "Session terminée.",
        "Replay…": "Rejouer…",
        "Replay — {name}": "Relecture — {name}",
        "Previous": "Précédent",
        "Play": "Lecture",
        "Next": "Suivant",
        "Show all": "Tout afficher",
        "Nothing to replay yet.": "Rien à rejouer pour l’instant.",
        "Chat — {dir}": "Discussion — {dir}",
        "Chat — {name}": "Discussion — {name}",
        "Continue in native chat (experimental)": "Continuer dans la discussion native (expérimental)",
        "Continue in native chat ({mode}) (experimental)": "Continuer dans la discussion native ({mode}) (expérimental)",
        "Continuing the previous session — earlier messages aren't shown here.":
            "Reprise de la session précédente — les messages antérieurs ne sont pas affichés ici.",
        "Exit session and close tab": "Quitter la session et fermer l’onglet",
        "Background session and close tab": "Session en arrière-plan et fermer l’onglet",
        "Exit Session": "Quitter la session",
        "Exit Sessions": "Quitter les sessions",
        "Background Session": "Session en arrière-plan",
        "Background Sessions": "Sessions en arrière-plan",
        "Backgrounding instead keeps the agent running detached — reopen the "
        "session later to re-attach.":
            "Le passage en arrière-plan laisse l'agent s'exécuter détaché — rouvrez "
            "la session plus tard pour vous y rattacher.",
        "Agents are asked to exit cleanly first; other running commands will be "
        "terminated. Backgrounding instead keeps the agents running detached — "
        "reopen a session later to re-attach.":
            "Les agents sont d'abord invités à quitter proprement ; les autres "
            "commandes en cours seront interrompues. Le passage en arrière-plan "
            "laisse les agents s'exécuter détachés — rouvrez une session plus tard "
            "pour vous y rattacher.",
        "No session open": "Aucune session ouverte",
        "Pick a session from the sidebar, or start a new one.":
            "Choisissez une session dans la barre latérale, ou démarrez-en une nouvelle.",
        "Some transcripts could not be trashed":
            "Certaines transcriptions n’ont pas pu être mises à la corbeille",
        "Move {n} transcript(s) to trash?": "Mettre {n} transcription(s) à la corbeille ?",
        "The files are moved to the trash and can be restored.":
            "Les fichiers sont mis à la corbeille et peuvent être restaurés.",
        "Move to Trash": "Mettre à la corbeille",
        "New window": "Nouvelle fenêtre",
        "Quit": "Quitter",
        "Show Collins": "Afficher Collins",
        "Show Collins (Hidden)": "Afficher Collins (masqué)",
        "Collins is still running": "Collins est toujours en cours d’exécution",
        "Find it in the top bar.": "Retrouvez-le dans la barre supérieure.",
        "Reopen it by relaunching Collins, or from a session's notification.":
            "Rouvrez-le en relançant Collins ou depuis la notification d’une session.",
        "no sessions open": "aucune session ouverte",
        "1 session": "1 session",
        "{n} sessions": "{n} sessions",
        "1 working": "1 en cours",
        "{n} working": "{n} en cours",
        "1 unread": "1 non lue",
        "{n} unread": "{n} non lues",
        "You interrupted Claude here": "Vous avez interrompu Claude ici",
        "Restart now": "Redémarrer maintenant",
        "Choose project directory": "Choisir le répertoire du projet",
        "Tab name": "Nom de l’onglet",
        "Export session as Markdown": "Exporter la session en Markdown",
        "Export failed": "Échec de l’exportation",
        "Could not trash transcript": "Impossible de mettre la transcription à la corbeille",
        "Move transcript to trash?": "Mettre la transcription à la corbeille ?",
        "“{name}” will be removed from Claude's history.":
            "« {name} » sera retiré de l’historique de Claude.",
        "The file is moved to the trash and can be restored.":
            "Le fichier est mis à la corbeille et peut être restauré.",
        "Click to copy": "Cliquer pour copier",
        "Click to open": "Cliquer pour ouvrir",
        "Click for actions": "Cliquer pour les actions",
        "Right-click to open": "Clic droit pour ouvrir",
        "Right-click to copy the link": "Clic droit pour copier le lien",
        "Click to view in Collins": "Cliquer pour l'ouvrir dans Collins",
        "Click to view #{number} in Collins":
            "Cliquer pour ouvrir #{number} dans Collins",
        "Right-click for actions": "Clic droit pour les actions",
        "Open on GitHub": "Ouvrir sur GitHub",
        "Git pull failed": "Échec de git pull",
        "git was not found on PATH.": "git est introuvable dans le PATH.",
        "git exited with status {code}": "git s'est terminé avec le statut {code}",
        "Pulled {project} — {summary}": "{project} mis à jour — {summary}",
        "Pulled {project}": "{project} mis à jour",
        "Rebase / resolve conflicts": "Rebase / résoudre les conflits",
        "Has merge conflicts": "A des conflits de fusion",
        "Has unresolved comments": "A des commentaires sans réponse",
        # pull request footer chip
        "Open pull request": "Pull request ouverte",
        "Draft pull request": "Brouillon de pull request",
        "Merged pull request": "Pull request fusionnée",
        "Closed pull request": "Pull request fermée",
        "{n} passed": "{n} réussie(s)",
        "{n} failed": "{n} échouée(s)",
        "{n} pending": "{n} en attente",
        "Couldn't open {name}: {message}": "Impossible d’ouvrir « {name} » : {message}",
        "{name} is too large to open in the editor.":
            "« {name} » est trop volumineux pour être ouvert dans l’éditeur.",
        "{name} looks like a binary file and can't be opened here.":
            "« {name} » ressemble à un fichier binaire et ne peut pas être ouvert ici.",
        "{name} is not a file.": "« {name} » n’est pas un fichier.",
        "{name} is outside this project and can't be opened here.":
            "« {name} » est en dehors de ce projet et ne peut pas être ouvert ici.",
        "Couldn't open {name}.": "Impossible d’ouvrir « {name} ».",
        "{name} changed on disk": "« {name} » a changé sur le disque",
        "Overwrite it with the changes you made here?":
            "L’écraser avec les modifications faites ici ?",
        "Overwrite": "Écraser",
        "Couldn't save {name}: {message}": "Impossible d’enregistrer « {name} » : {message}",
        "{name} was deleted.": "« {name} » a été supprimé.",
        "{name} changed on disk.": "« {name} » a changé sur le disque.",
        "Reload": "Recharger",
        "Couldn't reload {name}: {message}": "Impossible de recharger « {name} » : {message}",
        "Save Changes?": "Enregistrer les modifications ?",
        "Don't Save": "Ne pas enregistrer",
        "“{name}” contains unsaved changes. Changes which are not saved will be permanently lost.":
            "« {name} » contient des modifications non enregistrées. Les "
            "modifications non enregistrées seront définitivement perdues.",
        "{n} files contain unsaved changes. Changes which are not saved will be permanently lost.":
            "{n} fichiers contiennent des modifications non enregistrées. Les "
            "modifications non enregistrées seront définitivement perdues.",
        "Find in file…": "Rechercher dans le fichier…",
        "Save (Ctrl+S)": "Enregistrer (Ctrl+S)",
        "Plain Text": "Texte brut",
        "Show editor panel": "Afficher le panneau de l’éditeur",
        "Hide editor panel": "Masquer le panneau de l’éditeur",
        "Move editor to its own window": "Déplacer l’éditeur dans sa propre fenêtre",
        "Bring editor back into this tab": "Ramener l’éditeur dans cet onglet",
        "Move editor back into its tab": "Ramener l’éditeur dans son onglet",
        "Editor": "Éditeur",
        "Follow app theme": "Suivre le thème de l’application",
        "Applies to the editor panel": "S’applique au panneau de l’éditeur",
        "Reset to system monospace": "Réinitialiser à la police à chasse fixe du système",
        "Show line numbers": "Afficher les numéros de ligne",
        "Show hidden files": "Afficher les fichiers cachés",
        "Show dotfiles in the editor's file tree":
            "Afficher les fichiers cachés dans l’arborescence de l’éditeur",
        "Open File": "Ouvrir un fichier",
        "Open a file…": "Ouvrir un fichier…",
        "Indexing project files…": "Indexation des fichiers du projet…",
        "No files found in this project.": "Aucun fichier trouvé dans ce projet.",
        "Project is large — only the first {count} files are searchable.":
            "Le projet est volumineux — seuls les premiers {count} fichiers sont consultables.",
        "Agent files": "Fichiers de l’agent",
        "Open {name} in the editor": "Ouvrir {name} dans l’éditeur",
        "Session tools": "Outils de session",
        "Tools a session can call to drive Collins. Turning one off takes "
        "effect immediately; sessions already running are only offered the "
        "tool again once they restart":
            "Outils qu’une session peut appeler pour piloter Collins. La "
            "désactivation prend effet immédiatement ; les sessions déjà lancées "
            "ne retrouvent l’outil qu’à leur prochain démarrage",
        "Name its own session": "Nommer sa propre session",
        "set_session_title — the session titles its own tab and sidebar row":
            "set_session_title — la session nomme son propre onglet et sa ligne "
            "dans la barre latérale",
        "Open files in the editor": "Ouvrir des fichiers dans l’éditeur",
        "open_in_editor — put a file from the project on screen, at a line":
            "open_in_editor — affiche un fichier du projet à l’écran, à une ligne donnée",
        "Show images": "Afficher des images",
        "show_image — a screenshot, plot, render, or image URL in the in-app lightbox":
            "show_image — une capture, un graphique, un rendu ou une URL d’image dans la visionneuse intégrée",
        "Send desktop notifications": "Envoyer des notifications de bureau",
        "notify_user — a notification titled with the session; clicking it opens the tab":
            "notify_user — une notification au nom de la session ; un clic ouvre l’onglet",
        "Attach pull requests": "Attacher des pull requests",
        "attach_pr — put a pull request on the session's own footer and sidebar row":
            "attach_pr — place une pull request sur le pied de page et la "
            "ligne de barre latérale de la session",
        "Open new pull requests automatically":
            "Ouvrir automatiquement les nouvelles pull requests",
        "Open a pull request's panel beside its session as soon as "
        "the session picks the PR up. Once per pull request, so one "
        "you close again stays closed":
            "Ouvre le panneau d’une pull request à côté de sa session dès "
            "que la session l’adopte. Une fois par pull request : celle que "
            "vous refermez reste fermée",
        "Show the attachments panel automatically":
            "Afficher automatiquement le panneau des pièces jointes",
        "Dock a session's attachments panel beside it the first "
        "time it shows an image — only in a tab wide enough to "
        "spare the column, past the terminal's maximum width. Once "
        "per session tab, so one you close again stays closed":
            "Ancre le panneau des pièces jointes d’une session à côté "
            "d’elle dès qu’elle affiche une image — uniquement dans un "
            "onglet assez large pour céder la colonne, au-delà de la largeur "
            "maximale du terminal. Une fois par onglet de session : celui "
            "que vous refermez reste fermé",
        "Open composer (Ctrl+.)": "Ouvrir le rédacteur (Ctrl+.)",
        "Attach file": "Joindre un fichier",
        "Remove image": "Retirer l'image",
        "Close composer and keep the text in the terminal":
            "Fermer le rédacteur et garder le texte dans le terminal",
        "Composer: the agent isn't running in this tab":
            "Rédacteur : l'agent ne s'exécute pas dans cet onglet",
        "Model switch: the agent isn't running in this tab":
            "Changement de modèle : l'agent ne s'exécute pas dans cet onglet",
        "Click to switch the model": "Cliquer pour changer de modèle",
        "Switch the model for this session": "Changer le modèle de cette session",
        "Loading models…": "Chargement des modèles…",
        "Copy model id": "Copier l'id du modèle",
        "Composer": "Rédacteur",
        "Dock the composer below the terminal":
            "Ancrer le rédacteur sous le terminal",
        "Float the composer over the terminal":
            "Faire flotter le rédacteur au-dessus du terminal",
        "Floating composer button": "Bouton flottant du rédacteur",
        "Overlay a semi-transparent button on the corner of each agent "
        "terminal that opens the composer, a spell-checked prompt box":
            "Superpose un bouton semi-transparent dans le coin de chaque "
            "terminal d'agent qui ouvre le rédacteur, une zone de saisie "
            "avec correction orthographique",
        "Enter sends composer text": "Entrée envoie le texte du rédacteur",
        "Off: Enter inserts a newline and Ctrl+Enter sends. "
        "Shift+Enter always inserts a newline":
            "Désactivé : Entrée insère un saut de ligne et Ctrl+Entrée "
            "envoie. Maj+Entrée insère toujours un saut de ligne",
        "Composer in new sessions": "Rédacteur dans les nouvelles sessions",
        "Open the composer as soon as a new session starts — floating "
        "over the agent terminal, or docked as a panel below it, where "
        "it stays for the session's later visits":
            "Ouvrir le rédacteur dès qu'une nouvelle session démarre — "
            "flottant au-dessus du terminal de l'agent, ou ancré en panneau "
            "en dessous, où il reste lors des visites suivantes de la session",
        "Never": "Jamais",
        "Floating": "Flottant",
        "Docked": "Ancré",
        # the attachments panel (collins/attachpanel.py)
        "Attachments": "Pièces jointes",
        "Close the attachments panel": "Fermer le panneau des pièces jointes",
        "Dock the attachments panel beside the terminal":
            "Ancrer le panneau des pièces jointes à côté du terminal",
        "Float the attachments panel over the terminal":
            "Faire flotter le panneau des pièces jointes au-dessus du terminal",
        "Images and files this session has seen":
            "Images et fichiers vus par cette session",
        "Images and files this session has seen ({n} new)":
            "Images et fichiers vus par cette session ({n} nouveaux)",
        "Open With…": "Ouvrir avec…",
        "Show in Folder": "Afficher dans le dossier",
        "Copy Path": "Copier le chemin",
        "Copy Address": "Copier l'adresse",
        "Remove From List": "Retirer de la liste",
        "No longer on disk": "N'est plus sur le disque",
        "Couldn't be downloaded": "N'a pas pu être téléchargée",
        "No attachments yet": "Pas encore de pièces jointes",
        "Pictures and files this session shares collect here.": "Les images et fichiers que cette session partage s'accumulent ici.",
        "that image isn't on disk any more: {path}": "cette image n'est plus sur le disque : {path}",
        "that file isn't on disk any more: {path}": "ce fichier n'est plus sur le disque : {path}",
        "couldn't download that image: {reason}": "impossible de télécharger cette image : {reason}",
        "Keep Running hides the window and leaves every session exactly as it is.":
            "Continuer l'exécution masque la fenêtre et laisse chaque session "
            "exactement telle quelle.",
        "Keep Running (Hide Window)": "Continuer l'exécution (masquer la fenêtre)",
        "Hide Window": "Masquer la fenêtre",
        "Without a status icon, a hidden window comes back by relaunching "
        "Collins or clicking a session notification":
            "Sans icône d'état, une fenêtre masquée revient en relançant "
            "Collins ou en cliquant sur une notification de session",
        "Install the desktop entry, app icon and metainfo for the current user":
            "Installer le lanceur de bureau, l'icône et les métadonnées pour l'utilisateur actuel",
        "Install desktop icon":
            "Installer l'icône de bureau",
        "Couldn't install the desktop icon":
            "Impossible d'installer l'icône de bureau",
        "Collins is in your applications now":
            "Collins est maintenant dans vos applications",
    },
}

_HEADER = (
    "# Modified from the original agent-session-manager\n"
    "# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett\n"
    "# fork. Last modified: 2026-08-19. Full change history: git log for this file.\n"
    "# Generated by po/generate.py — do not edit by hand.\n"
    'msgid ""\n'
    'msgstr ""\n'
    '"Project-Id-Version: collins\\n"\n'
    '"Language: {lang}\\n"\n'
    '"MIME-Version: 1.0\\n"\n'
    '"Content-Type: text/plain; charset=UTF-8\\n"\n'
    '"Content-Transfer-Encoding: 8bit\\n"\n'
    '"Plural-Forms: nplurals=2; plural=(n != 1);\\n"\n\n'
)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def main() -> None:
    for lang, entries in TRANSLATIONS.items():
        po_path = Path(__file__).resolve().parent / f"{lang}.po"
        body = [_HEADER.format(lang=lang)]
        for msgid, msgstr in entries.items():
            body.append(f'msgid "{_escape(msgid)}"\n')
            body.append(f'msgstr "{_escape(msgstr)}"\n\n')
        po_path.write_text("".join(body), encoding="utf-8")

        mo_dir = LOCALE / lang / "LC_MESSAGES"
        mo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["msgfmt", "-o", str(mo_dir / f"{DOMAIN}.mo"), str(po_path)], check=True
        )
        print(f"{lang}: {len(entries)} strings → {po_path.name} + .mo")


if __name__ == "__main__":
    main()
