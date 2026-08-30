#!/usr/bin/env python3
# Modified from the original agent-session-manager
# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett
# fork. Last modified: 2026-08-30. Full change history: git log for this file.
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
        'Before you start':
            'Mielőtt elkezdené',
        "Collins runs Claude for you in a few places. Here's where, and the switches for each.":
            'A Collins néhány helyen Ön helyett futtatja a Claude-ot. Itt látható, hol — és mindegyikhez a kapcsoló.',
        'Continue':
            'Folytatás',
        'Using claude at {path}':
            'A claude innen fut: {path}',
        'Change it later in Preferences':
            'Később a Beállításokban módosítható',
        "Claude Code CLI":
            "Claude Code CLI",
        "Use This CLI":
            "Ezt a CLI-t használja",
        "Browse…":
            "Tallózás…",
        "Path to the claude executable":
            "A claude futtatható fájl elérési útja",
        "Collins needs the Claude Code CLI":
            "A Collinsnak szüksége van a Claude Code CLI-re",
        "Found it — Collins will remember this location.":
            "Megvan — a Collins megjegyzi ezt a helyet.",
        "Choose the claude executable":
            "Válassza ki a claude futtatható fájlt",
        "No Claude Code yet? Get it at {link}, then come back.":
            "Még nincs Claude Code? Szerezze be innen: {link}, majd térjen vissza.",
        "There's no executable file at this path.":
            "Ezen az elérési úton nincs futtatható fájl.",
        "It wasn't in any of the usual places — enter or browse to where it's installed.":
            "Nem volt a szokásos helyek egyikén sem — adja meg vagy tallózza ki, hova van telepítve.",
        "That's an executable, but not one named “claude” — pick the claude launcher itself.":
            "Ez futtatható fájl, de nem „claude” a neve — magát a claude indítót válassza.",
        "This is inside a version manager's tree, so Collins can't validate a stable path — it will work until that tool updates, and then this question comes back.":
            "Ez egy verziókezelő fájában van, így a Collins nem tud stabil elérési utat ellenőrizni — működni fog, amíg az az eszköz nem frissül, aztán ez a kérdés visszatér.",
        "This path has a version number in it, so it would break the next time Claude Code updates itself. Point at a stable launcher instead — usually ~/.local/bin/claude.":
            "Ebben az elérési útban verziószám van, így elromlana, amikor a Claude Code legközelebb frissíti magát. Mutasson inkább egy stabil indítóra — általában ~/.local/bin/claude.",
        "Every session runs through the claude command, and it isn't on the PATH that launches from the desktop are given — that PATH doesn't include the folders your shell adds. Point Collins at the CLI once; the location is remembered from then on.":
            "Minden munkamenet a claude parancson keresztül fut, és az nincs azon a PATH-on, amelyet az asztalról indított programok kapnak — az a PATH nem tartalmazza a shell által hozzáadott mappákat. Mutasson egyszer a CLI-re a Collinsban; a helyet ettől kezdve megjegyzi.",
        "Token use":
            "Tokenhasználat",
        "Each of these runs Claude on your behalf, against your subscription's usage limits, without a prompt from you. Every run is a headless claude -p from a scratch directory, carrying none of your skills, MCP servers, or the CLI's tools, so it never appears as a session and costs little more than its prompt.":
            "Ezek mindegyike az Ön nevében futtatja a Claude-ot, az előfizetése használati keretének terhére, anélkül, hogy Ön kérné. Minden futtatás egy fej nélküli claude -p egy ideiglenes könyvtárból, az Ön skilljei, MCP-kiszolgálói és a CLI eszközei nélkül, így sosem jelenik meg munkamenetként, és alig kerül többe a promptjánál.",
        "Auto-renew the Claude login":
            "A Claude-bejelentkezés automatikus megújítása",
        "When the login the usage panel and model list are fetched with has expired — at launch, or when a fetch is refused later — run one throwaway claude -p (a one-word prompt on Haiku) so the CLI renews it; off, the panel says to run claude yourself":
            "Ha lejárt a bejelentkezés, amellyel a használati panel és a modellista lekérése történik — indításkor, vagy amikor később egy lekérést elutasítanak —, egyetlen eldobható claude -p fut (egyszavas prompt Haiku-n), hogy a CLI megújítsa; kikapcsolva a panel azt kéri, hogy futtassa a claude-ot Ön",
        "{status} · free, no tokens":
            "{status} · ingyenes, nem használ tokent",
        'Names each new session from its first prompt — every session Collins sees under ~/.claude/projects, including ones an agent or a terminal started. None: sessions keep the first words of their prompt, which costs nothing':
            'Minden új munkamenetet az első promptja alapján nevez el — minden munkamenetet, amelyet a Collins a ~/.claude/projects alatt lát, az ügynök vagy terminál által indítottakat is. Egyik sem: a munkamenetek a promptjuk első szavait tartják meg, ami semmibe sem kerül',
        "Model the sidebar's Generate Icon dialog starts with. None: the dialog waits for you to pick a model and click Generate":
            'Az oldalsáv Ikon generálása párbeszédablakának kiinduló modellje. Egyik sem: az ablak megvárja, hogy modellt válasszon és a Generálás gombra kattintson',
        'Regenerate name ({model})':
            'Név újragenerálása ({model})',
        'Pick a model to generate an icon':
            'Válasszon modellt az ikon generálásához',
        'Generate':
            'Generálás',
        'Choose a model…':
            'Válasszon modellt…',
        'Add the Ubuntu PPA…': 'Az Ubuntu PPA hozzáadása…',
        'Add the package repository…': 'A csomagtároló hozzáadása…',
        'Add the Ubuntu PPA?': 'Hozzáadja az Ubuntu PPA-t?',
        "Collins isn't installed from ppa:episode6/stable yet. The PPA keeps it updated with the rest of the system: apt upgrade and the software updater both pick up new releases.":
            'A Collins még nincs a ppa:episode6/stable tárolóból telepítve. A PPA a rendszer többi részével együtt tartja naprakészen: az apt upgrade és a szoftverfrissítő is felveszi az új kiadásokat.',
        'Add the Fedora COPR…': 'A Fedora COPR hozzáadása…',
        'Add the Fedora COPR?': 'Hozzáadja a Fedora COPR-t?',
        "Collins isn't installed from the episode6/stable COPR yet. The COPR keeps it updated with the rest of the system: dnf upgrade and the software updater both pick up new releases.":
            'A Collins még nincs az episode6/stable COPR tárolóból telepítve. A COPR a rendszer többi részével együtt tartja naprakészen: a dnf upgrade és a szoftverfrissítő is felveszi az új kiadásokat.',
        "Collins isn't installed from its package repository yet.":
            'A Collins még nincs a csomagtárolójából telepítve.',
        'These commands ask for your password; they run in a terminal in this session.':
            'Ezek a parancsok a jelszavát kérik; ebben a munkamenetben, egy terminálban futnak.',
        'Run these in a terminal — they ask for your password.':
            'Futtassa ezeket egy terminálban — a jelszavát kérik.',
        'Run in Terminal': 'Futtatás terminálban',
        "Couldn't open a terminal": 'Nem sikerült terminált nyitni',
        'Run the commands in a terminal of your own instead.':
            'Futtassa a parancsokat inkább egy saját terminálban.',
        "── restored panel history ──": "── visszaállított panelelőzmények ──",
        "Rename session": "Munkamenet átnevezése",
        "Custom name": "Egyéni név",
        "Cancel": "Mégse",
        "Show folder paths in sidebar": "Mappák elérési útja az oldalsávban",
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
        "Continue last {name} session…": "Utolsó {name} munkamenet folytatása…",
        "Model": "Modell",
        "Default": "Alapértelmezett",
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
        "Color scheme": "Színséma",
        "Dark / Light Mode": "Sötét / világos mód",
        "Language": "Nyelv",
        "When archiving a running session": "Futó munkamenet archiválásakor",
        "Archiving a session that is still running also closes its tab":
            "A még futó munkamenet archiválása a lapját is bezárja",
        "When quitting with running sessions": "Kilépéskor futó munkamenetekkel",
        "Closing a window while agent sessions are still running":
            "Ablak bezárása, miközben ügynök-munkamenetek még futnak",
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
        "Search sessions…": "Munkamenetek keresése…",
        "Search sessions": "Munkamenetek keresése",
        "Close search": "Keresés bezárása",
        "A session is working": "Egy munkamenet dolgozik",
        "Collapse all groups": "Összes csoport összecsukása",
        "Expand all groups": "Összes csoport kibontása",
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
        "{n} sessions": "{n} munkamenet",
        "Finished a run": "Befejezett egy futást",
        "Default: the desktop's message sound": "Alapértelmezett: az asztal üzenethangja",
        "Silent": "Néma",
        "Bell": "Csengő",
        "Complete": "Kész",
        "Message": "Üzenet",
        "Information": "Információ",
        "Zen": "Zen",
        "Soft": "Lágy",
        "Glass": "Üveg",
        "Confirmation": "Megerősítés",
        "Pluck": "Pendítés",
        "The desktop's “{event}” sound": "Az asztal „{event}” hangja",
        "Ships with Collins: {source} (CC0)": "A Collins része: {source} (CC0)",
        "Rang the bell": "Csengetett",
        "In-app notifications": "Alkalmazáson belüli értesítések",
        "Card theme": "Kártya témája",
        "The in-app card's own light or dark, whatever the app is": "A kártya saját világos vagy sötét módja, bármilyen is az alkalmazás",
        "Follow app": "Alkalmazás követése",
        "Show a message from another session inside the window while Collins is focused. Off sends every notification to the desktop": "Másik munkamenet üzenetének megjelenítése az ablakban, amíg a Collins fókuszban van. Kikapcsolva minden értesítés az asztalra megy",
        "Sound": "Hang",
        "Custom…": "Egyéni…",
        "Play the notification sound": "Értesítési hang lejátszása",
        "Choose a different sound file": "Másik hangfájl kiválasztása",
        "Bells from other sessions": "Csengetés más munkamenetekből",
        "A terminal bell from a session you aren't looking at posts a notification and plays the sound. Off keeps the desktop's beep": "Egy éppen nem nézett munkamenet terminálcsengetése értesítést küld és lejátssza a hangot. Kikapcsolva az asztal sípolása marad",
        "Announce finished runs": "Befejezett futások bejelentése",
        "Also notify when a session's run finishes, not only when it asks for you": "Akkor is értesítsen, amikor egy munkamenet futása befejeződik, ne csak amikor Önt kéri",
        "Check for updates": "Frissítések keresése",
        "Ask GitHub once a day whether a newer Collins is out, and notify you when one is. Through your gh login, or anonymously": "Naponta egyszer megkérdezi a GitHubot, van-e újabb Collins, és értesít, ha van. A gh bejelentkezésén keresztül, vagy névtelenül",
        "Collins {version} is available": "Elérhető a Collins {version}",
        "You're running {version}. Click to open the release on GitHub": "Ön a(z) {version} verziót használja. Kattintson a kiadás megnyitásához a GitHubon",
        "Sound needs GStreamer ({package}); the desktop's beep is used instead": "A hanghoz GStreamer ({package}) kell; helyette az asztal sípolása szól",
        "Choose a notification sound": "Válasszon értesítési hangot",
        "Sound files": "Hangfájlok",
        "Notifications": "Értesítések",
        "1 unread notification": "1 olvasatlan értesítés",
        "{n} unread notifications": "{n} olvasatlan értesítés",
        "just now": "épp most",
        "{n}s ago": "{n} mp-e",
        "{n}m ago": "{n} perce",
        "{n}h ago": "{n} órája",
        "yesterday": "tegnap",
        "{n}d ago": "{n} napja",
        "{body} ×{n}": "{body} ×{n}",
        "Untitled session": "Névtelen munkamenet",
        "Mark all read": "Mind olvasott",
        "Mark every notification read": "Minden értesítés megjelölése olvasottként",
        "Clear": "Törlés",
        "Remove every notification": "Minden értesítés eltávolítása",
        "Unread": "Olvasatlan",
        "Earlier": "Korábban",
        "No notifications": "Nincs értesítés",
        "Messages from sessions you aren't looking at, and bells, land here.": "Ide érkeznek az épp nem nézett munkamenetek üzenetei és a csengetések.",
        "Mark read": "Olvasottnak jelöl",
        "Remove": "Eltávolítás",
        "Sound: {name}": "Hang: {name}",
        "Preferences…": "Beállítások…",
        "Show/hide notifications": "Értesítések megjelenítése/elrejtése",
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
        "Right-click to open on GitHub": "Jobb kattintás a GitHubon való megnyitáshoz",
        "Open on GitHub": "Megnyitás GitHubon",
        "Git pull failed": "A git pull sikertelen",
        "git was not found on PATH.": "A git nem található a PATH-on.",
        "git exited with status {code}": "A git {code} státuszkóddal lépett ki",
        "Pulled {project} — {summary}": "{project} frissítve — {summary}",
        "Pulled {project}": "{project} frissítve",
        "Checkout {branch}": "{branch} ág kivétele",
        "Checkout default branch": "Az alapértelmezett ág kivétele",
        "Git checkout failed": "A git checkout sikertelen",
        "Checked out {branch} in {project}": "{branch} kivéve itt: {project}",
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
        "Back to files": "Vissza a fájlokhoz",
        "Single column when narrow": "Egy oszlop, ha keskeny",
        "An editor column this many pixels wide or narrower shows the file tree and the open file one at a time, with a back button beside the tabs (0 = always side by side)":
            "Egy ennyi képpont széles vagy keskenyebb szerkesztőoszlop a fájlfát és a megnyitott fájlt felváltva mutatja, a lapok mellett egy vissza gombbal (0 = mindig egymás mellett)",
        "Open File": "Fájl megnyitása",
        "Open a file…": "Fájl megnyitása…",
        "Indexing project files…": "Projektfájlok indexelése…",
        "No files found in this project.": "Nem található fájl ebben a projektben.",
        "Project is large — only the first {count} files are searchable.":
            "A projekt nagy — csak az első {count} fájl kereshető.",
        "Agent files": "Ügynökfájlok",
        "Open {name} in the editor": "A(z) {name} megnyitása a szerkesztőben",
        "Session behavior": "Munkamenetek viselkedése",
        "Composer": "Szerkesztő",
        "Built-in MCP tools": "Beépített MCP-eszközök",
        "Every enabled tool's definition rides in each session's context, "
        "read_terminal sends the panel's text into the conversation, and a "
        "session start_session starts is titled like any other. Turning one "
        "off takes effect immediately; sessions already running are only "
        "offered the tool again once they restart":
            "Minden bekapcsolt eszköz leírása minden munkamenet kontextusába "
            "bekerül, a read_terminal a panel szövegét küldi a beszélgetésbe, a "
            "start_session által indított munkamenet pedig ugyanúgy címet kap, "
            "mint bármelyik másik. A kikapcsolás azonnal érvénybe lép; a már futó "
            "munkamenetek csak újraindításuk után kapják vissza az eszközt",
        "Name its own session": "Elnevezheti a saját munkamenetét",
        "set_session_title — the session titles its own tab and sidebar row":
            "set_session_title — a munkamenet elnevezi a saját lapját és oldalsávsorát",
        "Open files in the editor": "Fájlok megnyitása a szerkesztőben",
        "open_in_editor — put a file from the project on screen, at a line":
            "open_in_editor — a projekt egy fájlját a képernyőre teszi, adott sornál",
        "Show images": "Képek megjelenítése",
        "show_image — a screenshot, plot, render, or image URL in the in-app lightbox":
            "show_image — képernyőkép, diagram, render vagy képhivatkozás az alkalmazás képnézegetőjében",
        "Send notifications": "Értesítések küldése",
        "notify_user — a card in the window or a desktop notification, titled with the session; clicking it opens the tab":
            "notify_user — kártya az ablakban vagy asztali értesítés a munkamenet nevével; kattintásra megnyílik a lap",
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
        'Add application':
            'Alkalmazás hozzáadása',
        'Search applications…':
            'Alkalmazások keresése…',
        'Caffeine Mode is on':
            'A Caffeine Mode be van kapcsolva',
        "That file isn't an image Collins can display.":
            'Ez a fájl nem olyan kép, amelyet a Collins meg tud jeleníteni.',
        'Until idle':
            'Tétlenségig',
        'Indefinitely':
            'Korlátlan ideig',
        '1 hour':
            '1 óra',
        '{n} hours':
            '{n} óra',
        'Caffeine Mode is dozing until a session works again — then the computer and screen will stay awake':
            'A Caffeine Mode szunnyad, amíg egy munkamenet újra dolgozni nem kezd — akkor a számítógép és a képernyő ébren marad',
        'Caffeine Mode is dozing until a session works again — then the computer will stay awake, the screen may turn off':
            'A Caffeine Mode szunnyad, amíg egy munkamenet újra dolgozni nem kezd — akkor a számítógép ébren marad, a képernyő kikapcsolhat',
        'Caffeine Mode is on while sessions are working — the computer and screen will stay awake':
            'A Caffeine Mode aktív, amíg munkamenetek dolgoznak — a számítógép és a képernyő ébren marad',
        'Caffeine Mode is on while sessions are working — the computer will stay awake, the screen may turn off':
            'A Caffeine Mode aktív, amíg munkamenetek dolgoznak — a számítógép ébren marad, a képernyő kikapcsolhat',
        'Caffeine Mode is on — the computer and screen will stay awake':
            'A Caffeine Mode be van kapcsolva — a számítógép és a képernyő ébren marad',
        'Caffeine Mode is on — the computer will stay awake, the screen may turn off':
            'A Caffeine Mode be van kapcsolva — a számítógép ébren marad, a képernyő kikapcsolhat',
        'Caffeine Mode: keep the computer awake and the screen on':
            'Caffeine Mode: a számítógép ébren, a képernyő bekapcsolva marad',
        'Caffeine Mode: keep the computer awake, letting the screen turn off':
            'Caffeine Mode: a számítógép ébren marad, a képernyő kikapcsolhat',
        'Caffeine Mode turns off in {time} — computer and screen stay awake':
            'A Caffeine Mode {time} múlva kikapcsol — a számítógép és a képernyő ébren marad',
        'Caffeine Mode turns off in {time} — computer stays awake, screen may turn off':
            'A Caffeine Mode {time} múlva kikapcsol — a számítógép ébren marad, a képernyő kikapcsolhat',
        'Send':
            'Küldés',
        'The user declined this action.':
            'A felhasználó elutasította ezt a műveletet.',
        'Switch the effort level for this session':
            'Erőfeszítési szint váltása ehhez a munkamenethez',
        "couldn't save a copy of the dropped image":
            'nem sikerült másolatot menteni a bedobott képről',
        "skipped {n} item that isn't a local file":
            '{n} elem kihagyva, amely nem helyi fájl',
        "couldn't reference {n} dropped file name":
            'nem sikerült hivatkozni {n} bedobott fájlnévre',
        "couldn't save a copy of the pasted image":
            'nem sikerült másolatot menteni a beillesztett képről',
        'Effort':
            'Erőfeszítés',
        'Copied to clipboard':
            'Vágólapra másolva',
        'Rename folder':
            'Mappa átnevezése',
        'Rename file':
            'Fájl átnevezése',
        'Enter a new name for “{name}”.':
            'Adjon meg új nevet ennek: „{name}”.',
        'Rename':
            'Átnevezés',
        'Move editor to {name}?':
            'Áthelyezi a szerkesztőt ide: {name}?',
        'This session is now working in {path}. One open file has unsaved changes and also exists there — choose what happens to it.':
            'Ez a munkamenet mostantól itt dolgozik: {path}. Egy megnyitott fájlban mentetlen módosítások vannak, és a fájl ott is létezik — válassza ki, mi történjen vele.',
        'Stay':
            'Maradás',
        'Go on editing this file, where your unsaved changes belong':
            'E fájl szerkesztésének folytatása ott, ahová a mentetlen módosításai tartoznak',
        'Take edits':
            'Módosítások átvitele',
        'Move this tab to the new copy, keeping your unsaved changes — saving will write them over whatever that copy holds':
            'A lap áthelyezése az új másolatra a mentetlen módosítások megtartásával — a mentés felülírja velük azt, ami abban a másolatban van',
        'Use new':
            'Új használata',
        'Open the new copy and discard your unsaved changes':
            'Az új másolat megnyitása és a mentetlen módosítások elvetése',
        "Don't Move":
            'Ne helyezze át',
        'Move Editor':
            'Szerkesztő áthelyezése',
        'Do you trust this folder?':
            'Megbízik ebben a mappában?',
        '{agent} will be able to read, edit and execute files in\n\n{path}\n\nand everything inside it, including any worktrees it creates there. Open it only if this is a project you created or otherwise trust — like your own code, a well-known open source project, or work from your team.':
            'A(z) {agent} olvashatja, szerkesztheti és futtathatja a fájlokat itt:\n\n{path}\n\nés mindent, ami benne van, az ott létrehozott munkafákat is beleértve. Csak akkor nyissa meg, ha ez egy Ön által létrehozott vagy egyébként megbízható projekt — például a saját kódja, egy jól ismert nyílt forráskódú projekt vagy a csapata munkája.',
        'Trust and open':
            'Megbízom benne, megnyitás',
        'Generating icon…':
            'Ikon generálása…',
        'At sidebar size':
            'Oldalsávméretben',
        'Optional adjustments, e.g. “make it blue”':
            'Opcionális igazítások, pl. „legyen kék”',
        'Regenerate':
            'Újragenerálás',
        'Default model':
            'Alapértelmezett modell',
        "Model for this dialog's runs; Preferences sets the default":
            'A párbeszédablak futtatásainak modellje; az alapértelmezettet a Beállítások adja meg',
        'Default ({model})':
            'Alapértelmezett ({model})',
        'Generate Icon':
            'Ikon generálása',
        'the generated SVG could not be rendered':
            'a generált SVG nem renderelhető',
        'Icon generation failed: {error}':
            'Az ikongenerálás sikertelen: {error}',
        'Saving failed: {error}':
            'A mentés sikertelen: {error}',
        'Close other tabs':
            'Többi lap bezárása',
        'Close tabs to the right':
            'Jobbra lévő lapok bezárása',
        'Close all tabs':
            'Összes lap bezárása',
        'Add to chat':
            'Hozzáadás a csevegéshez',
        "Couldn't rename {name}: {message}":
            'A(z) „{name}” nem nevezhető át: {message}',
        'A name is needed to rename {name}.':
            'A(z) „{name}” átnevezéséhez név szükséges.',
        "“{new_name}” isn't a name — renaming can't move things elsewhere.":
            'A(z) „{new_name}” nem név — az átnevezés nem helyezhet át semmit máshová.',
        '“{new_name}” already exists here.':
            'A(z) „{new_name}” már létezik itt.',
        '{name} is no longer there.':
            'A(z) „{name}” már nincs ott.',
        "{name} can't be renamed to something outside this project.":
            'A(z) „{name}” nem nevezhető át a projekten kívülre.',
        "There's nothing on the clipboard to paste here.":
            'Nincs a vágólapon semmi, amit ide be lehetne illeszteni.',
        "{count} item couldn't be pasted.":
            '{count} elemet nem sikerült beilleszteni.',
        "{name} can't be pasted into itself.":
            'A(z) „{name}” nem illeszthető be önmagába.',
        'That folder is no longer there.':
            'Ez a mappa már nincs ott.',
        "{name} can't be pasted outside this project.":
            'A(z) „{name}” nem illeszthető be a projekten kívülre.',
        'There are already too many copies of {name} here.':
            'Már túl sok másolat van itt a(z) „{name}” elemből.',
        "Couldn't paste {name}: {message}":
            'A(z) „{name}” nem illeszthető be: {message}',
        "{name} couldn't be decoded as an image.":
            'A(z) „{name}” nem dekódolható képként.',
        'Session moved to {name}':
            'Munkamenet áthelyezve ide: {name}',
        'Follow':
            'Követés',
        'Image':
            'Kép',
        'Cut':
            'Kivágás',
        '{n} session(s) in {p} project(s) have their transcripts moved to the trash, where they can be restored. Sessions archived with their whole project — and originals a backgrounded fork replaced — are included.':
            '{n} munkamenet átirata {p} projektben a kukába kerül, ahonnan visszaállítható. Az egész projektjükkel együtt archivált munkamenetek — és a háttérbe küldött elágazás által lecserélt eredetik — szintén beleszámítanak.',
        '{project} — {n} of {total}':
            '{project} — {n} / {total}',
        '…and {p} other project(s) — {n} session(s)':
            '…és {p} további projekt — {n} munkamenet',
        '{p} of these project(s) lose every session they have.':
            'E projektek közül {p} minden munkamenetét elveszíti.',
        'Open, every check passed':
            'Nyitott, minden ellenőrzés sikeres',
        'Checks still running':
            'Az ellenőrzések még futnak',
        'A check failed':
            'Egy ellenőrzés sikertelen',
        'A reviewer is waiting on a reply':
            'Egy véleményező válaszra vár',
        'Draft, and the branch conflicts':
            'Piszkozat, és az ág konfliktusban van',
        'Merged':
            'Egyesítve',
        'Collins is better with the GitHub CLI':
            'A Collins jobb a GitHub CLI-vel',
        "Collins follows the pull requests your sessions open — and acts on them — through gh, GitHub's own command-line tool, which isn't installed here.":
            'A Collins a gh-val, a GitHub saját parancssori eszközével követi a munkamenetei által nyitott pull requesteket — és cselekszik is velük —, de az itt nincs telepítve.',
        "Collins follows the pull requests your sessions open — and acts on them — through gh, GitHub's own command-line tool, which is installed here but never signed in.":
            'A Collins a gh-val, a GitHub saját parancssori eszközével követi a munkamenetei által nyitott pull requesteket — és cselekszik is velük —, de az telepítve van ugyan, ám sosem jelentkezett be.',
        'Not now':
            'Most nem',
        'Get the GitHub CLI':
            'GitHub CLI beszerzése',
        'Copy command':
            'Parancs másolása',
        "With it, every session's pull requests carry their status:":
            'Vele minden munkamenet pull requestjei magukon hordják az állapotukat:',
        '…and a click on one does something about it:':
            '…és egy rájuk kattintás tesz is valamit:',
        "Don't show this again":
            'Ne jelenjen meg többé',
        'Install it from cli.github.com — Collins picks it up the next time it starts.':
            'Telepítse a cli.github.com oldalról — a Collins a következő indításakor felveszi.',
        'Run this once in any terminal. Collins asks for no login of its own.':
            'Futtassa ezt egyszer bármelyik terminálban. A Collins saját bejelentkezést nem kér.',
        'Keyboard Bindings':
            'Billentyűparancsok',
        'Reset All':
            'Összes visszaállítása',
        'Put every shortcut back to its default':
            'Minden gyorsbillentyű visszaállítása az alapértelmezettre',
        'Click a row to change its shortcut':
            'Kattintson egy sorra a gyorsbillentyűje módosításához',
        'Also bound to: {actions}':
            'Ehhez is hozzárendelve: {actions}',
        'Unbound':
            'Nincs hozzárendelve',
        'Reset to default':
            'Visszaállítás alapértelmezettre',
        'Reset every shortcut?':
            'Minden gyorsbillentyű visszaállítása?',
        'All of your custom keyboard bindings are replaced by the defaults.':
            'Minden egyéni billentyűparancsát az alapértelmezettek váltják fel.',
        '{chord} is already in use':
            'A(z) {chord} már használatban van',
        'It is bound to {actions}. Move it to {action}?':
            'Ehhez van hozzárendelve: {actions}. Áthelyezi ide: {action}?',
        'Move Shortcut':
            'Gyorsbillentyű áthelyezése',
        'unbound':
            'nincs hozzárendelve',
        'Set shortcut for “{action}”':
            'Gyorsbillentyű beállítása: „{action}”',
        'Press the new key combination. Currently: {current}.\nBackspace removes the binding; Escape keeps it.':
            'Nyomja le az új billentyűkombinációt. Jelenleg: {current}.\nA Backspace törli a hozzárendelést; az Escape megtartja.',
        'Tabs and windows':
            'Lapok és ablakok',
        'Panels':
            'Panelek',
        'Application':
            'Alkalmazás',
        'New session':
            'Új munkamenet',
        'Quick switcher':
            'Gyorsváltó',
        'Archive the current session':
            'A jelenlegi munkamenet archiválása',
        'Undo the last archive':
            'Az utolsó archiválás visszavonása',
        'Open the pull request page':
            'A pull request oldalának megnyitása',
        "Unbound by default; the sidebar's search button does the same.":
            'Alapértelmezetten nincs hozzárendelve; az oldalsáv keresőgombja ugyanezt teszi.',
        'Close tab':
            'Lap bezárása',
        'Next tab':
            'Következő lap',
        'Previous tab':
            'Előző lap',
        'Toggle the tab marker':
            'A lapjelölő ki/be kapcsolása',
        'Show/hide the sidebar':
            'Oldalsáv megjelenítése/elrejtése',
        'Show/hide the terminal panel':
            'Terminálpanel megjelenítése/elrejtése',
        'Clear the terminal panel':
            'Terminálpanel törlése',
        'Move the panel tab to the other side':
            'A panellap áthelyezése a másik oldalra',
        'Show/hide the composer':
            'Üzenetszerkesztő megjelenítése/elrejtése',
        'Show/hide the attachments gallery':
            'Mellékletgaléria megjelenítése/elrejtése',
        "Swap the panel's sides":
            'A panel oldalainak felcserélése',
        'Unbound by default.':
            'Alapértelmezetten nincs hozzárendelve.',
        'Move the panel tab to the other strip':
            'A panellap áthelyezése a másik sávra',
        'Show/hide the editor':
            'Szerkesztő megjelenítése/elrejtése',
        'Quick open a file':
            'Fájl gyors megnyitása',
        'Focus the editor':
            'Fókusz a szerkesztőre',
        'Save the file':
            'Fájl mentése',
        'In the editor.':
            'A szerkesztőben.',
        'Find in the file':
            'Keresés a fájlban',
        'Copy the selection':
            'Kijelölés másolása',
        'With easy copy and paste on; without a selection the key reaches the terminal.':
            'Az egyszerű másolás és beillesztés bekapcsolt állapotában; kijelölés nélkül a billentyű a terminálba jut.',
        'With easy copy and paste on.':
            'Az egyszerű másolás és beillesztés bekapcsolt állapotában.',
        'Copy (terminal-style)':
            'Másolás (terminál módra)',
        'Paste (terminal-style)':
            'Beillesztés (terminál módra)',
        'Find in the terminal':
            'Keresés a terminálban',
        'Insert a newline in the prompt':
            'Új sor beszúrása a promptba',
        'Zoom in':
            'Nagyítás',
        'Zoom out':
            'Kicsinyítés',
        'Reset zoom':
            'Nagyítás visszaállítása',
        'Keyboard bindings':
            'Billentyűparancsok',
        "Couldn't display image":
            'A kép nem jeleníthető meg',
        'Open in Editor':
            'Megnyitás a szerkesztőben',
        'Low':
            'Alacsony',
        'Medium':
            'Közepes',
        'High':
            'Magas',
        'Extra high':
            'Extra magas',
        'Max':
            'Maximális',
        'This model has no effort setting':
            'Ennek a modellnek nincs erőfeszítés-beállítása',
        'New chat':
            'Új csevegés',
        'Model to start this session on':
            'A modell, amellyel ez a munkamenet indul',
        'Effort level to start this session at':
            'Az erőfeszítési szint, amellyel ez a munkamenet indul',
        'New git worktree':
            'Új git munkafa',
        'Work in a fresh worktree of this project, apart from its uncommitted changes':
            'Munka a projekt friss munkafájában, elkülönítve a nem véglegesített módosításaitól',
        'Empty Session':
            'Üres munkamenet',
        'Start the session with no prompt':
            'Munkamenet indítása prompt nélkül',
        'Drag to move this tab: drop on an edge to split, on a strip to join':
            'Húzással áthelyezheti a lapot: élre ejtve feloszt, sávra ejtve csatlakozik',
        'Restore this tab to its size and place in the panel':
            'A lap visszaállítása a panelbeli méretére és helyére',
        'Close Tab':
            'Lap bezárása',
        'Overlay this tab over the whole session':
            'A lap ráterítése az egész munkamenetre',
        'Move this tab to the other side':
            'A lap áthelyezése a másik oldalra',
        'Close tab with a running command?':
            'Bezárja a lapot, amelyen parancs fut?',
        'Move to':
            'Áthelyezés ide',
        'Split Left':
            'Felosztás balra',
        'Split Right':
            'Felosztás jobbra',
        'Split Up':
            'Felosztás felfelé',
        'Split Down':
            'Felosztás lefelé',
        'Close tabs with running commands?':
            'Bezárja a lapokat, amelyeken parancsok futnak?',
        'A command is still running in one of these tabs and will be terminated.':
            'Az egyik lapon még fut egy parancs, amely le lesz állítva.',
        'Close Tabs':
            'Lapok bezárása',
        'Address unresolved comments':
            'Megválaszolatlan hozzászólások kezelése',
        'Send “{prompt}” to this session':
            '„{prompt}” küldése ennek a munkamenetnek',
        'Open a pull request':
            'Pull request nyitása',
        'Fix errors & resolve conflicts':
            'Hibák javítása és konfliktusok feloldása',
        'Address the CI errors':
            'A CI-hibák kezelése',
        'Fix errors':
            'Hibák javítása',
        'Resolve conflicts':
            'Konfliktusok feloldása',
        'Mark ready for review':
            'Megjelölés véleményezésre késznek',
        'Take {slug} out of draft':
            'A(z) {slug} kivétele piszkozatból',
        'Ready':
            'Kész',
        'Ask Claude for a review':
            'Véleményezés kérése a Claude-tól',
        'Comment “{comment}” on {slug}':
            '„{comment}” hozzászólás a(z) {slug} pull requesthez',
        'Merge when checks pass':
            'Egyesítés, ha az ellenőrzések sikeresek',
        'Turn on auto-merge for {slug}':
            'Automatikus egyesítés bekapcsolása a(z) {slug} pull requesthez',
        'Merge {slug} when its checks pass?':
            'Egyesíti a(z) {slug} pull requestet, amint az ellenőrzései sikeresek?',
        'GitHub merges it as soon as every required check has passed. You can still cancel auto-merge on the pull request page.':
            'A GitHub egyesíti, amint minden kötelező ellenőrzés sikeres. Az automatikus egyesítést a pull request oldalán továbbra is lemondhatja.',
        'Enable auto-merge':
            'Automatikus egyesítés bekapcsolása',
        'Auto-Merge':
            'Automatikus egyesítés',
        'Merge pull request':
            'Pull request egyesítése',
        'Merge {slug} now':
            'A(z) {slug} egyesítése most',
        'Merge {slug}?':
            'Egyesíti a(z) {slug} pull requestet?',
        'Merge':
            'Egyesítés',
        'Disable auto-merge':
            'Automatikus egyesítés kikapcsolása',
        'Stop GitHub from merging {slug} when its checks pass':
            'A GitHub ne egyesítse a(z) {slug} pull requestet, amikor az ellenőrzései sikeresek',
        'Disable Auto-Merge':
            'Automatikus egyesítés kikapcsolása',
        'Its checks have passed. This merges the pull request on GitHub now.':
            'Az ellenőrzései sikeresek. Ez most egyesíti a pull requestet a GitHubon.',
        "Its checks haven't all passed. This merges the pull request on GitHub now, if the repository lets it.":
            'Nem minden ellenőrzése sikeres. Ez most egyesíti a pull requestet a GitHubon, ha a tároló engedi.',
        'Merge and archive session':
            'Egyesítés és a munkamenet archiválása',
        'Merge {slug} now, then archive this session':
            'A(z) {slug} egyesítése most, majd a munkamenet archiválása',
        'Merge {slug} and archive this session?':
            'Egyesíti a(z) {slug} pull requestet, és archiválja ezt a munkamenetet?',
        'The session is archived once the merge lands — you can bring it back with Undo, or from “Show archived”.':
            'A munkamenet az egyesítés megtörténtekor archiválódik — a Visszavonással vagy az „Archivált munkamenetek megjelenítése” alól hozható vissza.',
        'Merge & archive':
            'Egyesítés és archiválás',
        'Mark ready & merge when checks pass':
            'Késznek jelölés és egyesítés, ha az ellenőrzések sikeresek',
        'Take {slug} out of draft, then turn on auto-merge':
            'A(z) {slug} kivétele piszkozatból, majd az automatikus egyesítés bekapcsolása',
        'Mark {slug} ready and merge it when its checks pass?':
            'Késznek jelöli a(z) {slug} pull requestet, és egyesíti, amint az ellenőrzései sikeresek?',
        'Ready & auto-merge':
            'Kész és automatikus egyesítés',
        'Ready & Auto-Merge':
            'Kész és automatikus egyesítés',
        'Mark ready & merge':
            'Késznek jelölés és egyesítés',
        'Take {slug} out of draft, then merge it now':
            'A(z) {slug} kivétele piszkozatból, majd egyesítése most',
        'Mark {slug} ready and merge it?':
            'Késznek jelöli a(z) {slug} pull requestet, és egyesíti?',
        'Ready & merge':
            'Kész és egyesítés',
        'Ready & Merge':
            'Kész és egyesítés',
        'Mark ready, merge & archive session':
            'Késznek jelölés, egyesítés és a munkamenet archiválása',
        'Take {slug} out of draft, merge it now, then archive this session':
            'A(z) {slug} kivétele piszkozatból, egyesítése most, majd a munkamenet archiválása',
        'Mark {slug} ready, merge it and archive this session?':
            'Késznek jelöli a(z) {slug} pull requestet, egyesíti, és archiválja ezt a munkamenetet?',
        'Ready, merge & archive':
            'Kész, egyesítés és archiválás',
        'The pull request is marked ready for review first.':
            'A pull request előbb véleményezésre késznek lesz jelölve.',
        'Close pull request':
            'Pull request lezárása',
        'Close {slug} without merging':
            'A(z) {slug} lezárása egyesítés nélkül',
        'Close {slug}?':
            'Lezárja a(z) {slug} pull requestet?',
        'The pull request is closed without merging. Its branch and its comments stay, and it can be reopened on GitHub.':
            'A pull request egyesítés nélkül lesz lezárva. Az ága és a hozzászólásai megmaradnak, és a GitHubon újranyitható.',
        "{url} doesn't look like a pull request.":
            'A(z) {url} nem tűnik pull requestnek.',
        "Collins doesn't know how to do that.":
            'A Collins ezt nem tudja megtenni.',
        'Merge conflicts':
            'Beolvasztási konfliktusok',
        'Refresh':
            'Frissítés',
        'Search settings…':
            'Keresés a beállításokban…',
        'Search settings':
            'Keresés a beállításokban',
        'No settings found':
            'Nincs találat a beállításokban',
        'Try a different search.':
            'Próbáljon másik keresőkifejezést.',
        'Tab drag handles':
            'Lapok húzófogantyúi',
        'Drag any panel tab by its handle to move, reorder, or split it. Relies on GTK internals — turn off to fall back to plain tab dragging plus a drag grip on each panel':
            'Bármely panellap a fogantyújánál fogva húzható áthelyezéshez, átrendezéshez vagy felosztáshoz. GTK-belsőkre támaszkodik — kikapcsolva sima laphúzás marad, panelenkénti húzófüllel',
        'Project icon size':
            'Projektikonok mérete',
        'Size of the project and folder icons in the sidebar':
            'A projekt- és mappaikonok mérete az oldalsávban',
        'Start new sessions in a git worktree':
            'Új munkamenetek indítása git munkafában',
        "Git projects only; each new session works in its own fresh worktree, so it won't see uncommitted local changes. Right-click a project header to override per project":
            'Csak git projektekre; minden új munkamenet a saját friss munkafájában dolgozik, így nem látja a nem véglegesített helyi módosításokat. Projektenkénti felülbíráláshoz kattintson jobb gombbal a projekt fejlécére',
        "Follow Claude's own session names":
            'A Claude saját munkamenetneveinek követése',
        'Rename sessions whenever Claude names or renames them — /rename and its automatic titles; manually renamed sessions keep their name':
            'Munkamenetek átnevezése, valahányszor a Claude elnevezi vagy átnevezi őket — a /rename és az automatikus címei; a kézzel átnevezett munkamenetek megtartják a nevüket',
        'Exact busy tracking from the agent':
            'Pontos elfoglaltságkövetés az ügynöktől',
        "Read Claude Code's own progress announcements for the sidebar's working indicator, instead of only inferring from terminal output (fully applies to newly opened tabs)":
            'A Claude Code saját haladásjelzéseinek olvasása az oldalsáv dolgozik-jelzőjéhez, a pusztán terminálkimenetből való következtetés helyett (teljesen csak az újonnan megnyitott lapokra érvényes)',
        'Poll for background sessions':
            'Háttér-munkamenetek lekérdezése',
        'Fallback: check the agent CLI every 20 seconds in case the yellow guide lines stop updating on their own':
            'Tartalék: az ügynök CLI ellenőrzése 20 másodpercenként, arra az esetre, ha a sárga jelzővonalak maguktól már nem frissülnének',
        'Typing opens the composer':
            'A gépelés megnyitja az üzenetszerkesztőt',
        "Start typing at an agent's empty prompt and the composer opens with what you typed. A dialog, a menu and the CLI's own /, !, # and @ keep their keys":
            'Kezdjen gépelni az ügynök üres promptjánál, és az üzenetszerkesztő megnyílik a beírtakkal. A párbeszédablakok, a menük és a CLI saját /, !, # és @ jelei megtartják a billentyűiket',
        'Right-click aims spell-check':
            'A jobb kattintás irányítja a helyesírás-ellenőrzést',
        'Right-clicking a misspelled word in the composer offers corrections for that word. Off: corrections follow the text cursor instead, and a right-click never moves it':
            'Az üzenetszerkesztőben egy hibásan írt szóra jobb gombbal kattintva ahhoz a szóhoz kap javításokat. Kikapcsolva: a javítások a szövegkurzort követik, és a jobb kattintás sosem mozdítja azt',
        'Max width':
            'Legnagyobb szélesség',
        'Stop growing past this width and center in the tab instead (0 = no limit)':
            'Ennél a szélességnél ne nőjön tovább, hanem igazodjon a lap közepére (0 = nincs korlát)',
        'Footer apps':
            'Lábléc-alkalmazások',
        "Buttons in each tab's footer that open the tab's directory":
            'Gombok az egyes lapok láblécén, amelyek a lap könyvtárát nyitják meg',
        'Add application…':
            'Alkalmazás hozzáadása…',
        'Pull requests':
            'Pull requestek',
        'Text size':
            'Szövegméret',
        'Reading-text size in the pull request panel, as a percentage of the app font; buttons and menus keep the app size':
            'Az olvasószöveg mérete a pull request panelen, az alkalmazás betűméretének százalékában; a gombok és a menük az alkalmazás méretén maradnak',
        'Show embedded images':
            'Beágyazott képek megjelenítése',
        'Render the images a description or comment embeds, and the changed image files, as pictures; click one to open it full size. Off, they stay links and patches, and opening a pull request downloads nothing':
            'A leírásba vagy hozzászólásba ágyazott képek és a módosult képfájlok megjelenítése képként; kattintásra teljes méretben nyílnak meg. Kikapcsolva hivatkozások és patchek maradnak, és a pull request megnyitása semmit sem tölt le',
        'Confirm before merging':
            'Megerősítés egyesítés előtt',
        'Ask before merging a pull request, enabling auto-merge, or merging and archiving the session. Off, the click merges; closing a pull request unmerged still asks either way':
            'Rákérdezés pull request egyesítése, automatikus egyesítés bekapcsolása, vagy egyesítés és a munkamenet archiválása előtt. Kikapcsolva a kattintás egyesít; a pull request egyesítés nélküli lezárása így is, úgy is rákérdez',
        'Attach pull requests named in prompts':
            'A promptokban említett pull requestek csatolása',
        "Put every pull request a new session's first prompt mentions on that session's row, without waiting for the agent to touch it":
            'Az új munkamenet első promptjában említett minden pull request felkerül a munkamenet sorára, meg sem várva, hogy az ügynök hozzányúljon',
        'Rename sessions after their pull requests':
            'Munkamenetek átnevezése a pull requestjeik után',
        'Retitle a session to match the newest pull request opened in it; manually renamed sessions keep their name':
            'A munkamenet címének igazítása a benne nyitott legújabb pull requesthez; a kézzel átnevezett munkamenetek megtartják a nevüket',
        'Refresh pull requests at launch':
            'Pull requestek frissítése indításkor',
        "Ask GitHub about every listed session's pull requests once on startup, so the marks in the sidebar start out current rather than as they were left":
            'Induláskor egyszer megkérdezi a GitHubot minden listázott munkamenet pull requestjeiről, hogy az oldalsáv jelölései frissen induljanak, ne úgy, ahogy maradtak',
        'Caffeine Mode':
            'Caffeine Mode',
        'Keep screen on':
            'Képernyő bekapcsolva tartása',
        'Hold the screen on as well as keeping the computer awake. Off lets the screen turn off as usual, while an unattended agent still keeps the computer from sleeping':
            'A képernyőt is bekapcsolva tartja, nem csak a számítógépet ébren. Kikapcsolva a képernyő szokás szerint kikapcsolhat, miközben a felügyelet nélküli ügynök továbbra sem hagyja elaludni a számítógépet',
        'Until idle grace period':
            'Tétlenségig türelmi ideje',
        'How many minutes Until idle keeps the computer awake after the last session stops working; any session picking work back up restarts the wait':
            'Hány percig tartja ébren a Tétlenségig a számítógépet, miután az utolsó munkamenet abbahagyta a munkát; ha bármely munkamenet újra dolgozni kezd, a várakozás újraindul',
        'Turn on at launch':
            'Bekapcsolás indításkor',
        'Start with Caffeine Mode already on, keeping the computer awake until you turn it off from the header':
            'Indulás már bekapcsolt Caffeine Mode-dal, ébren tartva a számítógépet, amíg a fejlécből ki nem kapcsolja',
        'Turn off after':
            'Kikapcsolás ennyi idő után',
        'Open in a window on small screens':
            'Megnyitás ablakban kis képernyőn',
        'On screens this many pixels wide or narrower (after display scaling), the editor opens in its own window instead of a panel (0 = always open as a panel)':
            'Az ennyi képpont széles vagy keskenyebb képernyőkön (a kijelzőskálázás után) a szerkesztő saját ablakban nyílik meg panel helyett (0 = mindig panelként nyíljon)',
        'Show status icon':
            'Állapotikon megjelenítése',
        'Shows Collins in the top bar, with a menu that jumps to any open session':
            'Megjeleníti a Collinst a felső sávban, egy menüvel, amely bármelyik nyitott munkamenetre ugrik',
        'No status-icon support was found in this desktop — GNOME needs an AppIndicator extension':
            'Ezen az asztali környezetben nincs állapotikon-támogatás — a GNOME-hoz AppIndicator-kiterjesztés kell',
        'Nothing on this desktop can show a status icon':
            'Ezen az asztali környezetben semmi sem tud állapotikont megjeleníteni',
        'Using the claude found on PATH at {path}.':
            'A PATH-on talált claude használata innen: {path}.',
        "claude isn't on PATH — Collins will ask where it is at the next launch.":
            'A claude nincs a PATH-on — a Collins a következő indításkor megkérdezi, hol van.',
        'How long that launch-time Caffeine Mode runs before it turns itself off. Until idle never does: it holds the computer awake while any session is working (and {n} minute past), dozing in between':
            'Meddig fut az indításkori Caffeine Mode, mielőtt magától kikapcsol. A Tétlenségig sosem kapcsol ki: ébren tartja a számítógépet, amíg bármely munkamenet dolgozik (és utána {n} percig), közben szunnyad',
        'Move up':
            'Mozgatás fel',
        'Move down':
            'Mozgatás le',
        'No apps configured':
            'Nincs beállított alkalmazás',
        'Before':
            'Előtte',
        'After':
            'Utána',
        'Back to the pull requests':
            'Vissza a pull requestekhez',
        'View in Collins':
            'Megtekintés a Collinsban',
        "Open this pull request's page beside the session":
            'A pull request oldalának megnyitása a munkamenet mellett',
        'View unresolved comments':
            'Megválaszolatlan hozzászólások megtekintése',
        "Open this pull request's page at its first unresolved thread":
            'A pull request oldalának megnyitása az első megválaszolatlan szálnál',
        "Collins couldn't run that action.":
            'A Collins nem tudta futtatni ezt a műveletet.',
        '{action} failed':
            'A(z) {action} sikertelen',
        'Pull request':
            'Pull request',
        'Merging when checks pass':
            'Egyesítés, amint az ellenőrzések sikeresek',
        "The GitHub CLI (gh) isn't installed, or isn't on PATH.":
            'A GitHub CLI (gh) nincs telepítve, vagy nincs a PATH-on.',
        "Collins couldn't run gh.":
            'A Collins nem tudta futtatni a gh-t.',
        'gh exited with status {code}.':
            'A gh {code} státuszkóddal lépett ki.',
        'and {n} more':
            'és még {n} további',
        'Approved':
            'Jóváhagyva',
        'Changes requested':
            'Módosítások kérve',
        'Review dismissed':
            'Vélemény elvetve',
        'Commented':
            'Hozzászólt',
        'Reload this pull request':
            'A pull request újratöltése',
        'Conversation':
            'Beszélgetés',
        'Files':
            'Fájlok',
        "Couldn't load this pull request — is the GitHub CLI signed in?":
            'A pull request nem tölthető be — be van jelentkezve a GitHub CLI?',
        'Nothing loaded yet.':
            'Még semmi sincs betöltve.',
        'Merges {head} into {base}':
            'A(z) {head} egyesítése ebbe: {base}',
        '{n} file':
            '{n} fájl',
        'No comments yet.':
            'Még nincs hozzászólás.',
        'No description provided.':
            'Nincs leírás.',
        'No changed files.':
            'Nincsenek módosult fájlok.',
        'Checks ({n})':
            'Ellenőrzések ({n})',
        'Checks':
            'Ellenőrzések',
        'More actions':
            'További műveletek',
        'Right-click for more actions':
            'Jobb kattintás a további műveletekhez',
        'Add a comment':
            'Hozzászólás írása',
        'Request changes':
            'Módosítások kérése',
        'Approve':
            'Jóváhagyás',
        'Comment':
            'Hozzászólás',
        'Comment on {slug}':
            'Hozzászólás a(z) {slug} pull requesthez',
        'Approve {slug}':
            'A(z) {slug} jóváhagyása',
        'Request changes on {slug}':
            'Módosítások kérése a(z) {slug} pull requesthez',
        'Address comments':
            'Hozzászólások kezelése',
        'Request review':
            'Véleményezés kérése',
        'Outdated':
            'Elavult',
        'The code this thread commented on has changed':
            'A kód, amelyhez ez a szál hozzászólt, azóta megváltozott',
        'Resolved':
            'Megoldva',
        'Reply':
            'Válasz',
        'Reply in this thread':
            'Válasz ebben a szálban',
        'Unresolve':
            'Megoldás visszavonása',
        'Resolve':
            'Megoldás',
        'Reopen this thread':
            'A szál újranyitása',
        'Mark this thread resolved':
            'A szál megjelölése megoldottként',
        'Post reply':
            'Válasz elküldése',
        'no diff — binary or too large':
            'nincs diff — bináris vagy túl nagy',
        '{n} line':
            '{n} sor',
        'Show more':
            'Több megjelenítése',
        'Show less':
            'Kevesebb megjelenítése',
        'New session in {path}':
            'Új munkamenet itt: {path}',
        'Expand':
            'Kibontás',
        'Collapse':
            'Összecsukás',
        'New Thread':
            'Új szál',
        'Discard draft and close tab':
            'Piszkozat elvetése és a lap bezárása',
        'Discard draft':
            'Piszkozat elvetése',
        'Backgrounding is unavailable until this session is registered and any handoff in progress finishes':
            'A háttérbe küldés nem érhető el, amíg ez a munkamenet nincs regisztrálva, és a folyamatban lévő átadás be nem fejeződik',
        'This session has no tab open.':
            'Ennek a munkamenetnek nincs nyitott lapja.',
        'Delete archived sessions…':
            'Archivált munkamenetek törlése…',
        'Add project':
            'Projekt hozzáadása',
        'Refresh session list and pull requests':
            'Munkamenetlista és pull requestek frissítése',
        'Chats':
            'Csevegések',
        'Draft':
            'Piszkozat',
        'Refreshing pull requests…':
            'Pull requestek frissítése…',
        'Open in new window':
            'Megnyitás új ablakban',
        'Move to new window':
            'Áthelyezés új ablakba',
        'Rename to match PR':
            'Átnevezés a PR-hoz igazítva',
        'Rename to match PR #{number}':
            'Átnevezés a(z) #{number} PR-hoz igazítva',
        'Repair session link':
            'Munkamenet-kapcsolat javítása',
        'New session in new window':
            'Új munkamenet új ablakban',
        'New session here (no worktree)':
            'Új munkamenet itt (munkafa nélkül)',
        'New session here (in a worktree)':
            'Új munkamenet itt (munkafában)',
        'New sessions use a worktree':
            'Az új munkamenetek munkafát használnak',
        'Git pull ({branch})':
            'Git pull ({branch})',
        'Git pull':
            'Git pull',
        'Remove project from sidebar':
            'Projekt eltávolítása az oldalsávból',
        'replaced':
            'lecserélve',
        'Open composer':
            'Üzenetszerkesztő megnyitása',
        "the agent didn't start — your prompt is kept in the composer":
            'az ügynök nem indult el — a prompt az üzenetszerkesztőben megmaradt',
        'Every pull request this session has opened':
            'A munkamenet által nyitott összes pull request',
        'Show/hide terminal panel':
            'Terminálpanel megjelenítése/elrejtése',
        'Move terminals to {name}?':
            'Áthelyezi a terminálokat ide: {name}?',
        'This session started in a worktree at {path}. The terminal open beside it is still in the project directory — change its directory to the worktree? A terminal running a command is left alone.':
            'Ez a munkamenet munkafában indult itt: {path}. A mellette nyitott terminál még a projektkönyvtárban van — átállítja a könyvtárát a munkafára? A parancsot futtató terminál békén marad.',
        'Change Directory':
            'Könyvtárváltás',
        '{n} terminal is running a command and stayed where it was':
            '{n} terminál parancsot futtat, és ott maradt, ahol volt',
        'Effort: {level}':
            'Erőfeszítés: {level}',
        'Click to switch the effort level':
            'Kattintson az erőfeszítési szint váltásához',
        'No pull request found for this branch':
            'Nem található pull request ehhez az ághoz',
        "Re-check this branch's pull requests":
            'Az ág pull requestjeinek újraellenőrzése',
        "Look for this branch's pull request":
            'Az ág pull requestjének megkeresése',
        "Add to chat: the agent isn't running in this tab":
            'Hozzáadás a csevegéshez: ezen a lapon nem fut az ügynök',
        "Add to chat isn't available for this file":
            'A Hozzáadás a csevegéshez nem érhető el ehhez a fájlhoz',
        "skipped {n} dropped item that isn't a local file":
            '{n} bedobott elem kihagyva, amely nem helyi fájl',
        "Composer: the input box holds a paste Collins can't read":
            'Üzenetszerkesztő: a beviteli mezőben olyan beillesztés van, amelyet a Collins nem tud beolvasni',
        "Effort switch: the agent isn't running in this tab":
            'Erőfeszítés-váltás: ezen a lapon nem fut az ügynök',
        "This session isn't at an empty prompt.":
            'Ez a munkamenet nem üres promptnál áll.',
        'Start sessions in the background':
            'Munkamenetek indítása a háttérben',
        'start_session — spawn a sibling agent in a new background tab, with a prompt':
            'start_session — testvérügynök indítása új háttérlapon, prompttal',
        'Read the terminal panel':
            'A terminálpanel olvasása',
        "read_terminal — the panel tabs' text and scrollback, your own typing included":
            'read_terminal — a panellapok szövege és visszagörgetése, a saját gépelését is beleértve',
        'Run commands in the terminal panel':
            'Parancsok futtatása a terminálpanelen',
        'run_in_terminal — type a command into an idle panel tab (or a new one) and run it':
            'run_in_terminal — parancs beírása egy tétlen panellapra (vagy egy újra) és futtatása',
        'Default (latest Haiku)':
            'Alapértelmezett (legújabb Haiku)',
        'Default (latest Sonnet)':
            'Alapértelmezett (legújabb Sonnet)',
        'Session title model':
            'Munkamenetcímek modellje',
        'Icon generation model':
            'Ikongenerálás modellje',
        'Model list':
            'Modellista',
        'Checking…':
            'Ellenőrzés…',
        'Ask Anthropic for the model list now, rather than waiting for the saved one to age out':
            'A modellista lekérése az Anthropictól most, nem várva a mentett lista elavulására',
        "Couldn't reach Anthropic — offering the CLI's aliases (opus, sonnet, haiku)":
            'Az Anthropic nem érhető el — a CLI aliasai érhetők el (opus, sonnet, haiku)',
        "Couldn't reach Anthropic — still showing the list fetched {when}":
            'Az Anthropic nem érhető el — továbbra is a(z) {when} lekért lista látható',
        '{count} models, updated {when}':
            '{count} modell, frissítve {when}',
        'Waiting for this session to be registered — backgrounding it now would leave the agent with no way back to it':
            'Várakozás a munkamenet regisztrálására — ha most háttérbe kerülne, az ügynöknek nem lenne útja vissza hozzá',
        'Another session is still being handed to the background — one at a time':
            'Egy másik munkamenet átadása a háttérnek még tart — egyszerre csak egy',
        'New chat (scratch folder)':
            'Új csevegés (ideiglenes mappa)',
        'Close window with {n} active session(s)?':
            'Bezárja az ablakot {n} aktív munkamenettel?',
        'Agents are asked to exit cleanly first; other running commands will be terminated.':
            'Az ügynököket először tiszta kilépésre kérjük; a többi futó parancs leáll.',
        'Backgrounding sessions…':
            'Munkamenetek háttérbe küldése…',
        'Quit Now':
            'Kilépés most',
        'Handing each session to a background agent, one at a time, so every one is paired with the agent it becomes. {done} of {total} done.':
            'Minden munkamenet átadása egy-egy háttérügynöknek, egyenként, hogy mindegyik azzal az ügynökkel párosuljon, amellyé válik. {done} / {total} kész.',
        'New session in {project} (no worktree)':
            'Új munkamenet itt: {project} (munkafa nélkül)',
        'New session in {project} (in a worktree)':
            'Új munkamenet itt: {project} (munkafában)',
        'Could not create chat directory':
            'Nem sikerült létrehozni a csevegéskönyvtárat',
        'Trust and add':
            'Megbízom benne, hozzáadás',
        'Discard draft?':
            'Elveti a piszkozatot?',
        '“{label}” will be forgotten, along with any terminal panel it kept.':
            'A(z) „{label}” el lesz felejtve, a megtartott terminálpaneljével együtt.',
        'Discard':
            'Elvetés',
        "Couldn't send that to the session":
            'Nem sikerült elküldeni a munkamenetnek',
        'Close tab with an active session?':
            'Bezárja a lapot aktív munkamenettel?',
        "The agent is asked to exit cleanly first; the command running in this tab's terminal panel will be terminated.":
            'Az ügynököt először tiszta kilépésre kérjük; a lap terminálpaneljén futó parancs leáll.',
        'The agent is asked to exit cleanly first.':
            'Az ügynököt először tiszta kilépésre kérjük.',
        "A command is still running in this tab's terminal panel and will be terminated.":
            'A lap terminálpaneljén még fut egy parancs, amely le lesz állítva.',
        "Backgrounding isn't available yet: this session hasn't been registered, so a detached agent would have no way back to it.":
            'A háttérbe küldés még nem érhető el: ez a munkamenet nincs regisztrálva, így a leválasztott ügynöknek nem lenne útja vissza hozzá.',
        "Backgrounding isn't available right now: another session is still being handed to the background.":
            'A háttérbe küldés most nem érhető el: egy másik munkamenet átadása a háttérnek még tart.',
        'No matching agent':
            'Nincs egyező ügynök',
        'No background agent matches this session — either its agent is gone, or more than one candidate matched and guessing would link the wrong one. The transcript itself is intact.':
            'Egyik háttérügynök sem illik ehhez a munkamenethez — vagy eltűnt az ügynöke, vagy több jelölt is egyezett, és a találgatás rosszat kapcsolna hozzá. Maga az átirat sértetlen.',
        'Session linked':
            'Munkamenet összekapcsolva',
        'Linked to its running background agent.':
            'Összekapcsolva a futó háttérügynökével.',
        'Nothing to repair':
            'Nincs mit javítani',
        'This session is already its own background agent. Opening it attaches to that agent.':
            'Ez a munkamenet már a saját háttérügynöke. Megnyitása ahhoz az ügynökhöz csatlakozik.',
        'No pull request is linked to this session yet':
            'Ehhez a munkamenethez még nincs pull request kapcsolva',
        'Undo':
            'Visszavonás',
        'Archived “{name}”':
            'A(z) „{name}” archiválva',
        'Archived {n} sessions':
            '{n} munkamenet archiválva',
        'Delete {n} archived session(s)?':
            'Töröl {n} archivált munkamenetet?',
        'Keep the {p} emptied project(s) in the sidebar':
            'A(z) {p} kiürült projekt megtartása az oldalsávban',
        'Manage and resume your AI coding agent sessions.\n\nUnofficial community tool — not affiliated with or endorsed by Anthropic.':
            'Kezelje és folytassa AI kódolóügynök-munkameneteit.\n\nNem hivatalos közösségi eszköz — nem áll kapcsolatban az Anthropickal, és az nem is támogatja.',
    },
    "de": {
        'Before you start':
            'Bevor Sie loslegen',
        "Collins runs Claude for you in a few places. Here's where, and the switches for each.":
            'Collins führt Claude an einigen Stellen für Sie aus. Hier steht, wo — und der Schalter für jede davon.',
        'Continue':
            'Weiter',
        'Using claude at {path}':
            'Verwendet claude unter {path}',
        'Change it later in Preferences':
            'Später in den Einstellungen änderbar',
        "Claude Code CLI":
            "Claude Code CLI",
        "Use This CLI":
            "Diese CLI verwenden",
        "Browse…":
            "Durchsuchen…",
        "Path to the claude executable":
            "Pfad zur ausführbaren claude-Datei",
        "Collins needs the Claude Code CLI":
            "Collins braucht die Claude Code CLI",
        "Found it — Collins will remember this location.":
            "Gefunden — Collins merkt sich diesen Ort.",
        "Choose the claude executable":
            "Ausführbare claude-Datei auswählen",
        "No Claude Code yet? Get it at {link}, then come back.":
            "Noch kein Claude Code? Holen Sie es unter {link} und kommen Sie dann zurück.",
        "There's no executable file at this path.":
            "Unter diesem Pfad gibt es keine ausführbare Datei.",
        "It wasn't in any of the usual places — enter or browse to where it's installed.":
            "Es war an keinem der üblichen Orte — geben Sie ein, wo es installiert ist, oder suchen Sie danach.",
        "That's an executable, but not one named “claude” — pick the claude launcher itself.":
            "Das ist eine ausführbare Datei, aber keine namens „claude“ — wählen Sie den claude-Starter selbst.",
        "This is inside a version manager's tree, so Collins can't validate a stable path — it will work until that tool updates, and then this question comes back.":
            "Das liegt im Baum eines Versionsmanagers, daher kann Collins keinen stabilen Pfad prüfen — es funktioniert, bis dieses Tool sich aktualisiert, und dann kommt diese Frage zurück.",
        "This path has a version number in it, so it would break the next time Claude Code updates itself. Point at a stable launcher instead — usually ~/.local/bin/claude.":
            "Dieser Pfad enthält eine Versionsnummer und würde beim nächsten Selbst-Update von Claude Code kaputtgehen. Zeigen Sie stattdessen auf einen stabilen Starter — meist ~/.local/bin/claude.",
        "Every session runs through the claude command, and it isn't on the PATH that launches from the desktop are given — that PATH doesn't include the folders your shell adds. Point Collins at the CLI once; the location is remembered from then on.":
            "Jede Sitzung läuft über den Befehl claude, und der liegt nicht auf dem PATH, den vom Desktop gestartete Programme bekommen — der enthält nicht die Ordner, die Ihre Shell hinzufügt. Zeigen Sie Collins die CLI einmal; der Ort wird von da an gemerkt.",
        "Token use":
            "Token-Verbrauch",
        "Each of these runs Claude on your behalf, against your subscription's usage limits, without a prompt from you. Every run is a headless claude -p from a scratch directory, carrying none of your skills, MCP servers, or the CLI's tools, so it never appears as a session and costs little more than its prompt.":
            "Jede dieser Einstellungen lässt Claude in Ihrem Namen laufen – auf Kosten der Nutzungsgrenzen Ihres Abonnements, ohne eine Eingabe von Ihnen. Jeder Lauf ist ein kopfloses claude -p aus einem Arbeitsverzeichnis, ohne Ihre Skills, MCP-Server oder die Werkzeuge der CLI, erscheint darum nie als Sitzung und kostet kaum mehr als seinen Prompt.",
        "Auto-renew the Claude login":
            "Claude-Anmeldung automatisch erneuern",
        "When the login the usage panel and model list are fetched with has expired — at launch, or when a fetch is refused later — run one throwaway claude -p (a one-word prompt on Haiku) so the CLI renews it; off, the panel says to run claude yourself":
            "Ist die Anmeldung abgelaufen, mit der Nutzungsanzeige und Modellliste abgerufen werden – beim Start, oder wenn ein Abruf später abgewiesen wird –, läuft ein einzelnes Wegwerf-claude -p (ein Ein-Wort-Prompt auf Haiku), damit die CLI sie erneuert; aus, sagt die Anzeige, dass Sie claude selbst ausführen sollen",
        "{status} · free, no tokens":
            "{status} · kostenlos, keine Tokens",
        'Names each new session from its first prompt — every session Collins sees under ~/.claude/projects, including ones an agent or a terminal started. None: sessions keep the first words of their prompt, which costs nothing':
            'Benennt jede neue Sitzung nach ihrem ersten Prompt — jede Sitzung, die Collins unter ~/.claude/projects sieht, auch von einem Agenten oder Terminal gestartete. Keine: Sitzungen behalten die ersten Wörter ihres Prompts, was nichts kostet',
        "Model the sidebar's Generate Icon dialog starts with. None: the dialog waits for you to pick a model and click Generate":
            'Modell, mit dem der Dialog „Icon generieren“ der Seitenleiste startet. Keine: der Dialog wartet, bis Sie ein Modell wählen und auf Generieren klicken',
        'Regenerate name ({model})':
            'Namen neu generieren ({model})',
        'Pick a model to generate an icon':
            'Wählen Sie ein Modell, um ein Icon zu generieren',
        'Generate':
            'Generieren',
        'Choose a model…':
            'Modell wählen…',
        'Add the Ubuntu PPA…': 'Ubuntu-PPA hinzufügen…',
        'Add the package repository…': 'Paketquelle hinzufügen…',
        'Add the Ubuntu PPA?': 'Ubuntu-PPA hinzufügen?',
        "Collins isn't installed from ppa:episode6/stable yet. The PPA keeps it updated with the rest of the system: apt upgrade and the software updater both pick up new releases.":
            'Collins ist noch nicht aus ppa:episode6/stable installiert. Das PPA hält es zusammen mit dem restlichen System aktuell: apt upgrade und die Softwareaktualisierung holen neue Versionen automatisch.',
        'Add the Fedora COPR…': 'Fedora-COPR hinzufügen…',
        'Add the Fedora COPR?': 'Fedora-COPR hinzufügen?',
        "Collins isn't installed from the episode6/stable COPR yet. The COPR keeps it updated with the rest of the system: dnf upgrade and the software updater both pick up new releases.":
            'Collins ist noch nicht aus dem COPR episode6/stable installiert. Das COPR hält es zusammen mit dem restlichen System aktuell: dnf upgrade und die Softwareaktualisierung holen neue Versionen automatisch.',
        "Collins isn't installed from its package repository yet.":
            'Collins ist noch nicht aus seiner Paketquelle installiert.',
        'These commands ask for your password; they run in a terminal in this session.':
            'Diese Befehle fragen nach Ihrem Passwort; sie laufen in einem Terminal dieser Sitzung.',
        'Run these in a terminal — they ask for your password.':
            'Führen Sie diese Befehle in einem Terminal aus — sie fragen nach Ihrem Passwort.',
        'Run in Terminal': 'Im Terminal ausführen',
        "Couldn't open a terminal":
            'Terminal konnte nicht geöffnet werden',
        'Run the commands in a terminal of your own instead.':
            'Führen Sie die Befehle stattdessen in einem eigenen Terminal aus.',
        "── restored panel history ──": "── wiederhergestellter Panel-Verlauf ──",
        "Rename session": "Sitzung umbenennen",
        "Custom name": "Eigener Name",
        "Cancel": "Abbrechen",
        "Show folder paths in sidebar": "Ordnerpfade in der Seitenleiste anzeigen",
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
        "Continue last {name} session…": "Letzte {name}-Sitzung fortsetzen…",
        "Model": "Modell",
        "Default": "Standard",
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
        "Color scheme": "Farbschema",
        "Dark / Light Mode": "Dunkel- / Hellmodus",
        "Language": "Sprache",
        "When archiving a running session": "Beim Archivieren einer laufenden Sitzung",
        "Archiving a session that is still running also closes its tab":
            "Das Archivieren einer noch laufenden Sitzung schließt auch ihren Tab",
        "When quitting with running sessions": "Beim Beenden mit laufenden Sitzungen",
        "Closing a window while agent sessions are still running":
            "Schließen eines Fensters, während Agenten-Sitzungen noch laufen",
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
        "Search sessions…": "Sitzungen suchen…",
        "Search sessions": "Sitzungen suchen",
        "Close search": "Suche schließen",
        "A session is working": "Eine Sitzung arbeitet",
        "Collapse all groups": "Alle Gruppen einklappen",
        "Expand all groups": "Alle Gruppen ausklappen",
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
        "{n} sessions": "{n} Sitzungen",
        "Finished a run": "Hat einen Lauf beendet",
        "Default: the desktop's message sound": "Standard: der Nachrichtenton des Desktops",
        "Silent": "Stumm",
        "Bell": "Glocke",
        "Complete": "Fertig",
        "Message": "Nachricht",
        "Information": "Information",
        "Zen": "Zen",
        "Soft": "Sanft",
        "Glass": "Glas",
        "Confirmation": "Bestätigung",
        "Pluck": "Zupfen",
        "The desktop's “{event}” sound": "Der Ton „{event}“ des Desktops",
        "Ships with Collins: {source} (CC0)": "Mit Collins geliefert: {source} (CC0)",
        "Rang the bell": "Hat geklingelt",
        "In-app notifications": "Benachrichtigungen in der App",
        "Card theme": "Kartendesign",
        "The in-app card's own light or dark, whatever the app is": "Hell oder dunkel für die Karte selbst, unabhängig von der App",
        "Follow app": "App folgen",
        "Show a message from another session inside the window while Collins is focused. Off sends every notification to the desktop": "Eine Nachricht aus einer anderen Sitzung im Fenster anzeigen, solange Collins den Fokus hat. Aus sendet jede Benachrichtigung an den Desktop",
        "Sound": "Ton",
        "Custom…": "Benutzerdefiniert…",
        "Play the notification sound": "Benachrichtigungston abspielen",
        "Choose a different sound file": "Eine andere Sounddatei wählen",
        "Bells from other sessions": "Klingeln aus anderen Sitzungen",
        "A terminal bell from a session you aren't looking at posts a notification and plays the sound. Off keeps the desktop's beep": "Ein Terminal-Klingeln aus einer Sitzung, die Sie gerade nicht ansehen, erzeugt eine Benachrichtigung und spielt den Ton. Aus behält den Signalton des Desktops",
        "Announce finished runs": "Beendete Läufe melden",
        "Also notify when a session's run finishes, not only when it asks for you": "Auch benachrichtigen, wenn der Lauf einer Sitzung endet, nicht nur, wenn sie nach Ihnen fragt",
        "Check for updates": "Nach Updates suchen",
        "Ask GitHub once a day whether a newer Collins is out, and notify you when one is. Through your gh login, or anonymously": "Einmal am Tag bei GitHub nachfragen, ob ein neueres Collins erschienen ist, und Sie benachrichtigen, wenn ja. Über Ihre gh-Anmeldung oder anonym",
        "Collins {version} is available": "Collins {version} ist verfügbar",
        "You're running {version}. Click to open the release on GitHub": "Sie verwenden {version}. Klicken Sie, um die Veröffentlichung auf GitHub zu öffnen",
        "Sound needs GStreamer ({package}); the desktop's beep is used instead": "Für den Ton wird GStreamer ({package}) benötigt; stattdessen wird der Signalton des Desktops verwendet",
        "Choose a notification sound": "Benachrichtigungston auswählen",
        "Sound files": "Tondateien",
        "Notifications": "Benachrichtigungen",
        "1 unread notification": "1 ungelesene Benachrichtigung",
        "{n} unread notifications": "{n} ungelesene Benachrichtigungen",
        "just now": "gerade eben",
        "{n}s ago": "vor {n} s",
        "{n}m ago": "vor {n} min",
        "{n}h ago": "vor {n} h",
        "yesterday": "gestern",
        "{n}d ago": "vor {n} Tagen",
        "{body} ×{n}": "{body} ×{n}",
        "Untitled session": "Unbenannte Sitzung",
        "Mark all read": "Alle als gelesen markieren",
        "Mark every notification read": "Jede Benachrichtigung als gelesen markieren",
        "Clear": "Leeren",
        "Remove every notification": "Jede Benachrichtigung entfernen",
        "Unread": "Ungelesen",
        "Earlier": "Früher",
        "No notifications": "Keine Benachrichtigungen",
        "Messages from sessions you aren't looking at, and bells, land here.": "Nachrichten aus Sitzungen, die Sie gerade nicht ansehen, und Klingelzeichen landen hier.",
        "Mark read": "Als gelesen markieren",
        "Remove": "Entfernen",
        "Sound: {name}": "Ton: {name}",
        "Preferences…": "Einstellungen…",
        "Show/hide notifications": "Benachrichtigungen ein-/ausblenden",
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
        "Right-click to open on GitHub": "Zum Öffnen auf GitHub rechtsklicken",
        "Open on GitHub": "Auf GitHub öffnen",
        "Git pull failed": "Git pull fehlgeschlagen",
        "git was not found on PATH.": "git wurde nicht im PATH gefunden.",
        "git exited with status {code}": "git wurde mit Status {code} beendet",
        "Pulled {project} — {summary}": "{project} aktualisiert — {summary}",
        "Pulled {project}": "{project} aktualisiert",
        "Checkout {branch}": "{branch} auschecken",
        "Checkout default branch": "Standardbranch auschecken",
        "Git checkout failed": "Git checkout fehlgeschlagen",
        "Checked out {branch} in {project}": "{branch} in {project} ausgecheckt",
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
        "Back to files": "Zurück zu den Dateien",
        "Single column when narrow": "Eine Spalte, wenn schmal",
        "An editor column this many pixels wide or narrower shows the file tree and the open file one at a time, with a back button beside the tabs (0 = always side by side)":
            "Eine Editorspalte, die so viele Pixel breit oder schmaler ist, zeigt den Dateibaum und die geöffnete Datei abwechselnd, mit einem Zurück-Knopf neben den Reitern (0 = immer nebeneinander)",
        "Open File": "Datei öffnen",
        "Open a file…": "Datei öffnen…",
        "Indexing project files…": "Projektdateien werden indiziert…",
        "No files found in this project.": "Keine Dateien in diesem Projekt gefunden.",
        "Project is large — only the first {count} files are searchable.":
            "Großes Projekt — nur die ersten {count} Dateien sind durchsuchbar.",
        "Agent files": "Agent-Dateien",
        "Open {name} in the editor": "{name} im Editor öffnen",
        "Session behavior": "Sitzungsverhalten",
        "Composer": "Composer",
        "Built-in MCP tools": "Eingebaute MCP-Werkzeuge",
        "Every enabled tool's definition rides in each session's context, "
        "read_terminal sends the panel's text into the conversation, and a "
        "session start_session starts is titled like any other. Turning one "
        "off takes effect immediately; sessions already running are only "
        "offered the tool again once they restart":
            "Die Definition jedes eingeschalteten Werkzeugs steckt im Kontext "
            "jeder Sitzung, read_terminal schickt den Text des Panels in die "
            "Unterhaltung, und eine von start_session gestartete Sitzung wird "
            "wie jede andere betitelt. Das Abschalten wirkt sofort; bereits "
            "laufende Sitzungen bekommen das Werkzeug erst nach einem Neustart "
            "wieder angeboten",
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
        "Send notifications": "Benachrichtigungen senden",
        "notify_user — a card in the window or a desktop notification, titled with the session; clicking it opens the tab":
            "notify_user — eine Karte im Fenster oder eine Desktop-Benachrichtigung mit dem Sitzungsnamen; ein Klick öffnet den Tab",
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
        'Add application':
            'Anwendung hinzufügen',
        'Search applications…':
            'Anwendungen suchen…',
        'Caffeine Mode is on':
            'Caffeine Mode ist an',
        "That file isn't an image Collins can display.":
            'Diese Datei ist kein Bild, das Collins anzeigen kann.',
        'Until idle':
            'Bis zum Leerlauf',
        'Indefinitely':
            'Unbegrenzt',
        '1 hour':
            '1 Stunde',
        '{n} hours':
            '{n} Stunden',
        'Caffeine Mode is dozing until a session works again — then the computer and screen will stay awake':
            'Caffeine Mode döst, bis wieder eine Sitzung arbeitet — dann bleiben Computer und Bildschirm wach',
        'Caffeine Mode is dozing until a session works again — then the computer will stay awake, the screen may turn off':
            'Caffeine Mode döst, bis wieder eine Sitzung arbeitet — dann bleibt der Computer wach, der Bildschirm darf sich ausschalten',
        'Caffeine Mode is on while sessions are working — the computer and screen will stay awake':
            'Caffeine Mode ist an, solange Sitzungen arbeiten — Computer und Bildschirm bleiben wach',
        'Caffeine Mode is on while sessions are working — the computer will stay awake, the screen may turn off':
            'Caffeine Mode ist an, solange Sitzungen arbeiten — der Computer bleibt wach, der Bildschirm darf sich ausschalten',
        'Caffeine Mode is on — the computer and screen will stay awake':
            'Caffeine Mode ist an — Computer und Bildschirm bleiben wach',
        'Caffeine Mode is on — the computer will stay awake, the screen may turn off':
            'Caffeine Mode ist an — der Computer bleibt wach, der Bildschirm darf sich ausschalten',
        'Caffeine Mode: keep the computer awake and the screen on':
            'Caffeine Mode: Computer wach und Bildschirm an halten',
        'Caffeine Mode: keep the computer awake, letting the screen turn off':
            'Caffeine Mode: Computer wach halten, Bildschirm darf sich ausschalten',
        'Caffeine Mode turns off in {time} — computer and screen stay awake':
            'Caffeine Mode schaltet sich in {time} aus — Computer und Bildschirm bleiben wach',
        'Caffeine Mode turns off in {time} — computer stays awake, screen may turn off':
            'Caffeine Mode schaltet sich in {time} aus — der Computer bleibt wach, der Bildschirm darf sich ausschalten',
        'Send':
            'Senden',
        'The user declined this action.':
            'Der Benutzer hat diese Aktion abgelehnt.',
        'Switch the effort level for this session':
            'Die Effort-Stufe für diese Sitzung wechseln',
        "couldn't save a copy of the dropped image":
            'eine Kopie des abgelegten Bilds konnte nicht gespeichert werden',
        "skipped {n} item that isn't a local file":
            '{n} Element übersprungen, das keine lokale Datei ist',
        "couldn't reference {n} dropped file name":
            '{n} abgelegter Dateiname konnte nicht referenziert werden',
        "couldn't save a copy of the pasted image":
            'eine Kopie des eingefügten Bilds konnte nicht gespeichert werden',
        'Effort':
            'Effort',
        'Copied to clipboard':
            'In die Zwischenablage kopiert',
        'Rename folder':
            'Ordner umbenennen',
        'Rename file':
            'Datei umbenennen',
        'Enter a new name for “{name}”.':
            'Neuen Namen für „{name}“ eingeben.',
        'Rename':
            'Umbenennen',
        'Move editor to {name}?':
            'Editor nach {name} verschieben?',
        'This session is now working in {path}. One open file has unsaved changes and also exists there — choose what happens to it.':
            'Diese Sitzung arbeitet jetzt in {path}. Eine offene Datei hat ungespeicherte Änderungen und existiert auch dort — wählen Sie, was mit ihr geschieht.',
        'Stay':
            'Bleiben',
        'Go on editing this file, where your unsaved changes belong':
            'Diese Datei weiter bearbeiten, wo Ihre ungespeicherten Änderungen hingehören',
        'Take edits':
            'Änderungen mitnehmen',
        'Move this tab to the new copy, keeping your unsaved changes — saving will write them over whatever that copy holds':
            'Diesen Tab zur neuen Kopie verschieben und die ungespeicherten Änderungen behalten — Speichern überschreibt damit, was diese Kopie enthält',
        'Use new':
            'Neue verwenden',
        'Open the new copy and discard your unsaved changes':
            'Die neue Kopie öffnen und die ungespeicherten Änderungen verwerfen',
        "Don't Move":
            'Nicht verschieben',
        'Move Editor':
            'Editor verschieben',
        'Do you trust this folder?':
            'Vertrauen Sie diesem Ordner?',
        '{agent} will be able to read, edit and execute files in\n\n{path}\n\nand everything inside it, including any worktrees it creates there. Open it only if this is a project you created or otherwise trust — like your own code, a well-known open source project, or work from your team.':
            '{agent} kann Dateien lesen, bearbeiten und ausführen in\n\n{path}\n\nund allem darin, einschließlich aller Worktrees, die dort entstehen. Öffnen Sie ihn nur, wenn dies ein Projekt ist, das Sie erstellt haben oder dem Sie anderweitig vertrauen — etwa eigener Code, ein bekanntes Open-Source-Projekt oder Arbeit aus Ihrem Team.',
        'Trust and open':
            'Vertrauen und öffnen',
        'Generating icon…':
            'Icon wird generiert…',
        'At sidebar size':
            'In Seitenleisten-Größe',
        'Optional adjustments, e.g. “make it blue”':
            'Optionale Anpassungen, z. B. „mach es blau“',
        'Regenerate':
            'Neu generieren',
        'Default model':
            'Standardmodell',
        "Model for this dialog's runs; Preferences sets the default":
            'Modell für die Läufe dieses Dialogs; die Einstellungen legen den Standard fest',
        'Default ({model})':
            'Standard ({model})',
        'Generate Icon':
            'Icon generieren',
        'the generated SVG could not be rendered':
            'das generierte SVG konnte nicht gerendert werden',
        'Icon generation failed: {error}':
            'Icon-Generierung fehlgeschlagen: {error}',
        'Saving failed: {error}':
            'Speichern fehlgeschlagen: {error}',
        'Close other tabs':
            'Andere Tabs schließen',
        'Close tabs to the right':
            'Tabs rechts davon schließen',
        'Close all tabs':
            'Alle Tabs schließen',
        'Add to chat':
            'Zum Chat hinzufügen',
        "Couldn't rename {name}: {message}":
            '„{name}“ konnte nicht umbenannt werden: {message}',
        'A name is needed to rename {name}.':
            'Zum Umbenennen von „{name}“ wird ein Name benötigt.',
        "“{new_name}” isn't a name — renaming can't move things elsewhere.":
            '„{new_name}“ ist kein Name — Umbenennen kann nichts woandershin verschieben.',
        '“{new_name}” already exists here.':
            '„{new_name}“ existiert hier bereits.',
        '{name} is no longer there.':
            '„{name}“ ist nicht mehr da.',
        "{name} can't be renamed to something outside this project.":
            '„{name}“ kann nicht in etwas außerhalb dieses Projekts umbenannt werden.',
        "There's nothing on the clipboard to paste here.":
            'Die Zwischenablage enthält nichts, das sich hier einfügen ließe.',
        "{count} item couldn't be pasted.":
            '{count} Element konnte nicht eingefügt werden.',
        "{name} can't be pasted into itself.":
            '„{name}“ kann nicht in sich selbst eingefügt werden.',
        'That folder is no longer there.':
            'Dieser Ordner ist nicht mehr da.',
        "{name} can't be pasted outside this project.":
            '„{name}“ kann nicht außerhalb dieses Projekts eingefügt werden.',
        'There are already too many copies of {name} here.':
            'Es gibt hier bereits zu viele Kopien von „{name}“.',
        "Couldn't paste {name}: {message}":
            '„{name}“ konnte nicht eingefügt werden: {message}',
        "{name} couldn't be decoded as an image.":
            '„{name}“ konnte nicht als Bild dekodiert werden.',
        'Session moved to {name}':
            'Sitzung nach {name} verschoben',
        'Follow':
            'Folgen',
        'Image':
            'Bild',
        'Cut':
            'Ausschneiden',
        '{n} session(s) in {p} project(s) have their transcripts moved to the trash, where they can be restored. Sessions archived with their whole project — and originals a backgrounded fork replaced — are included.':
            'Die Transkripte von {n} Sitzung(en) in {p} Projekt(en) werden in den Papierkorb verschoben, wo sie wiederhergestellt werden können. Sitzungen, die mit ihrem ganzen Projekt archiviert wurden — und Originale, die ein Hintergrund-Fork ersetzt hat —, sind eingeschlossen.',
        '{project} — {n} of {total}':
            '{project} — {n} von {total}',
        '…and {p} other project(s) — {n} session(s)':
            '…und {p} weitere(s) Projekt(e) — {n} Sitzung(en)',
        '{p} of these project(s) lose every session they have.':
            '{p} dieser Projekte verlieren jede Sitzung, die sie haben.',
        'Open, every check passed':
            'Offen, alle Checks bestanden',
        'Checks still running':
            'Checks laufen noch',
        'A check failed':
            'Ein Check ist fehlgeschlagen',
        'A reviewer is waiting on a reply':
            'Ein Reviewer wartet auf eine Antwort',
        'Draft, and the branch conflicts':
            'Entwurf, und der Branch hat Konflikte',
        'Merged':
            'Zusammengeführt',
        'Collins is better with the GitHub CLI':
            'Collins ist besser mit der GitHub CLI',
        "Collins follows the pull requests your sessions open — and acts on them — through gh, GitHub's own command-line tool, which isn't installed here.":
            'Collins verfolgt die Pull-Requests, die Ihre Sitzungen öffnen — und führt Aktionen darauf aus — über gh, GitHubs eigenes Kommandozeilen-Tool, das hier nicht installiert ist.',
        "Collins follows the pull requests your sessions open — and acts on them — through gh, GitHub's own command-line tool, which is installed here but never signed in.":
            'Collins verfolgt die Pull-Requests, die Ihre Sitzungen öffnen — und führt Aktionen darauf aus — über gh, GitHubs eigenes Kommandozeilen-Tool, das hier installiert, aber nie angemeldet ist.',
        'Not now':
            'Jetzt nicht',
        'Get the GitHub CLI':
            'GitHub CLI holen',
        'Copy command':
            'Befehl kopieren',
        "With it, every session's pull requests carry their status:":
            'Damit tragen die Pull-Requests jeder Sitzung ihren Status:',
        '…and a click on one does something about it:':
            '…und ein Klick darauf unternimmt etwas:',
        "Don't show this again":
            'Nicht mehr anzeigen',
        'Install it from cli.github.com — Collins picks it up the next time it starts.':
            'Installieren Sie es von cli.github.com — Collins findet es beim nächsten Start.',
        'Run this once in any terminal. Collins asks for no login of its own.':
            'Führen Sie dies einmal in einem beliebigen Terminal aus. Collins verlangt keine eigene Anmeldung.',
        'Keyboard Bindings':
            'Tastaturbelegung',
        'Reset All':
            'Alle zurücksetzen',
        'Put every shortcut back to its default':
            'Jedes Kürzel auf seinen Standard zurücksetzen',
        'Click a row to change its shortcut':
            'Klicken Sie auf eine Zeile, um ihr Kürzel zu ändern',
        'Also bound to: {actions}':
            'Auch belegt für: {actions}',
        'Unbound':
            'Nicht belegt',
        'Reset to default':
            'Auf Standard zurücksetzen',
        'Reset every shortcut?':
            'Jedes Kürzel zurücksetzen?',
        'All of your custom keyboard bindings are replaced by the defaults.':
            'Alle Ihre eigenen Tastenbelegungen werden durch die Standards ersetzt.',
        '{chord} is already in use':
            '{chord} ist bereits belegt',
        'It is bound to {actions}. Move it to {action}?':
            'Es ist mit {actions} belegt. Zu {action} verschieben?',
        'Move Shortcut':
            'Kürzel verschieben',
        'unbound':
            'nicht belegt',
        'Set shortcut for “{action}”':
            'Kürzel für „{action}“ festlegen',
        'Press the new key combination. Currently: {current}.\nBackspace removes the binding; Escape keeps it.':
            'Drücken Sie die neue Tastenkombination. Aktuell: {current}.\nRücktaste entfernt die Belegung; Escape behält sie.',
        'Tabs and windows':
            'Tabs und Fenster',
        'Panels':
            'Panels',
        'Application':
            'Anwendung',
        'New session':
            'Neue Sitzung',
        'Quick switcher':
            'Schnellwechsler',
        'Archive the current session':
            'Aktuelle Sitzung archivieren',
        'Undo the last archive':
            'Letztes Archivieren rückgängig machen',
        'Open the pull request page':
            'Die Pull-Request-Seite öffnen',
        "Unbound by default; the sidebar's search button does the same.":
            'Standardmäßig nicht belegt; der Suchknopf der Seitenleiste tut dasselbe.',
        'Close tab':
            'Tab schließen',
        'Next tab':
            'Nächster Tab',
        'Previous tab':
            'Vorheriger Tab',
        'Toggle the tab marker':
            'Tab-Markierung umschalten',
        'Show/hide the sidebar':
            'Seitenleiste ein-/ausblenden',
        'Show/hide the terminal panel':
            'Terminal-Panel ein-/ausblenden',
        'Clear the terminal panel':
            'Terminal-Panel leeren',
        'Move the panel tab to the other side':
            'Panel-Tab auf die andere Seite verschieben',
        'Show/hide the composer':
            'Composer ein-/ausblenden',
        'Show/hide the attachments gallery':
            'Anhänge-Galerie ein-/ausblenden',
        "Swap the panel's sides":
            'Seiten des Panels tauschen',
        'Unbound by default.':
            'Standardmäßig nicht belegt.',
        'Move the panel tab to the other strip':
            'Panel-Tab auf die andere Leiste verschieben',
        'Show/hide the editor':
            'Editor ein-/ausblenden',
        'Quick open a file':
            'Datei schnell öffnen',
        'Focus the editor':
            'Editor fokussieren',
        'Save the file':
            'Datei speichern',
        'In the editor.':
            'Im Editor.',
        'Find in the file':
            'In der Datei suchen',
        'Copy the selection':
            'Auswahl kopieren',
        'With easy copy and paste on; without a selection the key reaches the terminal.':
            'Bei eingeschaltetem einfachem Kopieren & Einfügen; ohne Auswahl erreicht die Taste das Terminal.',
        'With easy copy and paste on.':
            'Bei eingeschaltetem einfachem Kopieren & Einfügen.',
        'Copy (terminal-style)':
            'Kopieren (Terminal-Stil)',
        'Paste (terminal-style)':
            'Einfügen (Terminal-Stil)',
        'Find in the terminal':
            'Im Terminal suchen',
        'Insert a newline in the prompt':
            'Neue Zeile in den Prompt einfügen',
        'Zoom in':
            'Vergrößern',
        'Zoom out':
            'Verkleinern',
        'Reset zoom':
            'Zoom zurücksetzen',
        'Keyboard bindings':
            'Tastaturbelegung',
        "Couldn't display image":
            'Bild konnte nicht angezeigt werden',
        'Open in Editor':
            'Im Editor öffnen',
        'Low':
            'Niedrig',
        'Medium':
            'Mittel',
        'High':
            'Hoch',
        'Extra high':
            'Sehr hoch',
        'Max':
            'Max',
        'This model has no effort setting':
            'Dieses Modell hat keine Effort-Einstellung',
        'New chat':
            'Neuer Chat',
        'Model to start this session on':
            'Modell, mit dem diese Sitzung startet',
        'Effort level to start this session at':
            'Effort-Stufe, mit der diese Sitzung startet',
        'New git worktree':
            'Neuer Git-Worktree',
        'Work in a fresh worktree of this project, apart from its uncommitted changes':
            'In einem frischen Worktree dieses Projekts arbeiten, getrennt von seinen nicht committeten Änderungen',
        'Empty Session':
            'Leere Sitzung',
        'Start the session with no prompt':
            'Sitzung ohne Prompt starten',
        'Drag to move this tab: drop on an edge to split, on a strip to join':
            'Ziehen verschiebt diesen Tab: Ablegen an einer Kante teilt, auf einer Leiste reiht ein',
        'Restore this tab to its size and place in the panel':
            'Diesen Tab auf seine Größe und seinen Platz im Panel zurücksetzen',
        'Close Tab':
            'Tab schließen',
        'Overlay this tab over the whole session':
            'Diesen Tab über die ganze Sitzung legen',
        'Move this tab to the other side':
            'Diesen Tab auf die andere Seite verschieben',
        'Close tab with a running command?':
            'Tab mit laufendem Befehl schließen?',
        'Move to':
            'Verschieben nach',
        'Split Left':
            'Nach links teilen',
        'Split Right':
            'Nach rechts teilen',
        'Split Up':
            'Nach oben teilen',
        'Split Down':
            'Nach unten teilen',
        'Close tabs with running commands?':
            'Tabs mit laufenden Befehlen schließen?',
        'A command is still running in one of these tabs and will be terminated.':
            'In einem dieser Tabs läuft noch ein Befehl, der beendet wird.',
        'Close Tabs':
            'Tabs schließen',
        'Address unresolved comments':
            'Unbeantwortete Kommentare bearbeiten',
        'Send “{prompt}” to this session':
            '„{prompt}“ an diese Sitzung senden',
        'Open a pull request':
            'Einen Pull Request öffnen',
        'Fix errors & resolve conflicts':
            'Fehler beheben & Konflikte auflösen',
        'Address the CI errors':
            'Die CI-Fehler beheben',
        'Fix errors':
            'Fehler beheben',
        'Resolve conflicts':
            'Konflikte auflösen',
        'Mark ready for review':
            'Als bereit für Review markieren',
        'Take {slug} out of draft':
            '{slug} aus dem Entwurfsstatus nehmen',
        'Ready':
            'Bereit',
        'Ask Claude for a review':
            'Claude um ein Review bitten',
        'Comment “{comment}” on {slug}':
            '„{comment}“ zu {slug} kommentieren',
        'Merge when checks pass':
            'Zusammenführen, wenn die Checks bestehen',
        'Turn on auto-merge for {slug}':
            'Auto-Merge für {slug} einschalten',
        'Merge {slug} when its checks pass?':
            '{slug} zusammenführen, wenn seine Checks bestehen?',
        'GitHub merges it as soon as every required check has passed. You can still cancel auto-merge on the pull request page.':
            'GitHub führt ihn zusammen, sobald jeder erforderliche Check bestanden ist. Auto-Merge lässt sich auf der Pull-Request-Seite weiterhin abbrechen.',
        'Enable auto-merge':
            'Auto-Merge aktivieren',
        'Auto-Merge':
            'Auto-Merge',
        'Merge pull request':
            'Pull Request zusammenführen',
        'Merge {slug} now':
            '{slug} jetzt zusammenführen',
        'Merge {slug}?':
            '{slug} zusammenführen?',
        'Merge':
            'Zusammenführen',
        'Disable auto-merge':
            'Auto-Merge deaktivieren',
        'Stop GitHub from merging {slug} when its checks pass':
            'GitHub davon abhalten, {slug} zusammenzuführen, wenn seine Checks bestehen',
        'Disable Auto-Merge':
            'Auto-Merge deaktivieren',
        'Its checks have passed. This merges the pull request on GitHub now.':
            'Seine Checks sind bestanden. Dies führt den Pull Request jetzt auf GitHub zusammen.',
        "Its checks haven't all passed. This merges the pull request on GitHub now, if the repository lets it.":
            'Seine Checks sind nicht alle bestanden. Dies führt den Pull Request jetzt auf GitHub zusammen, sofern das Repository es zulässt.',
        'Merge and archive session':
            'Zusammenführen und Sitzung archivieren',
        'Merge {slug} now, then archive this session':
            '{slug} jetzt zusammenführen, dann diese Sitzung archivieren',
        'Merge {slug} and archive this session?':
            '{slug} zusammenführen und diese Sitzung archivieren?',
        'The session is archived once the merge lands — you can bring it back with Undo, or from “Show archived”.':
            'Die Sitzung wird archiviert, sobald der Merge durch ist — zurückholen lässt sie sich mit Rückgängig oder über „Archivierte anzeigen“.',
        'Merge & archive':
            'Zusammenführen & archivieren',
        'Mark ready & merge when checks pass':
            'Bereit markieren & zusammenführen, wenn Checks bestehen',
        'Take {slug} out of draft, then turn on auto-merge':
            '{slug} aus dem Entwurfsstatus nehmen, dann Auto-Merge einschalten',
        'Mark {slug} ready and merge it when its checks pass?':
            '{slug} als bereit markieren und zusammenführen, wenn seine Checks bestehen?',
        'Ready & auto-merge':
            'Bereit & Auto-Merge',
        'Ready & Auto-Merge':
            'Bereit & Auto-Merge',
        'Mark ready & merge':
            'Bereit markieren & zusammenführen',
        'Take {slug} out of draft, then merge it now':
            '{slug} aus dem Entwurfsstatus nehmen, dann jetzt zusammenführen',
        'Mark {slug} ready and merge it?':
            '{slug} als bereit markieren und zusammenführen?',
        'Ready & merge':
            'Bereit & zusammenführen',
        'Ready & Merge':
            'Bereit & Zusammenführen',
        'Mark ready, merge & archive session':
            'Bereit markieren, zusammenführen & Sitzung archivieren',
        'Take {slug} out of draft, merge it now, then archive this session':
            '{slug} aus dem Entwurfsstatus nehmen, jetzt zusammenführen, dann diese Sitzung archivieren',
        'Mark {slug} ready, merge it and archive this session?':
            '{slug} als bereit markieren, zusammenführen und diese Sitzung archivieren?',
        'Ready, merge & archive':
            'Bereit, zusammenführen & archivieren',
        'The pull request is marked ready for review first.':
            'Der Pull Request wird zuerst als bereit für Review markiert.',
        'Close pull request':
            'Pull Request schließen',
        'Close {slug} without merging':
            '{slug} ohne Zusammenführen schließen',
        'Close {slug}?':
            '{slug} schließen?',
        'The pull request is closed without merging. Its branch and its comments stay, and it can be reopened on GitHub.':
            'Der Pull Request wird ohne Zusammenführen geschlossen. Sein Branch und seine Kommentare bleiben, und er kann auf GitHub wieder geöffnet werden.',
        "{url} doesn't look like a pull request.":
            '{url} sieht nicht wie ein Pull Request aus.',
        "Collins doesn't know how to do that.":
            'Collins weiß nicht, wie das geht.',
        'Merge conflicts':
            'Merge-Konflikte',
        'Refresh':
            'Aktualisieren',
        'Search settings…':
            'Einstellungen durchsuchen…',
        'Search settings':
            'Einstellungen durchsuchen',
        'No settings found':
            'Keine Einstellungen gefunden',
        'Try a different search.':
            'Versuchen Sie eine andere Suche.',
        'Tab drag handles':
            'Tab-Ziehgriffe',
        'Drag any panel tab by its handle to move, reorder, or split it. Relies on GTK internals — turn off to fall back to plain tab dragging plus a drag grip on each panel':
            'Jeden Panel-Tab an seinem Griff ziehen, um ihn zu verschieben, umzuordnen oder zu teilen. Beruht auf GTK-Interna — ausschalten, um auf einfaches Tab-Ziehen plus einen Ziehgriff pro Panel zurückzufallen',
        'Project icon size':
            'Projekt-Icon-Größe',
        'Size of the project and folder icons in the sidebar':
            'Größe der Projekt- und Ordner-Icons in der Seitenleiste',
        'Start new sessions in a git worktree':
            'Neue Sitzungen in einem Git-Worktree starten',
        "Git projects only; each new session works in its own fresh worktree, so it won't see uncommitted local changes. Right-click a project header to override per project":
            'Nur Git-Projekte; jede neue Sitzung arbeitet in einem eigenen frischen Worktree und sieht darum keine nicht committeten lokalen Änderungen. Rechtsklick auf einen Projektkopf überschreibt das pro Projekt',
        "Follow Claude's own session names":
            'Claudes eigenen Sitzungsnamen folgen',
        'Rename sessions whenever Claude names or renames them — /rename and its automatic titles; manually renamed sessions keep their name':
            'Sitzungen umbenennen, wann immer Claude sie benennt oder umbenennt — /rename und seine automatischen Titel; manuell umbenannte Sitzungen behalten ihren Namen',
        'Exact busy tracking from the agent':
            'Exakte Beschäftigt-Erkennung vom Agenten',
        "Read Claude Code's own progress announcements for the sidebar's working indicator, instead of only inferring from terminal output (fully applies to newly opened tabs)":
            'Claude Codes eigene Fortschrittsmeldungen für die Arbeitsanzeige der Seitenleiste lesen, statt nur aus der Terminalausgabe zu schließen (gilt vollständig für neu geöffnete Tabs)',
        'Poll for background sessions':
            'Hintergrundsitzungen abfragen',
        'Fallback: check the agent CLI every 20 seconds in case the yellow guide lines stop updating on their own':
            'Fallback: alle 20 Sekunden die Agent-CLI prüfen, falls die gelben Leitlinien von selbst nicht mehr aktualisieren',
        'Typing opens the composer':
            'Tippen öffnet den Composer',
        "Start typing at an agent's empty prompt and the composer opens with what you typed. A dialog, a menu and the CLI's own /, !, # and @ keep their keys":
            'Tippen Sie am leeren Prompt eines Agenten los, öffnet sich der Composer mit dem Getippten. Ein Dialog, ein Menü und die CLI-eigenen /, !, # und @ behalten ihre Tasten',
        'Right-click aims spell-check':
            'Rechtsklick lenkt die Rechtschreibprüfung',
        'Right-clicking a misspelled word in the composer offers corrections for that word. Off: corrections follow the text cursor instead, and a right-click never moves it':
            'Ein Rechtsklick auf ein falsch geschriebenes Wort im Composer bietet Korrekturen für dieses Wort an. Aus: Korrekturen folgen stattdessen dem Textcursor, und ein Rechtsklick bewegt ihn nie',
        'Max width':
            'Maximale Breite',
        'Stop growing past this width and center in the tab instead (0 = no limit)':
            'Über diese Breite hinaus nicht wachsen, sondern im Tab zentrieren (0 = keine Grenze)',
        'Footer apps':
            'Fußzeilen-Apps',
        "Buttons in each tab's footer that open the tab's directory":
            'Knöpfe in der Fußzeile jedes Tabs, die das Verzeichnis des Tabs öffnen',
        'Add application…':
            'Anwendung hinzufügen…',
        'Pull requests':
            'Pull-Requests',
        'Text size':
            'Textgröße',
        'Reading-text size in the pull request panel, as a percentage of the app font; buttons and menus keep the app size':
            'Lesetextgröße im Pull-Request-Panel, als Prozentsatz der App-Schrift; Knöpfe und Menüs behalten die App-Größe',
        'Show embedded images':
            'Eingebettete Bilder anzeigen',
        'Render the images a description or comment embeds, and the changed image files, as pictures; click one to open it full size. Off, they stay links and patches, and opening a pull request downloads nothing':
            'Bilder, die eine Beschreibung oder ein Kommentar einbettet, und geänderte Bilddateien als Bilder darstellen; ein Klick öffnet sie in voller Größe. Aus bleiben sie Links und Patches, und das Öffnen eines Pull-Requests lädt nichts herunter',
        'Confirm before merging':
            'Vor dem Zusammenführen bestätigen',
        'Ask before merging a pull request, enabling auto-merge, or merging and archiving the session. Off, the click merges; closing a pull request unmerged still asks either way':
            'Nachfragen, bevor ein Pull Request zusammengeführt, Auto-Merge aktiviert oder zusammengeführt und die Sitzung archiviert wird. Aus führt der Klick direkt zusammen; das Schließen eines nicht zusammengeführten Pull-Requests fragt trotzdem immer nach',
        'Attach pull requests named in prompts':
            'In Prompts genannte Pull-Requests anheften',
        "Put every pull request a new session's first prompt mentions on that session's row, without waiting for the agent to touch it":
            'Jeden Pull Request, den der erste Prompt einer neuen Sitzung erwähnt, auf die Zeile dieser Sitzung setzen, ohne zu warten, bis der Agent ihn anfasst',
        'Rename sessions after their pull requests':
            'Sitzungen nach ihren Pull-Requests benennen',
        'Retitle a session to match the newest pull request opened in it; manually renamed sessions keep their name':
            'Eine Sitzung nach dem neuesten in ihr geöffneten Pull Request umbenennen; manuell umbenannte Sitzungen behalten ihren Namen',
        'Refresh pull requests at launch':
            'Pull-Requests beim Start aktualisieren',
        "Ask GitHub about every listed session's pull requests once on startup, so the marks in the sidebar start out current rather than as they were left":
            'GitHub beim Start einmal nach den Pull-Requests jeder gelisteten Sitzung fragen, damit die Markierungen in der Seitenleiste aktuell starten statt so, wie sie zurückblieben',
        'Caffeine Mode':
            'Caffeine Mode',
        'Keep screen on':
            'Bildschirm anlassen',
        'Hold the screen on as well as keeping the computer awake. Off lets the screen turn off as usual, while an unattended agent still keeps the computer from sleeping':
            'Neben dem wachen Computer auch den Bildschirm anlassen. Aus lässt den Bildschirm wie üblich ausgehen, während ein unbeaufsichtigter Agent den Computer weiterhin am Schlafen hindert',
        'Until idle grace period':
            'Karenzzeit für „Bis zum Leerlauf“',
        'How many minutes Until idle keeps the computer awake after the last session stops working; any session picking work back up restarts the wait':
            'Wie viele Minuten „Bis zum Leerlauf“ den Computer wach hält, nachdem die letzte Sitzung aufgehört hat zu arbeiten; nimmt eine Sitzung die Arbeit wieder auf, beginnt die Wartezeit von vorn',
        'Turn on at launch':
            'Beim Start einschalten',
        'Start with Caffeine Mode already on, keeping the computer awake until you turn it off from the header':
            'Mit bereits eingeschaltetem Caffeine Mode starten; der Computer bleibt wach, bis Sie ihn in der Kopfleiste ausschalten',
        'Turn off after':
            'Ausschalten nach',
        'Open in a window on small screens':
            'Auf kleinen Bildschirmen in einem Fenster öffnen',
        'On screens this many pixels wide or narrower (after display scaling), the editor opens in its own window instead of a panel (0 = always open as a panel)':
            'Auf Bildschirmen, die so viele Pixel breit oder schmaler sind (nach Displayskalierung), öffnet sich der Editor in einem eigenen Fenster statt als Panel (0 = immer als Panel öffnen)',
        'Show status icon':
            'Statussymbol anzeigen',
        'Shows Collins in the top bar, with a menu that jumps to any open session':
            'Zeigt Collins in der oberen Leiste, mit einem Menü, das zu jeder offenen Sitzung springt',
        'No status-icon support was found in this desktop — GNOME needs an AppIndicator extension':
            'In diesem Desktop wurde keine Statussymbol-Unterstützung gefunden — GNOME braucht eine AppIndicator-Erweiterung',
        'Nothing on this desktop can show a status icon':
            'Nichts auf diesem Desktop kann ein Statussymbol anzeigen',
        'Using the claude found on PATH at {path}.':
            'Verwendet das auf PATH gefundene claude unter {path}.',
        "claude isn't on PATH — Collins will ask where it is at the next launch.":
            'claude ist nicht auf PATH — Collins fragt beim nächsten Start, wo es liegt.',
        'How long that launch-time Caffeine Mode runs before it turns itself off. Until idle never does: it holds the computer awake while any session is working (and {n} minute past), dozing in between':
            'Wie lange dieser beim Start eingeschaltete Caffeine Mode läuft, bevor er sich selbst ausschaltet. „Bis zum Leerlauf“ tut das nie: Er hält den Computer wach, solange irgendeine Sitzung arbeitet (und {n} Minute darüber hinaus), und döst dazwischen',
        'Move up':
            'Nach oben',
        'Move down':
            'Nach unten',
        'No apps configured':
            'Keine Apps konfiguriert',
        'Before':
            'Vorher',
        'After':
            'Nachher',
        'Back to the pull requests':
            'Zurück zu den Pull-Requests',
        'View in Collins':
            'In Collins anzeigen',
        "Open this pull request's page beside the session":
            'Die Seite dieses Pull-Requests neben der Sitzung öffnen',
        'View unresolved comments':
            'Unbeantwortete Kommentare anzeigen',
        "Open this pull request's page at its first unresolved thread":
            'Die Seite dieses Pull-Requests beim ersten unerledigten Thread öffnen',
        "Collins couldn't run that action.":
            'Collins konnte diese Aktion nicht ausführen.',
        '{action} failed':
            '{action} fehlgeschlagen',
        'Pull request':
            'Pull Request',
        'Merging when checks pass':
            'Wird zusammengeführt, wenn die Checks bestehen',
        "The GitHub CLI (gh) isn't installed, or isn't on PATH.":
            'Die GitHub CLI (gh) ist nicht installiert oder nicht auf PATH.',
        "Collins couldn't run gh.":
            'Collins konnte gh nicht ausführen.',
        'gh exited with status {code}.':
            'gh wurde mit Status {code} beendet.',
        'and {n} more':
            'und {n} weitere',
        'Approved':
            'Genehmigt',
        'Changes requested':
            'Änderungen angefordert',
        'Review dismissed':
            'Review verworfen',
        'Commented':
            'Kommentiert',
        'Reload this pull request':
            'Diesen Pull Request neu laden',
        'Conversation':
            'Unterhaltung',
        'Files':
            'Dateien',
        "Couldn't load this pull request — is the GitHub CLI signed in?":
            'Dieser Pull Request konnte nicht geladen werden — ist die GitHub CLI angemeldet?',
        'Nothing loaded yet.':
            'Noch nichts geladen.',
        'Merges {head} into {base}':
            'Führt {head} in {base} zusammen',
        '{n} file':
            '{n} Datei',
        'No comments yet.':
            'Noch keine Kommentare.',
        'No description provided.':
            'Keine Beschreibung vorhanden.',
        'No changed files.':
            'Keine geänderten Dateien.',
        'Checks ({n})':
            'Checks ({n})',
        'Checks':
            'Checks',
        'More actions':
            'Weitere Aktionen',
        'Right-click for more actions':
            'Rechtsklick für weitere Aktionen',
        'Add a comment':
            'Kommentar hinzufügen',
        'Request changes':
            'Änderungen anfordern',
        'Approve':
            'Genehmigen',
        'Comment':
            'Kommentieren',
        'Comment on {slug}':
            '{slug} kommentieren',
        'Approve {slug}':
            '{slug} genehmigen',
        'Request changes on {slug}':
            'Änderungen an {slug} anfordern',
        'Address comments':
            'Kommentare bearbeiten',
        'Request review':
            'Review anfordern',
        'Outdated':
            'Veraltet',
        'The code this thread commented on has changed':
            'Der Code, den dieser Thread kommentiert hat, hat sich geändert',
        'Resolved':
            'Erledigt',
        'Reply':
            'Antworten',
        'Reply in this thread':
            'In diesem Thread antworten',
        'Unresolve':
            'Wieder öffnen',
        'Resolve':
            'Erledigen',
        'Reopen this thread':
            'Diesen Thread wieder öffnen',
        'Mark this thread resolved':
            'Diesen Thread als erledigt markieren',
        'Post reply':
            'Antwort senden',
        'no diff — binary or too large':
            'kein Diff — binär oder zu groß',
        '{n} line':
            '{n} Zeile',
        'Show more':
            'Mehr anzeigen',
        'Show less':
            'Weniger anzeigen',
        'New session in {path}':
            'Neue Sitzung in {path}',
        'Expand':
            'Ausklappen',
        'Collapse':
            'Einklappen',
        'New Thread':
            'Neuer Thread',
        'Discard draft and close tab':
            'Entwurf verwerfen und Tab schließen',
        'Discard draft':
            'Entwurf verwerfen',
        'Backgrounding is unavailable until this session is registered and any handoff in progress finishes':
            'Das Verschieben in den Hintergrund ist erst möglich, wenn diese Sitzung registriert ist und eine laufende Übergabe abgeschlossen ist',
        'This session has no tab open.':
            'Diese Sitzung hat keinen offenen Tab.',
        'Delete archived sessions…':
            'Archivierte Sitzungen löschen…',
        'Add project':
            'Projekt hinzufügen',
        'Refresh session list and pull requests':
            'Sitzungsliste und Pull-Requests aktualisieren',
        'Chats':
            'Chats',
        'Draft':
            'Entwurf',
        'Refreshing pull requests…':
            'Pull-Requests werden aktualisiert…',
        'Open in new window':
            'In neuem Fenster öffnen',
        'Move to new window':
            'In neues Fenster verschieben',
        'Rename to match PR':
            'Nach PR benennen',
        'Rename to match PR #{number}':
            'Nach PR #{number} benennen',
        'Repair session link':
            'Sitzungsverknüpfung reparieren',
        'New session in new window':
            'Neue Sitzung in neuem Fenster',
        'New session here (no worktree)':
            'Neue Sitzung hier (ohne Worktree)',
        'New session here (in a worktree)':
            'Neue Sitzung hier (in einem Worktree)',
        'New sessions use a worktree':
            'Neue Sitzungen verwenden einen Worktree',
        'Git pull ({branch})':
            'Git pull ({branch})',
        'Git pull':
            'Git pull',
        'Remove project from sidebar':
            'Projekt aus der Seitenleiste entfernen',
        'replaced':
            'ersetzt',
        'Open composer':
            'Composer öffnen',
        "the agent didn't start — your prompt is kept in the composer":
            'der Agent ist nicht gestartet — Ihr Prompt bleibt im Composer',
        'Every pull request this session has opened':
            'Jeder Pull Request, den diese Sitzung geöffnet hat',
        'Show/hide terminal panel':
            'Terminal-Panel ein-/ausblenden',
        'Move terminals to {name}?':
            'Terminals nach {name} verschieben?',
        'This session started in a worktree at {path}. The terminal open beside it is still in the project directory — change its directory to the worktree? A terminal running a command is left alone.':
            'Diese Sitzung ist in einem Worktree unter {path} gestartet. Das daneben offene Terminal ist noch im Projektverzeichnis — sein Verzeichnis auf den Worktree wechseln? Ein Terminal, in dem ein Befehl läuft, bleibt unangetastet.',
        'Change Directory':
            'Verzeichnis wechseln',
        '{n} terminal is running a command and stayed where it was':
            'In {n} Terminal läuft ein Befehl; es blieb, wo es war',
        'Effort: {level}':
            'Effort: {level}',
        'Click to switch the effort level':
            'Zum Wechseln der Effort-Stufe klicken',
        'No pull request found for this branch':
            'Kein Pull Request für diesen Branch gefunden',
        "Re-check this branch's pull requests":
            'Pull-Requests dieses Branches erneut prüfen',
        "Look for this branch's pull request":
            'Nach dem Pull Request dieses Branches suchen',
        "Add to chat: the agent isn't running in this tab":
            'Zum Chat hinzufügen: In diesem Tab läuft kein Agent',
        "Add to chat isn't available for this file":
            'Zum Chat hinzufügen ist für diese Datei nicht verfügbar',
        "skipped {n} dropped item that isn't a local file":
            '{n} abgelegtes Element übersprungen, das keine lokale Datei ist',
        "Composer: the input box holds a paste Collins can't read":
            'Composer: Das Eingabefeld enthält eine Einfügung, die Collins nicht lesen kann',
        "Effort switch: the agent isn't running in this tab":
            'Effort-Wechsel: In diesem Tab läuft kein Agent',
        "This session isn't at an empty prompt.":
            'Diese Sitzung steht nicht an einem leeren Prompt.',
        'Start sessions in the background':
            'Sitzungen im Hintergrund starten',
        'start_session — spawn a sibling agent in a new background tab, with a prompt':
            'start_session — startet einen Geschwister-Agenten in einem neuen Hintergrund-Tab, mit einem Prompt',
        'Read the terminal panel':
            'Das Terminal-Panel lesen',
        "read_terminal — the panel tabs' text and scrollback, your own typing included":
            'read_terminal — Text und Scrollback der Panel-Tabs, eigene Eingaben eingeschlossen',
        'Run commands in the terminal panel':
            'Befehle im Terminal-Panel ausführen',
        'run_in_terminal — type a command into an idle panel tab (or a new one) and run it':
            'run_in_terminal — tippt einen Befehl in einen untätigen Panel-Tab (oder einen neuen) und führt ihn aus',
        'Default (latest Haiku)':
            'Standard (neuestes Haiku)',
        'Default (latest Sonnet)':
            'Standard (neuestes Sonnet)',
        'Session title model':
            'Modell für Sitzungstitel',
        'Icon generation model':
            'Modell für Icon-Generierung',
        'Model list':
            'Modellliste',
        'Checking…':
            'Wird geprüft…',
        'Ask Anthropic for the model list now, rather than waiting for the saved one to age out':
            'Anthropic jetzt nach der Modellliste fragen, statt zu warten, bis die gespeicherte veraltet',
        "Couldn't reach Anthropic — offering the CLI's aliases (opus, sonnet, haiku)":
            'Anthropic nicht erreichbar — angeboten werden die Aliasse der CLI (opus, sonnet, haiku)',
        "Couldn't reach Anthropic — still showing the list fetched {when}":
            'Anthropic nicht erreichbar — gezeigt wird weiterhin die Liste, abgerufen {when}',
        '{count} models, updated {when}':
            '{count} Modelle, aktualisiert {when}',
        'Waiting for this session to be registered — backgrounding it now would leave the agent with no way back to it':
            'Warten, bis diese Sitzung registriert ist — sie jetzt in den Hintergrund zu schicken ließe dem Agenten keinen Weg zurück zu ihr',
        'Another session is still being handed to the background — one at a time':
            'Eine andere Sitzung wird noch in den Hintergrund übergeben — eine nach der anderen',
        'New chat (scratch folder)':
            'Neuer Chat (Arbeitsverzeichnis)',
        'Close window with {n} active session(s)?':
            'Fenster mit {n} aktiven Sitzung(en) schließen?',
        'Agents are asked to exit cleanly first; other running commands will be terminated.':
            'Agenten werden zuerst gebeten, sich sauber zu beenden; andere laufende Befehle werden abgebrochen.',
        'Backgrounding sessions…':
            'Sitzungen werden in den Hintergrund verschoben…',
        'Quit Now':
            'Jetzt beenden',
        'Handing each session to a background agent, one at a time, so every one is paired with the agent it becomes. {done} of {total} done.':
            'Jede Sitzung wird einzeln an einen Hintergrund-Agenten übergeben, damit jede mit dem Agenten gepaart ist, der sie wird. {done} von {total} erledigt.',
        'New session in {project} (no worktree)':
            'Neue Sitzung in {project} (ohne Worktree)',
        'New session in {project} (in a worktree)':
            'Neue Sitzung in {project} (in einem Worktree)',
        'Could not create chat directory':
            'Chat-Verzeichnis konnte nicht erstellt werden',
        'Trust and add':
            'Vertrauen und hinzufügen',
        'Discard draft?':
            'Entwurf verwerfen?',
        '“{label}” will be forgotten, along with any terminal panel it kept.':
            '„{label}“ wird vergessen, mitsamt einem eventuell behaltenen Terminal-Panel.',
        'Discard':
            'Verwerfen',
        "Couldn't send that to the session":
            'Das konnte nicht an die Sitzung gesendet werden',
        'Close tab with an active session?':
            'Tab mit aktiver Sitzung schließen?',
        "The agent is asked to exit cleanly first; the command running in this tab's terminal panel will be terminated.":
            'Der Agent wird zuerst gebeten, sich sauber zu beenden; der im Terminal-Panel dieses Tabs laufende Befehl wird abgebrochen.',
        'The agent is asked to exit cleanly first.':
            'Der Agent wird zuerst gebeten, sich sauber zu beenden.',
        "A command is still running in this tab's terminal panel and will be terminated.":
            'Im Terminal-Panel dieses Tabs läuft noch ein Befehl, der beendet wird.',
        "Backgrounding isn't available yet: this session hasn't been registered, so a detached agent would have no way back to it.":
            'In den Hintergrund geht noch nicht: Diese Sitzung ist noch nicht registriert, ein abgekoppelter Agent fände also nicht zu ihr zurück.',
        "Backgrounding isn't available right now: another session is still being handed to the background.":
            'In den Hintergrund geht gerade nicht: Eine andere Sitzung wird noch in den Hintergrund übergeben.',
        'No matching agent':
            'Kein passender Agent',
        'No background agent matches this session — either its agent is gone, or more than one candidate matched and guessing would link the wrong one. The transcript itself is intact.':
            'Kein Hintergrund-Agent passt zu dieser Sitzung — entweder ist ihr Agent weg, oder mehr als ein Kandidat passte und Raten würde den falschen verknüpfen. Das Transkript selbst ist intakt.',
        'Session linked':
            'Sitzung verknüpft',
        'Linked to its running background agent.':
            'Mit ihrem laufenden Hintergrund-Agenten verknüpft.',
        'Nothing to repair':
            'Nichts zu reparieren',
        'This session is already its own background agent. Opening it attaches to that agent.':
            'Diese Sitzung ist bereits ihr eigener Hintergrund-Agent. Sie zu öffnen verbindet mit diesem Agenten.',
        'No pull request is linked to this session yet':
            'Mit dieser Sitzung ist noch kein Pull Request verknüpft',
        'Undo':
            'Rückgängig',
        'Archived “{name}”':
            '„{name}“ archiviert',
        'Archived {n} sessions':
            '{n} Sitzungen archiviert',
        'Delete {n} archived session(s)?':
            '{n} archivierte Sitzung(en) löschen?',
        'Keep the {p} emptied project(s) in the sidebar':
            '{p} geleerte(s) Projekt(e) in der Seitenleiste behalten',
        'Manage and resume your AI coding agent sessions.\n\nUnofficial community tool — not affiliated with or endorsed by Anthropic.':
            'Verwalten Sie Ihre KI-Coding-Agent-Sitzungen und setzen Sie sie fort.\n\nInoffizielles Community-Tool — weder mit Anthropic verbunden noch von Anthropic gebilligt.',
    },
    "es": {
        'Before you start':
            'Antes de empezar',
        "Collins runs Claude for you in a few places. Here's where, and the switches for each.":
            'Collins ejecuta Claude por usted en algunos sitios. Aquí está dónde, y el interruptor de cada uno.',
        'Continue':
            'Continuar',
        'Using claude at {path}':
            'Usando claude en {path}',
        'Change it later in Preferences':
            'Cámbielo más tarde en Preferencias',
        "Claude Code CLI":
            "CLI de Claude Code",
        "Use This CLI":
            "Usar esta CLI",
        "Browse…":
            "Examinar…",
        "Path to the claude executable":
            "Ruta del ejecutable claude",
        "Collins needs the Claude Code CLI":
            "Collins necesita la CLI de Claude Code",
        "Found it — Collins will remember this location.":
            "Encontrado — Collins recordará esta ubicación.",
        "Choose the claude executable":
            "Elegir el ejecutable claude",
        "No Claude Code yet? Get it at {link}, then come back.":
            "¿Aún no tiene Claude Code? Consígalo en {link} y vuelva después.",
        "There's no executable file at this path.":
            "No hay ningún archivo ejecutable en esta ruta.",
        "It wasn't in any of the usual places — enter or browse to where it's installed.":
            "No estaba en ninguno de los lugares habituales — escriba o examine dónde está instalado.",
        "That's an executable, but not one named “claude” — pick the claude launcher itself.":
            "Es un ejecutable, pero no uno llamado «claude» — elija el propio lanzador claude.",
        "This is inside a version manager's tree, so Collins can't validate a stable path — it will work until that tool updates, and then this question comes back.":
            "Está dentro del árbol de un gestor de versiones, así que Collins no puede validar una ruta estable — funcionará hasta que esa herramienta se actualice, y entonces esta pregunta volverá.",
        "This path has a version number in it, so it would break the next time Claude Code updates itself. Point at a stable launcher instead — usually ~/.local/bin/claude.":
            "Esta ruta tiene un número de versión, así que se rompería la próxima vez que Claude Code se actualice. Apunte a un lanzador estable — normalmente ~/.local/bin/claude.",
        "Every session runs through the claude command, and it isn't on the PATH that launches from the desktop are given — that PATH doesn't include the folders your shell adds. Point Collins at the CLI once; the location is remembered from then on.":
            "Cada sesión pasa por el comando claude, y no está en el PATH que reciben los programas lanzados desde el escritorio — ese PATH no incluye las carpetas que añade su shell. Indique a Collins dónde está la CLI una vez; la ubicación se recuerda desde entonces.",
        "Token use":
            "Uso de tokens",
        "Each of these runs Claude on your behalf, against your subscription's usage limits, without a prompt from you. Every run is a headless claude -p from a scratch directory, carrying none of your skills, MCP servers, or the CLI's tools, so it never appears as a session and costs little more than its prompt.":
            "Cada una de estas opciones ejecuta Claude en su nombre, contra los límites de uso de su suscripción, sin que usted lo pida. Cada ejecución es un claude -p sin interfaz desde un directorio temporal, sin sus skills, servidores MCP ni las herramientas de la CLI, así que nunca aparece como sesión y cuesta poco más que su prompt.",
        "Auto-renew the Claude login":
            "Renovar automáticamente el inicio de sesión de Claude",
        "When the login the usage panel and model list are fetched with has expired — at launch, or when a fetch is refused later — run one throwaway claude -p (a one-word prompt on Haiku) so the CLI renews it; off, the panel says to run claude yourself":
            "Cuando el inicio de sesión con el que se obtienen el panel de uso y la lista de modelos ha caducado — al arrancar, o cuando una consulta se rechaza más tarde —, ejecuta un claude -p desechable (un prompt de una palabra en Haiku) para que la CLI lo renueve; desactivado, el panel le pide ejecutar claude usted mismo",
        "{status} · free, no tokens":
            "{status} · gratis, sin tokens",
        'Names each new session from its first prompt — every session Collins sees under ~/.claude/projects, including ones an agent or a terminal started. None: sessions keep the first words of their prompt, which costs nothing':
            'Nombra cada sesión nueva a partir de su primer prompt — cada sesión que Collins ve en ~/.claude/projects, incluidas las iniciadas por un agente o una terminal. Ninguna: las sesiones conservan las primeras palabras de su prompt, lo que no cuesta nada',
        "Model the sidebar's Generate Icon dialog starts with. None: the dialog waits for you to pick a model and click Generate":
            'Modelo con el que arranca el diálogo Generar icono de la barra lateral. Ninguna: el diálogo espera a que elija un modelo y pulse Generar',
        'Regenerate name ({model})':
            'Regenerar nombre ({model})',
        'Pick a model to generate an icon':
            'Elija un modelo para generar un icono',
        'Generate':
            'Generar',
        'Choose a model…':
            'Elegir un modelo…',
        'Add the Ubuntu PPA…': 'Añadir el PPA de Ubuntu…',
        'Add the package repository…':
            'Añadir el repositorio de paquetes…',
        'Add the Ubuntu PPA?': '¿Añadir el PPA de Ubuntu?',
        "Collins isn't installed from ppa:episode6/stable yet. The PPA keeps it updated with the rest of the system: apt upgrade and the software updater both pick up new releases.":
            'Collins aún no está instalado desde ppa:episode6/stable. El PPA lo mantiene actualizado con el resto del sistema: tanto apt upgrade como el actualizador de software recogen las nuevas versiones.',
        'Add the Fedora COPR…': 'Añadir el COPR de Fedora…',
        'Add the Fedora COPR?': '¿Añadir el COPR de Fedora?',
        "Collins isn't installed from the episode6/stable COPR yet. The COPR keeps it updated with the rest of the system: dnf upgrade and the software updater both pick up new releases.":
            'Collins aún no está instalado desde el COPR episode6/stable. El COPR lo mantiene actualizado con el resto del sistema: tanto dnf upgrade como el actualizador de software recogen las nuevas versiones.',
        "Collins isn't installed from its package repository yet.":
            'Collins aún no está instalado desde su repositorio de paquetes.',
        'These commands ask for your password; they run in a terminal in this session.':
            'Estos comandos piden tu contraseña; se ejecutan en una terminal de esta sesión.',
        'Run these in a terminal — they ask for your password.':
            'Ejecútalos en una terminal — piden tu contraseña.',
        'Run in Terminal': 'Ejecutar en la terminal',
        "Couldn't open a terminal": 'No se pudo abrir una terminal',
        'Run the commands in a terminal of your own instead.':
            'Ejecuta los comandos en una terminal propia en su lugar.',
        "── restored panel history ──": "── historial del panel restaurado ──",
        "Rename session": "Renombrar sesión",
        "Custom name": "Nombre personalizado",
        "Cancel": "Cancelar",
        "Show folder paths in sidebar": "Mostrar rutas de carpeta en la barra lateral",
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
        "Continue last {name} session…": "Continuar la última sesión de {name}…",
        "Model": "Modelo",
        "Default": "Predeterminado",
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
        "Color scheme": "Esquema de color",
        "Dark / Light Mode": "Modo oscuro / claro",
        "Language": "Idioma",
        "When archiving a running session": "Al archivar una sesión en ejecución",
        "Archiving a session that is still running also closes its tab":
            "Archivar una sesión que sigue en ejecución también cierra su pestaña",
        "When quitting with running sessions": "Al salir con sesiones en ejecución",
        "Closing a window while agent sessions are still running":
            "Cerrar una ventana mientras las sesiones del agente siguen en ejecución",
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
        "Search sessions…": "Buscar sesiones…",
        "Search sessions": "Buscar sesiones",
        "Close search": "Cerrar la búsqueda",
        "A session is working": "Una sesión está trabajando",
        "Collapse all groups": "Contraer todos los grupos",
        "Expand all groups": "Expandir todos los grupos",
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
        "{n} sessions": "{n} sesiones",
        "Finished a run": "Terminó una ejecución",
        "Default: the desktop's message sound": "Predeterminado: el sonido de mensaje del escritorio",
        "Silent": "Silencio",
        "Bell": "Campana",
        "Complete": "Completado",
        "Message": "Mensaje",
        "Information": "Información",
        "Zen": "Zen",
        "Soft": "Suave",
        "Glass": "Cristal",
        "Confirmation": "Confirmación",
        "Pluck": "Punteo",
        "The desktop's “{event}” sound": "El sonido «{event}» del escritorio",
        "Ships with Collins: {source} (CC0)": "Incluido con Collins: {source} (CC0)",
        "Rang the bell": "Sonó la campana",
        "In-app notifications": "Notificaciones en la aplicación",
        "Card theme": "Tema de la tarjeta",
        "The in-app card's own light or dark, whatever the app is": "Claro u oscuro para la propia tarjeta, sea cual sea la aplicación",
        "Follow app": "Seguir la aplicación",
        "Show a message from another session inside the window while Collins is focused. Off sends every notification to the desktop": "Mostrar un mensaje de otra sesión dentro de la ventana mientras Collins tiene el foco. Desactivado, cada notificación va al escritorio",
        "Sound": "Sonido",
        "Custom…": "Personalizado…",
        "Play the notification sound": "Reproducir el sonido de notificación",
        "Choose a different sound file": "Elegir otro archivo de sonido",
        "Bells from other sessions": "Campanas de otras sesiones",
        "A terminal bell from a session you aren't looking at posts a notification and plays the sound. Off keeps the desktop's beep": "Una campana de terminal de una sesión que no está mirando publica una notificación y reproduce el sonido. Desactivado, se mantiene el pitido del escritorio",
        "Announce finished runs": "Anunciar ejecuciones terminadas",
        "Also notify when a session's run finishes, not only when it asks for you": "Notificar también cuando termina la ejecución de una sesión, no solo cuando pregunta por usted",
        "Check for updates": "Buscar actualizaciones",
        "Ask GitHub once a day whether a newer Collins is out, and notify you when one is. Through your gh login, or anonymously": "Preguntar a GitHub una vez al día si hay un Collins más nuevo y avisarle cuando lo haya. Con su sesión de gh, o de forma anónima",
        "Collins {version} is available": "Collins {version} está disponible",
        "You're running {version}. Click to open the release on GitHub": "Está usando {version}. Haga clic para abrir la versión en GitHub",
        "Sound needs GStreamer ({package}); the desktop's beep is used instead": "El sonido necesita GStreamer ({package}); se usa el pitido del escritorio en su lugar",
        "Choose a notification sound": "Elija un sonido de notificación",
        "Sound files": "Archivos de sonido",
        "Notifications": "Notificaciones",
        "1 unread notification": "1 notificación sin leer",
        "{n} unread notifications": "{n} notificaciones sin leer",
        "just now": "ahora mismo",
        "{n}s ago": "hace {n} s",
        "{n}m ago": "hace {n} min",
        "{n}h ago": "hace {n} h",
        "yesterday": "ayer",
        "{n}d ago": "hace {n} días",
        "{body} ×{n}": "{body} ×{n}",
        "Untitled session": "Sesión sin título",
        "Mark all read": "Marcar todo como leído",
        "Mark every notification read": "Marcar todas las notificaciones como leídas",
        "Clear": "Vaciar",
        "Remove every notification": "Quitar todas las notificaciones",
        "Unread": "Sin leer",
        "Earlier": "Anteriores",
        "No notifications": "No hay notificaciones",
        "Messages from sessions you aren't looking at, and bells, land here.": "Aquí llegan los mensajes de las sesiones que no estás mirando, y las campanas.",
        "Mark read": "Marcar como leída",
        "Remove": "Quitar",
        "Sound: {name}": "Sonido: {name}",
        "Preferences…": "Preferencias…",
        "Show/hide notifications": "Mostrar/ocultar las notificaciones",
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
        "Right-click to open on GitHub": "Haz clic derecho para abrir en GitHub",
        "Open on GitHub": "Abrir en GitHub",
        "Git pull failed": "Falló git pull",
        "git was not found on PATH.": "No se encontró git en el PATH.",
        "git exited with status {code}": "git terminó con estado {code}",
        "Pulled {project} — {summary}": "{project} actualizado — {summary}",
        "Pulled {project}": "{project} actualizado",
        "Checkout {branch}": "Cambiar a {branch}",
        "Checkout default branch": "Cambiar a la rama predeterminada",
        "Git checkout failed": "Falló git checkout",
        "Checked out {branch} in {project}": "{branch} activada en {project}",
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
        "Back to files": "Volver a los archivos",
        "Single column when narrow": "Una columna cuando es estrecho",
        "An editor column this many pixels wide or narrower shows the file tree and the open file one at a time, with a back button beside the tabs (0 = always side by side)":
            "Una columna del editor de este ancho en píxeles o más estrecha muestra el árbol de archivos y el archivo abierto de uno en uno, con un botón de volver junto a las pestañas (0 = siempre lado a lado)",
        "Open File": "Abrir archivo",
        "Open a file…": "Abrir un archivo…",
        "Indexing project files…": "Indexando archivos del proyecto…",
        "No files found in this project.": "No se encontraron archivos en este proyecto.",
        "Project is large — only the first {count} files are searchable.":
            "El proyecto es grande — solo se puede buscar entre los primeros {count} archivos.",
        "Agent files": "Archivos del agente",
        "Open {name} in the editor": "Abrir {name} en el editor",
        "Session behavior": "Comportamiento de las sesiones",
        "Composer": "Compositor",
        "Built-in MCP tools": "Herramientas MCP integradas",
        "Every enabled tool's definition rides in each session's context, "
        "read_terminal sends the panel's text into the conversation, and a "
        "session start_session starts is titled like any other. Turning one "
        "off takes effect immediately; sessions already running are only "
        "offered the tool again once they restart":
            "La definición de cada herramienta activada viaja en el contexto de "
            "cada sesión, read_terminal envía el texto del panel a la "
            "conversación, y una sesión iniciada por start_session recibe título "
            "como cualquier otra. Desactivar una surte efecto de inmediato; las "
            "sesiones ya en marcha solo vuelven a recibirla cuando se reinician",
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
        "Send notifications": "Enviar notificaciones",
        "notify_user — a card in the window or a desktop notification, titled with the session; clicking it opens the tab":
            "notify_user — una tarjeta en la ventana o una notificación de escritorio con el nombre de la sesión; al hacer clic se abre la pestaña",
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
        'Add application':
            'Añadir aplicación',
        'Search applications…':
            'Buscar aplicaciones…',
        'Caffeine Mode is on':
            'Caffeine Mode está activado',
        "That file isn't an image Collins can display.":
            'Ese archivo no es una imagen que Collins pueda mostrar.',
        'Until idle':
            'Hasta inactividad',
        'Indefinitely':
            'Indefinidamente',
        '1 hour':
            '1 hora',
        '{n} hours':
            '{n} horas',
        'Caffeine Mode is dozing until a session works again — then the computer and screen will stay awake':
            'Caffeine Mode dormita hasta que una sesión vuelva a trabajar — entonces el equipo y la pantalla se mantendrán despiertos',
        'Caffeine Mode is dozing until a session works again — then the computer will stay awake, the screen may turn off':
            'Caffeine Mode dormita hasta que una sesión vuelva a trabajar — entonces el equipo se mantendrá despierto, la pantalla puede apagarse',
        'Caffeine Mode is on while sessions are working — the computer and screen will stay awake':
            'Caffeine Mode está activado mientras las sesiones trabajan — el equipo y la pantalla se mantendrán despiertos',
        'Caffeine Mode is on while sessions are working — the computer will stay awake, the screen may turn off':
            'Caffeine Mode está activado mientras las sesiones trabajan — el equipo se mantendrá despierto, la pantalla puede apagarse',
        'Caffeine Mode is on — the computer and screen will stay awake':
            'Caffeine Mode está activado — el equipo y la pantalla se mantendrán despiertos',
        'Caffeine Mode is on — the computer will stay awake, the screen may turn off':
            'Caffeine Mode está activado — el equipo se mantendrá despierto, la pantalla puede apagarse',
        'Caffeine Mode: keep the computer awake and the screen on':
            'Caffeine Mode: mantener el equipo despierto y la pantalla encendida',
        'Caffeine Mode: keep the computer awake, letting the screen turn off':
            'Caffeine Mode: mantener el equipo despierto, dejando que la pantalla se apague',
        'Caffeine Mode turns off in {time} — computer and screen stay awake':
            'Caffeine Mode se apaga en {time} — el equipo y la pantalla siguen despiertos',
        'Caffeine Mode turns off in {time} — computer stays awake, screen may turn off':
            'Caffeine Mode se apaga en {time} — el equipo sigue despierto, la pantalla puede apagarse',
        'Send':
            'Enviar',
        'The user declined this action.':
            'El usuario rechazó esta acción.',
        'Switch the effort level for this session':
            'Cambiar el nivel de esfuerzo de esta sesión',
        "couldn't save a copy of the dropped image":
            'no se pudo guardar una copia de la imagen soltada',
        "skipped {n} item that isn't a local file":
            'se omitió {n} elemento que no es un archivo local',
        "couldn't reference {n} dropped file name":
            'no se pudo referenciar {n} nombre de archivo soltado',
        "couldn't save a copy of the pasted image":
            'no se pudo guardar una copia de la imagen pegada',
        'Effort':
            'Esfuerzo',
        'Copied to clipboard':
            'Copiado al portapapeles',
        'Rename folder':
            'Renombrar carpeta',
        'Rename file':
            'Renombrar archivo',
        'Enter a new name for “{name}”.':
            'Escribe un nombre nuevo para «{name}».',
        'Rename':
            'Renombrar',
        'Move editor to {name}?':
            '¿Mover el editor a {name}?',
        'This session is now working in {path}. One open file has unsaved changes and also exists there — choose what happens to it.':
            'Esta sesión ahora trabaja en {path}. Un archivo abierto tiene cambios sin guardar y también existe allí — elige qué pasa con él.',
        'Stay':
            'Quedarse',
        'Go on editing this file, where your unsaved changes belong':
            'Seguir editando este archivo, donde están tus cambios sin guardar',
        'Take edits':
            'Llevar los cambios',
        'Move this tab to the new copy, keeping your unsaved changes — saving will write them over whatever that copy holds':
            'Mover esta pestaña a la copia nueva conservando tus cambios sin guardar — al guardar se escribirán sobre lo que esa copia contenga',
        'Use new':
            'Usar la nueva',
        'Open the new copy and discard your unsaved changes':
            'Abrir la copia nueva y descartar tus cambios sin guardar',
        "Don't Move":
            'No mover',
        'Move Editor':
            'Mover el editor',
        'Do you trust this folder?':
            '¿Confías en esta carpeta?',
        '{agent} will be able to read, edit and execute files in\n\n{path}\n\nand everything inside it, including any worktrees it creates there. Open it only if this is a project you created or otherwise trust — like your own code, a well-known open source project, or work from your team.':
            '{agent} podrá leer, editar y ejecutar archivos en\n\n{path}\n\ny todo lo que contiene, incluidos los worktrees que cree ahí. Ábrela solo si es un proyecto que creaste o en el que confías — como tu propio código, un proyecto de código abierto conocido o trabajo de tu equipo.',
        'Trust and open':
            'Confiar y abrir',
        'Generating icon…':
            'Generando el icono…',
        'At sidebar size':
            'A tamaño de barra lateral',
        'Optional adjustments, e.g. “make it blue”':
            'Ajustes opcionales, p. ej. «hazlo azul»',
        'Regenerate':
            'Regenerar',
        'Default model':
            'Modelo predeterminado',
        "Model for this dialog's runs; Preferences sets the default":
            'Modelo para las ejecuciones de este diálogo; Preferencias establece el predeterminado',
        'Default ({model})':
            'Predeterminado ({model})',
        'Generate Icon':
            'Generar icono',
        'the generated SVG could not be rendered':
            'no se pudo renderizar el SVG generado',
        'Icon generation failed: {error}':
            'Falló la generación del icono: {error}',
        'Saving failed: {error}':
            'Falló el guardado: {error}',
        'Close other tabs':
            'Cerrar las otras pestañas',
        'Close tabs to the right':
            'Cerrar las pestañas a la derecha',
        'Close all tabs':
            'Cerrar todas las pestañas',
        'Add to chat':
            'Añadir al chat',
        "Couldn't rename {name}: {message}":
            'No se pudo renombrar «{name}»: {message}',
        'A name is needed to rename {name}.':
            'Se necesita un nombre para renombrar «{name}».',
        "“{new_name}” isn't a name — renaming can't move things elsewhere.":
            '«{new_name}» no es un nombre — renombrar no puede mover cosas a otro lugar.',
        '“{new_name}” already exists here.':
            '«{new_name}» ya existe aquí.',
        '{name} is no longer there.':
            '«{name}» ya no está ahí.',
        "{name} can't be renamed to something outside this project.":
            '«{name}» no se puede renombrar a algo fuera de este proyecto.',
        "There's nothing on the clipboard to paste here.":
            'No hay nada en el portapapeles que pegar aquí.',
        "{count} item couldn't be pasted.":
            'No se pudo pegar {count} elemento.',
        "{name} can't be pasted into itself.":
            '«{name}» no se puede pegar dentro de sí mismo.',
        'That folder is no longer there.':
            'Esa carpeta ya no está ahí.',
        "{name} can't be pasted outside this project.":
            '«{name}» no se puede pegar fuera de este proyecto.',
        'There are already too many copies of {name} here.':
            'Ya hay demasiadas copias de «{name}» aquí.',
        "Couldn't paste {name}: {message}":
            'No se pudo pegar «{name}»: {message}',
        "{name} couldn't be decoded as an image.":
            '«{name}» no se pudo decodificar como imagen.',
        'Session moved to {name}':
            'Sesión movida a {name}',
        'Follow':
            'Seguir',
        'Image':
            'Imagen',
        'Cut':
            'Cortar',
        '{n} session(s) in {p} project(s) have their transcripts moved to the trash, where they can be restored. Sessions archived with their whole project — and originals a backgrounded fork replaced — are included.':
            'Las transcripciones de {n} sesión(es) de {p} proyecto(s) se mueven a la papelera, donde se pueden restaurar. Se incluyen las sesiones archivadas con todo su proyecto — y los originales que un fork en segundo plano reemplazó.',
        '{project} — {n} of {total}':
            '{project} — {n} de {total}',
        '…and {p} other project(s) — {n} session(s)':
            '…y {p} proyecto(s) más — {n} sesión(es)',
        '{p} of these project(s) lose every session they have.':
            '{p} de estos proyecto(s) pierden todas las sesiones que tienen.',
        'Open, every check passed':
            'Abierto, todas las comprobaciones superadas',
        'Checks still running':
            'Comprobaciones aún en ejecución',
        'A check failed':
            'Falló una comprobación',
        'A reviewer is waiting on a reply':
            'Un revisor espera una respuesta',
        'Draft, and the branch conflicts':
            'Borrador, y la rama tiene conflictos',
        'Merged':
            'Fusionado',
        'Collins is better with the GitHub CLI':
            'Collins es mejor con la CLI de GitHub',
        "Collins follows the pull requests your sessions open — and acts on them — through gh, GitHub's own command-line tool, which isn't installed here.":
            'Collins sigue los pull requests que abren tus sesiones — y actúa sobre ellos — a través de gh, la herramienta de línea de comandos de GitHub, que no está instalada aquí.',
        "Collins follows the pull requests your sessions open — and acts on them — through gh, GitHub's own command-line tool, which is installed here but never signed in.":
            'Collins sigue los pull requests que abren tus sesiones — y actúa sobre ellos — a través de gh, la herramienta de línea de comandos de GitHub, que está instalada aquí pero nunca ha iniciado sesión.',
        'Not now':
            'Ahora no',
        'Get the GitHub CLI':
            'Obtener la CLI de GitHub',
        'Copy command':
            'Copiar el comando',
        "With it, every session's pull requests carry their status:":
            'Con ella, los pull requests de cada sesión llevan su estado:',
        '…and a click on one does something about it:':
            '…y un clic en uno hace algo al respecto:',
        "Don't show this again":
            'No volver a mostrar esto',
        'Install it from cli.github.com — Collins picks it up the next time it starts.':
            'Instálala desde cli.github.com — Collins la detecta la próxima vez que arranque.',
        'Run this once in any terminal. Collins asks for no login of its own.':
            'Ejecuta esto una vez en cualquier terminal. Collins no pide ningún inicio de sesión propio.',
        'Keyboard Bindings':
            'Combinaciones de teclado',
        'Reset All':
            'Restablecer todo',
        'Put every shortcut back to its default':
            'Devolver cada atajo a su valor predeterminado',
        'Click a row to change its shortcut':
            'Haz clic en una fila para cambiar su atajo',
        'Also bound to: {actions}':
            'También asignado a: {actions}',
        'Unbound':
            'Sin asignar',
        'Reset to default':
            'Restablecer el predeterminado',
        'Reset every shortcut?':
            '¿Restablecer todos los atajos?',
        'All of your custom keyboard bindings are replaced by the defaults.':
            'Todas tus combinaciones de teclado personalizadas se reemplazan por las predeterminadas.',
        '{chord} is already in use':
            '{chord} ya está en uso',
        'It is bound to {actions}. Move it to {action}?':
            'Está asignado a {actions}. ¿Moverlo a {action}?',
        'Move Shortcut':
            'Mover el atajo',
        'unbound':
            'sin asignar',
        'Set shortcut for “{action}”':
            'Establecer el atajo de «{action}»',
        'Press the new key combination. Currently: {current}.\nBackspace removes the binding; Escape keeps it.':
            'Pulsa la nueva combinación de teclas. Actual: {current}.\nRetroceso quita la asignación; Escape la conserva.',
        'Tabs and windows':
            'Pestañas y ventanas',
        'Panels':
            'Paneles',
        'Application':
            'Aplicación',
        'New session':
            'Nueva sesión',
        'Quick switcher':
            'Selector rápido',
        'Archive the current session':
            'Archivar la sesión actual',
        'Undo the last archive':
            'Deshacer el último archivado',
        'Open the pull request page':
            'Abrir la página del pull request',
        "Unbound by default; the sidebar's search button does the same.":
            'Sin asignar de forma predeterminada; el botón de búsqueda de la barra lateral hace lo mismo.',
        'Close tab':
            'Cerrar la pestaña',
        'Next tab':
            'Pestaña siguiente',
        'Previous tab':
            'Pestaña anterior',
        'Toggle the tab marker':
            'Alternar el marcador de la pestaña',
        'Show/hide the sidebar':
            'Mostrar/ocultar la barra lateral',
        'Show/hide the terminal panel':
            'Mostrar/ocultar el panel de terminal',
        'Clear the terminal panel':
            'Limpiar el panel de terminal',
        'Move the panel tab to the other side':
            'Mover la pestaña del panel al otro lado',
        'Show/hide the composer':
            'Mostrar/ocultar el redactor',
        'Show/hide the attachments gallery':
            'Mostrar/ocultar la galería de adjuntos',
        "Swap the panel's sides":
            'Intercambiar los lados del panel',
        'Unbound by default.':
            'Sin asignar de forma predeterminada.',
        'Move the panel tab to the other strip':
            'Mover la pestaña del panel a la otra franja',
        'Show/hide the editor':
            'Mostrar/ocultar el editor',
        'Quick open a file':
            'Apertura rápida de un archivo',
        'Focus the editor':
            'Enfocar el editor',
        'Save the file':
            'Guardar el archivo',
        'In the editor.':
            'En el editor.',
        'Find in the file':
            'Buscar en el archivo',
        'Copy the selection':
            'Copiar la selección',
        'With easy copy and paste on; without a selection the key reaches the terminal.':
            'Con «Copiado y pegado fáciles» activado; sin selección, la tecla llega a la terminal.',
        'With easy copy and paste on.':
            'Con «Copiado y pegado fáciles» activado.',
        'Copy (terminal-style)':
            'Copiar (estilo terminal)',
        'Paste (terminal-style)':
            'Pegar (estilo terminal)',
        'Find in the terminal':
            'Buscar en la terminal',
        'Insert a newline in the prompt':
            'Insertar una línea nueva en el prompt',
        'Zoom in':
            'Ampliar',
        'Zoom out':
            'Reducir',
        'Reset zoom':
            'Restablecer el zoom',
        'Keyboard bindings':
            'Combinaciones de teclado',
        "Couldn't display image":
            'No se pudo mostrar la imagen',
        'Open in Editor':
            'Abrir en el editor',
        'Low':
            'Bajo',
        'Medium':
            'Medio',
        'High':
            'Alto',
        'Extra high':
            'Muy alto',
        'Max':
            'Máximo',
        'This model has no effort setting':
            'Este modelo no tiene ajuste de esfuerzo',
        'New chat':
            'Nuevo chat',
        'Model to start this session on':
            'Modelo con el que iniciar esta sesión',
        'Effort level to start this session at':
            'Nivel de esfuerzo con el que iniciar esta sesión',
        'New git worktree':
            'Nuevo worktree de git',
        'Work in a fresh worktree of this project, apart from its uncommitted changes':
            'Trabajar en un worktree nuevo de este proyecto, aparte de sus cambios sin confirmar',
        'Empty Session':
            'Sesión vacía',
        'Start the session with no prompt':
            'Iniciar la sesión sin prompt',
        'Drag to move this tab: drop on an edge to split, on a strip to join':
            'Arrastra para mover esta pestaña: suéltala en un borde para dividir, en una franja para unir',
        'Restore this tab to its size and place in the panel':
            'Restaurar esta pestaña a su tamaño y lugar en el panel',
        'Close Tab':
            'Cerrar la pestaña',
        'Overlay this tab over the whole session':
            'Superponer esta pestaña sobre toda la sesión',
        'Move this tab to the other side':
            'Mover esta pestaña al otro lado',
        'Close tab with a running command?':
            '¿Cerrar la pestaña con un comando en ejecución?',
        'Move to':
            'Mover a',
        'Split Left':
            'Dividir a la izquierda',
        'Split Right':
            'Dividir a la derecha',
        'Split Up':
            'Dividir arriba',
        'Split Down':
            'Dividir abajo',
        'Close tabs with running commands?':
            '¿Cerrar pestañas con comandos en ejecución?',
        'A command is still running in one of these tabs and will be terminated.':
            'Todavía se está ejecutando un comando en una de estas pestañas y se terminará.',
        'Close Tabs':
            'Cerrar las pestañas',
        'Address unresolved comments':
            'Atender los comentarios sin resolver',
        'Send “{prompt}” to this session':
            'Enviar «{prompt}» a esta sesión',
        'Open a pull request':
            'Abrir un pull request',
        'Fix errors & resolve conflicts':
            'Corregir errores y resolver conflictos',
        'Address the CI errors':
            'Atender los errores de CI',
        'Fix errors':
            'Corregir errores',
        'Resolve conflicts':
            'Resolver conflictos',
        'Mark ready for review':
            'Marcar como listo para revisión',
        'Take {slug} out of draft':
            'Sacar {slug} de borrador',
        'Ready':
            'Listo',
        'Ask Claude for a review':
            'Pedir una revisión a Claude',
        'Comment “{comment}” on {slug}':
            'Comentar «{comment}» en {slug}',
        'Merge when checks pass':
            'Fusionar cuando pasen las comprobaciones',
        'Turn on auto-merge for {slug}':
            'Activar la fusión automática para {slug}',
        'Merge {slug} when its checks pass?':
            '¿Fusionar {slug} cuando pasen sus comprobaciones?',
        'GitHub merges it as soon as every required check has passed. You can still cancel auto-merge on the pull request page.':
            'GitHub lo fusiona en cuanto todas las comprobaciones requeridas hayan pasado. Aún puedes cancelar la fusión automática en la página del pull request.',
        'Enable auto-merge':
            'Activar la fusión automática',
        'Auto-Merge':
            'Fusión automática',
        'Merge pull request':
            'Fusionar el pull request',
        'Merge {slug} now':
            'Fusionar {slug} ahora',
        'Merge {slug}?':
            '¿Fusionar {slug}?',
        'Merge':
            'Fusionar',
        'Disable auto-merge':
            'Desactivar la fusión automática',
        'Stop GitHub from merging {slug} when its checks pass':
            'Impedir que GitHub fusione {slug} cuando pasen sus comprobaciones',
        'Disable Auto-Merge':
            'Desactivar la fusión automática',
        'Its checks have passed. This merges the pull request on GitHub now.':
            'Sus comprobaciones han pasado. Esto fusiona el pull request en GitHub ahora.',
        "Its checks haven't all passed. This merges the pull request on GitHub now, if the repository lets it.":
            'No todas sus comprobaciones han pasado. Esto fusiona el pull request en GitHub ahora, si el repositorio lo permite.',
        'Merge and archive session':
            'Fusionar y archivar la sesión',
        'Merge {slug} now, then archive this session':
            'Fusionar {slug} ahora y luego archivar esta sesión',
        'Merge {slug} and archive this session?':
            '¿Fusionar {slug} y archivar esta sesión?',
        'The session is archived once the merge lands — you can bring it back with Undo, or from “Show archived”.':
            'La sesión se archiva en cuanto la fusión se completa — puedes recuperarla con Deshacer o desde «Mostrar archivadas».',
        'Merge & archive':
            'Fusionar y archivar',
        'Mark ready & merge when checks pass':
            'Marcar como listo y fusionar cuando pasen las comprobaciones',
        'Take {slug} out of draft, then turn on auto-merge':
            'Sacar {slug} de borrador y luego activar la fusión automática',
        'Mark {slug} ready and merge it when its checks pass?':
            '¿Marcar {slug} como listo y fusionarlo cuando pasen sus comprobaciones?',
        'Ready & auto-merge':
            'Listo y fusión automática',
        'Ready & Auto-Merge':
            'Listo y fusión automática',
        'Mark ready & merge':
            'Marcar como listo y fusionar',
        'Take {slug} out of draft, then merge it now':
            'Sacar {slug} de borrador y luego fusionarlo ahora',
        'Mark {slug} ready and merge it?':
            '¿Marcar {slug} como listo y fusionarlo?',
        'Ready & merge':
            'Listo y fusionar',
        'Ready & Merge':
            'Listo y fusionar',
        'Mark ready, merge & archive session':
            'Marcar como listo, fusionar y archivar la sesión',
        'Take {slug} out of draft, merge it now, then archive this session':
            'Sacar {slug} de borrador, fusionarlo ahora y luego archivar esta sesión',
        'Mark {slug} ready, merge it and archive this session?':
            '¿Marcar {slug} como listo, fusionarlo y archivar esta sesión?',
        'Ready, merge & archive':
            'Listo, fusionar y archivar',
        'The pull request is marked ready for review first.':
            'El pull request se marca primero como listo para revisión.',
        'Close pull request':
            'Cerrar el pull request',
        'Close {slug} without merging':
            'Cerrar {slug} sin fusionar',
        'Close {slug}?':
            '¿Cerrar {slug}?',
        'The pull request is closed without merging. Its branch and its comments stay, and it can be reopened on GitHub.':
            'El pull request se cierra sin fusionar. Su rama y sus comentarios se conservan, y puede reabrirse en GitHub.',
        "{url} doesn't look like a pull request.":
            '{url} no parece un pull request.',
        "Collins doesn't know how to do that.":
            'Collins no sabe cómo hacer eso.',
        'Merge conflicts':
            'Conflictos de fusión',
        'Refresh':
            'Actualizar',
        'Search settings…':
            'Buscar ajustes…',
        'Search settings':
            'Buscar ajustes',
        'No settings found':
            'No se encontraron ajustes',
        'Try a different search.':
            'Prueba otra búsqueda.',
        'Tab drag handles':
            'Asas de arrastre de pestañas',
        'Drag any panel tab by its handle to move, reorder, or split it. Relies on GTK internals — turn off to fall back to plain tab dragging plus a drag grip on each panel':
            'Arrastra cualquier pestaña de panel por su asa para moverla, reordenarla o dividirla. Depende de detalles internos de GTK — desactívalo para volver al arrastre simple de pestañas más un agarre de arrastre en cada panel',
        'Project icon size':
            'Tamaño del icono de proyecto',
        'Size of the project and folder icons in the sidebar':
            'Tamaño de los iconos de proyecto y carpeta en la barra lateral',
        'Start new sessions in a git worktree':
            'Iniciar las sesiones nuevas en un worktree de git',
        "Git projects only; each new session works in its own fresh worktree, so it won't see uncommitted local changes. Right-click a project header to override per project":
            'Solo proyectos git; cada sesión nueva trabaja en su propio worktree nuevo, así que no verá cambios locales sin confirmar. Haz clic derecho en la cabecera de un proyecto para cambiarlo por proyecto',
        "Follow Claude's own session names":
            'Seguir los nombres de sesión del propio Claude',
        'Rename sessions whenever Claude names or renames them — /rename and its automatic titles; manually renamed sessions keep their name':
            'Renombrar las sesiones cuando Claude las nombra o renombra — /rename y sus títulos automáticos; las sesiones renombradas a mano conservan su nombre',
        'Exact busy tracking from the agent':
            'Seguimiento exacto de actividad desde el agente',
        "Read Claude Code's own progress announcements for the sidebar's working indicator, instead of only inferring from terminal output (fully applies to newly opened tabs)":
            'Leer los propios anuncios de progreso de Claude Code para el indicador de trabajo de la barra lateral, en lugar de inferirlo solo de la salida de la terminal (se aplica del todo a las pestañas recién abiertas)',
        'Poll for background sessions':
            'Sondear las sesiones en segundo plano',
        'Fallback: check the agent CLI every 20 seconds in case the yellow guide lines stop updating on their own':
            'Respaldo: consultar la CLI del agente cada 20 segundos por si las líneas guía amarillas dejan de actualizarse solas',
        'Typing opens the composer':
            'Escribir abre el redactor',
        "Start typing at an agent's empty prompt and the composer opens with what you typed. A dialog, a menu and the CLI's own /, !, # and @ keep their keys":
            'Empieza a escribir en el prompt vacío de un agente y el redactor se abre con lo que escribiste. Un diálogo, un menú y los /, !, # y @ propios de la CLI conservan sus teclas',
        'Right-click aims spell-check':
            'El clic derecho apunta el corrector',
        'Right-clicking a misspelled word in the composer offers corrections for that word. Off: corrections follow the text cursor instead, and a right-click never moves it':
            'Hacer clic derecho en una palabra mal escrita del redactor ofrece correcciones para esa palabra. Desactivado: las correcciones siguen al cursor de texto y el clic derecho nunca lo mueve',
        'Max width':
            'Ancho máximo',
        'Stop growing past this width and center in the tab instead (0 = no limit)':
            'Dejar de crecer más allá de este ancho y centrarse en la pestaña (0 = sin límite)',
        'Footer apps':
            'Aplicaciones del pie',
        "Buttons in each tab's footer that open the tab's directory":
            'Botones en el pie de cada pestaña que abren el directorio de la pestaña',
        'Add application…':
            'Añadir aplicación…',
        'Pull requests':
            'Pull requests',
        'Text size':
            'Tamaño del texto',
        'Reading-text size in the pull request panel, as a percentage of the app font; buttons and menus keep the app size':
            'Tamaño del texto de lectura en el panel de pull requests, como porcentaje de la fuente de la aplicación; botones y menús conservan el tamaño de la aplicación',
        'Show embedded images':
            'Mostrar imágenes incrustadas',
        'Render the images a description or comment embeds, and the changed image files, as pictures; click one to open it full size. Off, they stay links and patches, and opening a pull request downloads nothing':
            'Mostrar como imágenes las que incrusta una descripción o un comentario, y los archivos de imagen cambiados; haz clic en una para abrirla a tamaño completo. Desactivado, se quedan como enlaces y parches, y abrir un pull request no descarga nada',
        'Confirm before merging':
            'Confirmar antes de fusionar',
        'Ask before merging a pull request, enabling auto-merge, or merging and archiving the session. Off, the click merges; closing a pull request unmerged still asks either way':
            'Preguntar antes de fusionar un pull request, activar la fusión automática o fusionar y archivar la sesión. Desactivado, el clic fusiona; cerrar un pull request sin fusionar pregunta igualmente',
        'Attach pull requests named in prompts':
            'Adjuntar los pull requests mencionados en los prompts',
        "Put every pull request a new session's first prompt mentions on that session's row, without waiting for the agent to touch it":
            'Poner cada pull request que mencione el primer prompt de una sesión nueva en la fila de esa sesión, sin esperar a que el agente lo toque',
        'Rename sessions after their pull requests':
            'Renombrar las sesiones según sus pull requests',
        'Retitle a session to match the newest pull request opened in it; manually renamed sessions keep their name':
            'Retitular una sesión para que coincida con el pull request más reciente abierto en ella; las sesiones renombradas a mano conservan su nombre',
        'Refresh pull requests at launch':
            'Actualizar los pull requests al arrancar',
        "Ask GitHub about every listed session's pull requests once on startup, so the marks in the sidebar start out current rather than as they were left":
            'Preguntar a GitHub por los pull requests de cada sesión listada una vez al inicio, para que las marcas de la barra lateral empiecen al día en lugar de como quedaron',
        'Caffeine Mode':
            'Caffeine Mode',
        'Keep screen on':
            'Mantener la pantalla encendida',
        'Hold the screen on as well as keeping the computer awake. Off lets the screen turn off as usual, while an unattended agent still keeps the computer from sleeping':
            'Mantener la pantalla encendida además de mantener el equipo despierto. Desactivado, la pantalla se apaga como de costumbre, mientras un agente desatendido sigue impidiendo que el equipo se duerma',
        'Until idle grace period':
            'Periodo de gracia de «Hasta inactividad»',
        'How many minutes Until idle keeps the computer awake after the last session stops working; any session picking work back up restarts the wait':
            'Cuántos minutos mantiene «Hasta inactividad» el equipo despierto después de que la última sesión deje de trabajar; cualquier sesión que retome el trabajo reinicia la espera',
        'Turn on at launch':
            'Activar al arrancar',
        'Start with Caffeine Mode already on, keeping the computer awake until you turn it off from the header':
            'Arrancar con Caffeine Mode ya activado, manteniendo el equipo despierto hasta que lo apagues desde la cabecera',
        'Turn off after':
            'Apagar después de',
        'Open in a window on small screens':
            'Abrir en una ventana en pantallas pequeñas',
        'On screens this many pixels wide or narrower (after display scaling), the editor opens in its own window instead of a panel (0 = always open as a panel)':
            'En pantallas de este ancho en píxeles o más estrechas (tras el escalado de pantalla), el editor se abre en su propia ventana en lugar de en un panel (0 = abrir siempre como panel)',
        'Show status icon':
            'Mostrar el icono de estado',
        'Shows Collins in the top bar, with a menu that jumps to any open session':
            'Muestra Collins en la barra superior, con un menú que salta a cualquier sesión abierta',
        'No status-icon support was found in this desktop — GNOME needs an AppIndicator extension':
            'No se encontró soporte de icono de estado en este escritorio — GNOME necesita una extensión AppIndicator',
        'Nothing on this desktop can show a status icon':
            'Nada en este escritorio puede mostrar un icono de estado',
        'Using the claude found on PATH at {path}.':
            'Usando el claude encontrado en el PATH, en {path}.',
        "claude isn't on PATH — Collins will ask where it is at the next launch.":
            'claude no está en el PATH — Collins preguntará dónde está en el próximo arranque.',
        'How long that launch-time Caffeine Mode runs before it turns itself off. Until idle never does: it holds the computer awake while any session is working (and {n} minute past), dozing in between':
            'Cuánto dura ese Caffeine Mode de arranque antes de apagarse solo. «Hasta inactividad» nunca lo hace: mantiene el equipo despierto mientras alguna sesión trabaja (y {n} minuto más), dormitando entre tanto',
        'Move up':
            'Subir',
        'Move down':
            'Bajar',
        'No apps configured':
            'No hay aplicaciones configuradas',
        'Before':
            'Antes',
        'After':
            'Después',
        'Back to the pull requests':
            'Volver a los pull requests',
        'View in Collins':
            'Ver en Collins',
        "Open this pull request's page beside the session":
            'Abrir la página de este pull request junto a la sesión',
        'View unresolved comments':
            'Ver los comentarios sin resolver',
        "Open this pull request's page at its first unresolved thread":
            'Abrir la página de este pull request en su primer hilo sin resolver',
        "Collins couldn't run that action.":
            'Collins no pudo ejecutar esa acción.',
        '{action} failed':
            'Falló {action}',
        'Pull request':
            'Pull request',
        'Merging when checks pass':
            'Se fusionará cuando pasen las comprobaciones',
        "The GitHub CLI (gh) isn't installed, or isn't on PATH.":
            'La CLI de GitHub (gh) no está instalada o no está en el PATH.',
        "Collins couldn't run gh.":
            'Collins no pudo ejecutar gh.',
        'gh exited with status {code}.':
            'gh terminó con estado {code}.',
        'and {n} more':
            'y {n} más',
        'Approved':
            'Aprobado',
        'Changes requested':
            'Cambios solicitados',
        'Review dismissed':
            'Revisión descartada',
        'Commented':
            'Comentado',
        'Reload this pull request':
            'Recargar este pull request',
        'Conversation':
            'Conversación',
        'Files':
            'Archivos',
        "Couldn't load this pull request — is the GitHub CLI signed in?":
            'No se pudo cargar este pull request — ¿ha iniciado sesión la CLI de GitHub?',
        'Nothing loaded yet.':
            'Aún no se ha cargado nada.',
        'Merges {head} into {base}':
            'Fusiona {head} en {base}',
        '{n} file':
            '{n} archivo',
        'No comments yet.':
            'Aún no hay comentarios.',
        'No description provided.':
            'No se dio ninguna descripción.',
        'No changed files.':
            'No hay archivos cambiados.',
        'Checks ({n})':
            'Comprobaciones ({n})',
        'Checks':
            'Comprobaciones',
        'More actions':
            'Más acciones',
        'Right-click for more actions':
            'Haz clic derecho para más acciones',
        'Add a comment':
            'Añadir un comentario',
        'Request changes':
            'Solicitar cambios',
        'Approve':
            'Aprobar',
        'Comment':
            'Comentar',
        'Comment on {slug}':
            'Comentar en {slug}',
        'Approve {slug}':
            'Aprobar {slug}',
        'Request changes on {slug}':
            'Solicitar cambios en {slug}',
        'Address comments':
            'Atender los comentarios',
        'Request review':
            'Solicitar revisión',
        'Outdated':
            'Desactualizado',
        'The code this thread commented on has changed':
            'El código que comentaba este hilo ha cambiado',
        'Resolved':
            'Resuelto',
        'Reply':
            'Responder',
        'Reply in this thread':
            'Responder en este hilo',
        'Unresolve':
            'Reabrir',
        'Resolve':
            'Resolver',
        'Reopen this thread':
            'Reabrir este hilo',
        'Mark this thread resolved':
            'Marcar este hilo como resuelto',
        'Post reply':
            'Publicar la respuesta',
        'no diff — binary or too large':
            'sin diff — binario o demasiado grande',
        '{n} line':
            '{n} línea',
        'Show more':
            'Mostrar más',
        'Show less':
            'Mostrar menos',
        'New session in {path}':
            'Nueva sesión en {path}',
        'Expand':
            'Expandir',
        'Collapse':
            'Contraer',
        'New Thread':
            'Nuevo hilo',
        'Discard draft and close tab':
            'Descartar el borrador y cerrar la pestaña',
        'Discard draft':
            'Descartar el borrador',
        'Backgrounding is unavailable until this session is registered and any handoff in progress finishes':
            'Pasar a segundo plano no está disponible hasta que esta sesión esté registrada y termine cualquier traspaso en curso',
        'This session has no tab open.':
            'Esta sesión no tiene ninguna pestaña abierta.',
        'Delete archived sessions…':
            'Eliminar sesiones archivadas…',
        'Add project':
            'Añadir proyecto',
        'Refresh session list and pull requests':
            'Actualizar la lista de sesiones y los pull requests',
        'Chats':
            'Chats',
        'Draft':
            'Borrador',
        'Refreshing pull requests…':
            'Actualizando los pull requests…',
        'Open in new window':
            'Abrir en una ventana nueva',
        'Move to new window':
            'Mover a una ventana nueva',
        'Rename to match PR':
            'Renombrar según el PR',
        'Rename to match PR #{number}':
            'Renombrar según el PR #{number}',
        'Repair session link':
            'Reparar el vínculo de la sesión',
        'New session in new window':
            'Nueva sesión en una ventana nueva',
        'New session here (no worktree)':
            'Nueva sesión aquí (sin worktree)',
        'New session here (in a worktree)':
            'Nueva sesión aquí (en un worktree)',
        'New sessions use a worktree':
            'Las sesiones nuevas usan un worktree',
        'Git pull ({branch})':
            'Git pull ({branch})',
        'Git pull':
            'Git pull',
        'Remove project from sidebar':
            'Quitar el proyecto de la barra lateral',
        'replaced':
            'reemplazada',
        'Open composer':
            'Abrir el redactor',
        "the agent didn't start — your prompt is kept in the composer":
            'el agente no arrancó — tu prompt se conserva en el redactor',
        'Every pull request this session has opened':
            'Todos los pull requests que ha abierto esta sesión',
        'Show/hide terminal panel':
            'Mostrar/ocultar el panel de terminal',
        'Move terminals to {name}?':
            '¿Mover las terminales a {name}?',
        'This session started in a worktree at {path}. The terminal open beside it is still in the project directory — change its directory to the worktree? A terminal running a command is left alone.':
            'Esta sesión empezó en un worktree en {path}. La terminal abierta a su lado sigue en el directorio del proyecto — ¿cambiar su directorio al worktree? Una terminal con un comando en ejecución no se toca.',
        'Change Directory':
            'Cambiar el directorio',
        '{n} terminal is running a command and stayed where it was':
            '{n} terminal está ejecutando un comando y se quedó donde estaba',
        'Effort: {level}':
            'Esfuerzo: {level}',
        'Click to switch the effort level':
            'Haz clic para cambiar el nivel de esfuerzo',
        'No pull request found for this branch':
            'No se encontró ningún pull request para esta rama',
        "Re-check this branch's pull requests":
            'Volver a comprobar los pull requests de esta rama',
        "Look for this branch's pull request":
            'Buscar el pull request de esta rama',
        "Add to chat: the agent isn't running in this tab":
            'Añadir al chat: el agente no se está ejecutando en esta pestaña',
        "Add to chat isn't available for this file":
            'Añadir al chat no está disponible para este archivo',
        "skipped {n} dropped item that isn't a local file":
            'se omitió {n} elemento soltado que no es un archivo local',
        "Composer: the input box holds a paste Collins can't read":
            'Redactor: el cuadro de entrada contiene un pegado que Collins no puede leer',
        "Effort switch: the agent isn't running in this tab":
            'Cambio de esfuerzo: el agente no se está ejecutando en esta pestaña',
        "This session isn't at an empty prompt.":
            'Esta sesión no está en un prompt vacío.',
        'Start sessions in the background':
            'Iniciar sesiones en segundo plano',
        'start_session — spawn a sibling agent in a new background tab, with a prompt':
            'start_session — lanza un agente hermano en una pestaña nueva en segundo plano, con un prompt',
        'Read the terminal panel':
            'Leer el panel de terminal',
        "read_terminal — the panel tabs' text and scrollback, your own typing included":
            'read_terminal — el texto y el historial de las pestañas del panel, incluido lo que tú mismo escribes',
        'Run commands in the terminal panel':
            'Ejecutar comandos en el panel de terminal',
        'run_in_terminal — type a command into an idle panel tab (or a new one) and run it':
            'run_in_terminal — escribe un comando en una pestaña del panel inactiva (o una nueva) y lo ejecuta',
        'Default (latest Haiku)':
            'Predeterminado (último Haiku)',
        'Default (latest Sonnet)':
            'Predeterminado (último Sonnet)',
        'Session title model':
            'Modelo de títulos de sesión',
        'Icon generation model':
            'Modelo de generación de iconos',
        'Model list':
            'Lista de modelos',
        'Checking…':
            'Comprobando…',
        'Ask Anthropic for the model list now, rather than waiting for the saved one to age out':
            'Pedir a Anthropic la lista de modelos ahora, en lugar de esperar a que caduque la guardada',
        "Couldn't reach Anthropic — offering the CLI's aliases (opus, sonnet, haiku)":
            'No se pudo contactar con Anthropic — se ofrecen los alias de la CLI (opus, sonnet, haiku)',
        "Couldn't reach Anthropic — still showing the list fetched {when}":
            'No se pudo contactar con Anthropic — se sigue mostrando la lista obtenida {when}',
        '{count} models, updated {when}':
            '{count} modelos, actualizada {when}',
        'Waiting for this session to be registered — backgrounding it now would leave the agent with no way back to it':
            'Esperando a que esta sesión se registre — pasarla ahora a segundo plano dejaría al agente sin camino de vuelta a ella',
        'Another session is still being handed to the background — one at a time':
            'Otra sesión aún se está traspasando al segundo plano — de una en una',
        'New chat (scratch folder)':
            'Nuevo chat (carpeta temporal)',
        'Close window with {n} active session(s)?':
            '¿Cerrar la ventana con {n} sesión(es) activa(s)?',
        'Agents are asked to exit cleanly first; other running commands will be terminated.':
            'Primero se pide a los agentes que salgan limpiamente; los demás comandos en ejecución se terminarán.',
        'Backgrounding sessions…':
            'Pasando sesiones a segundo plano…',
        'Quit Now':
            'Salir ahora',
        'Handing each session to a background agent, one at a time, so every one is paired with the agent it becomes. {done} of {total} done.':
            'Traspasando cada sesión a un agente en segundo plano, de una en una, para que cada una quede emparejada con el agente en que se convierte. {done} de {total} hechas.',
        'New session in {project} (no worktree)':
            'Nueva sesión en {project} (sin worktree)',
        'New session in {project} (in a worktree)':
            'Nueva sesión en {project} (en un worktree)',
        'Could not create chat directory':
            'No se pudo crear el directorio del chat',
        'Trust and add':
            'Confiar y añadir',
        'Discard draft?':
            '¿Descartar el borrador?',
        '“{label}” will be forgotten, along with any terminal panel it kept.':
            '«{label}» se olvidará, junto con cualquier panel de terminal que conservara.',
        'Discard':
            'Descartar',
        "Couldn't send that to the session":
            'No se pudo enviar eso a la sesión',
        'Close tab with an active session?':
            '¿Cerrar la pestaña con una sesión activa?',
        "The agent is asked to exit cleanly first; the command running in this tab's terminal panel will be terminated.":
            'Primero se pide al agente que salga limpiamente; el comando en ejecución en el panel de terminal de esta pestaña se terminará.',
        'The agent is asked to exit cleanly first.':
            'Primero se pide al agente que salga limpiamente.',
        "A command is still running in this tab's terminal panel and will be terminated.":
            'Todavía se está ejecutando un comando en el panel de terminal de esta pestaña y se terminará.',
        "Backgrounding isn't available yet: this session hasn't been registered, so a detached agent would have no way back to it.":
            'Pasar a segundo plano aún no está disponible: esta sesión no se ha registrado, así que un agente desacoplado no tendría camino de vuelta a ella.',
        "Backgrounding isn't available right now: another session is still being handed to the background.":
            'Pasar a segundo plano no está disponible ahora mismo: otra sesión aún se está traspasando al segundo plano.',
        'No matching agent':
            'Ningún agente coincide',
        'No background agent matches this session — either its agent is gone, or more than one candidate matched and guessing would link the wrong one. The transcript itself is intact.':
            'Ningún agente en segundo plano coincide con esta sesión — o su agente ya no existe, o coincidió más de un candidato y adivinar vincularía el equivocado. La transcripción en sí está intacta.',
        'Session linked':
            'Sesión vinculada',
        'Linked to its running background agent.':
            'Vinculada a su agente en segundo plano en ejecución.',
        'Nothing to repair':
            'Nada que reparar',
        'This session is already its own background agent. Opening it attaches to that agent.':
            'Esta sesión ya es su propio agente en segundo plano. Al abrirla te conectas a ese agente.',
        'No pull request is linked to this session yet':
            'Aún no hay ningún pull request vinculado a esta sesión',
        'Undo':
            'Deshacer',
        'Archived “{name}”':
            '«{name}» archivada',
        'Archived {n} sessions':
            '{n} sesiones archivadas',
        'Delete {n} archived session(s)?':
            '¿Eliminar {n} sesión(es) archivada(s)?',
        'Keep the {p} emptied project(s) in the sidebar':
            'Mantener en la barra lateral los {p} proyecto(s) vaciados',
        'Manage and resume your AI coding agent sessions.\n\nUnofficial community tool — not affiliated with or endorsed by Anthropic.':
            'Gestiona y reanuda tus sesiones de agente de programación con IA.\n\nHerramienta comunitaria no oficial — sin afiliación ni respaldo de Anthropic.',
    },
    "fr": {
        'Before you start':
            'Avant de commencer',
        "Collins runs Claude for you in a few places. Here's where, and the switches for each.":
            "Collins exécute Claude pour vous à quelques endroits. Voici lesquels, et l'interrupteur de chacun.",
        'Continue':
            'Continuer',
        'Using claude at {path}':
            'Utilise claude depuis {path}',
        'Change it later in Preferences':
            'Modifiable plus tard dans les Préférences',
        "Claude Code CLI":
            "CLI Claude Code",
        "Use This CLI":
            "Utiliser cette CLI",
        "Browse…":
            "Parcourir…",
        "Path to the claude executable":
            "Chemin de l'exécutable claude",
        "Collins needs the Claude Code CLI":
            "Collins a besoin de la CLI Claude Code",
        "Found it — Collins will remember this location.":
            "Trouvé — Collins retiendra cet emplacement.",
        "Choose the claude executable":
            "Choisir l'exécutable claude",
        "No Claude Code yet? Get it at {link}, then come back.":
            "Pas encore de Claude Code ? Procurez-vous-le sur {link}, puis revenez.",
        "There's no executable file at this path.":
            "Il n'y a pas de fichier exécutable à ce chemin.",
        "It wasn't in any of the usual places — enter or browse to where it's installed.":
            "Il n'était à aucun des emplacements habituels — saisissez ou parcourez jusqu'à l'endroit où il est installé.",
        "That's an executable, but not one named “claude” — pick the claude launcher itself.":
            "C'est un exécutable, mais pas un nommé « claude » — choisissez le lanceur claude lui-même.",
        "This is inside a version manager's tree, so Collins can't validate a stable path — it will work until that tool updates, and then this question comes back.":
            "C'est dans l'arborescence d'un gestionnaire de versions, donc Collins ne peut pas valider un chemin stable — cela fonctionnera jusqu'à la mise à jour de cet outil, puis cette question reviendra.",
        "This path has a version number in it, so it would break the next time Claude Code updates itself. Point at a stable launcher instead — usually ~/.local/bin/claude.":
            "Ce chemin contient un numéro de version et se casserait à la prochaine mise à jour de Claude Code. Pointez plutôt vers un lanceur stable — généralement ~/.local/bin/claude.",
        "Every session runs through the claude command, and it isn't on the PATH that launches from the desktop are given — that PATH doesn't include the folders your shell adds. Point Collins at the CLI once; the location is remembered from then on.":
            "Chaque session passe par la commande claude, qui n'est pas dans le PATH donné aux programmes lancés depuis le bureau — ce PATH n'inclut pas les dossiers ajoutés par votre shell. Indiquez la CLI à Collins une fois ; l'emplacement est retenu dès lors.",
        "Token use":
            "Utilisation des jetons",
        "Each of these runs Claude on your behalf, against your subscription's usage limits, without a prompt from you. Every run is a headless claude -p from a scratch directory, carrying none of your skills, MCP servers, or the CLI's tools, so it never appears as a session and costs little more than its prompt.":
            "Chacun de ces réglages fait tourner Claude pour vous, sur les limites d’utilisation de votre abonnement, sans que vous le demandiez. Chaque exécution est un claude -p sans interface depuis un répertoire temporaire, sans vos skills, serveurs MCP ni les outils de la CLI ; elle n’apparaît donc jamais comme session et ne coûte guère plus que son prompt.",
        "Auto-renew the Claude login":
            "Renouveler automatiquement la connexion Claude",
        "When the login the usage panel and model list are fetched with has expired — at launch, or when a fetch is refused later — run one throwaway claude -p (a one-word prompt on Haiku) so the CLI renews it; off, the panel says to run claude yourself":
            "Quand la connexion qui sert à récupérer le panneau d’utilisation et la liste des modèles a expiré — au lancement, ou quand une requête est refusée plus tard —, lance un claude -p jetable (une invite d’un mot sur Haiku) pour que la CLI la renouvelle ; désactivé, le panneau vous demande de lancer claude vous-même",
        "{status} · free, no tokens":
            "{status} · gratuit, sans jetons",
        'Names each new session from its first prompt — every session Collins sees under ~/.claude/projects, including ones an agent or a terminal started. None: sessions keep the first words of their prompt, which costs nothing':
            "Nomme chaque nouvelle session d'après son premier prompt — chaque session que Collins voit sous ~/.claude/projects, y compris celles lancées par un agent ou un terminal. Aucune : les sessions gardent les premiers mots de leur prompt, ce qui ne coûte rien",
        "Model the sidebar's Generate Icon dialog starts with. None: the dialog waits for you to pick a model and click Generate":
            'Modèle avec lequel démarre la boîte de dialogue Générer une icône de la barre latérale. Aucune : la boîte attend que vous choisissiez un modèle et cliquiez sur Générer',
        'Regenerate name ({model})':
            'Régénérer le nom ({model})',
        'Pick a model to generate an icon':
            'Choisissez un modèle pour générer une icône',
        'Generate':
            'Générer',
        'Choose a model…':
            'Choisir un modèle…',
        'Add the Ubuntu PPA…': 'Ajouter le PPA Ubuntu…',
        'Add the package repository…': 'Ajouter le dépôt de paquets…',
        'Add the Ubuntu PPA?': 'Ajouter le PPA Ubuntu ?',
        "Collins isn't installed from ppa:episode6/stable yet. The PPA keeps it updated with the rest of the system: apt upgrade and the software updater both pick up new releases.":
            "Collins n'est pas encore installé depuis ppa:episode6/stable. Le PPA le maintient à jour avec le reste du système : apt upgrade et le gestionnaire de mises à jour récupèrent tous deux les nouvelles versions.",
        'Add the Fedora COPR…': 'Ajouter le COPR Fedora…',
        'Add the Fedora COPR?': 'Ajouter le COPR Fedora ?',
        "Collins isn't installed from the episode6/stable COPR yet. The COPR keeps it updated with the rest of the system: dnf upgrade and the software updater both pick up new releases.":
            "Collins n'est pas encore installé depuis le COPR episode6/stable. Le COPR le maintient à jour avec le reste du système : dnf upgrade et le gestionnaire de mises à jour récupèrent tous deux les nouvelles versions.",
        "Collins isn't installed from its package repository yet.":
            "Collins n'est pas encore installé depuis son dépôt de paquets.",
        'These commands ask for your password; they run in a terminal in this session.':
            "Ces commandes demandent votre mot de passe ; elles s'exécutent dans un terminal de cette session.",
        'Run these in a terminal — they ask for your password.':
            'Exécutez-les dans un terminal — elles demandent votre mot de passe.',
        'Run in Terminal': 'Exécuter dans le terminal',
        "Couldn't open a terminal": "Impossible d'ouvrir un terminal",
        'Run the commands in a terminal of your own instead.':
            'Exécutez plutôt les commandes dans un terminal à vous.',
        "── restored panel history ──": "── historique du panneau restauré ──",
        "Rename session": "Renommer la session",
        "Custom name": "Nom personnalisé",
        "Cancel": "Annuler",
        "Show folder paths in sidebar": "Afficher les chemins des dossiers dans la barre latérale",
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
        "Continue last {name} session…": "Continuer la dernière session {name}…",
        "Model": "Modèle",
        "Default": "Par défaut",
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
        "Color scheme": "Schéma de couleurs",
        "Dark / Light Mode": "Mode sombre / clair",
        "Language": "Langue",
        "When archiving a running session": "Lors de l’archivage d’une session en cours",
        "Archiving a session that is still running also closes its tab":
            "Archiver une session encore en cours ferme aussi son onglet",
        "When quitting with running sessions": "À la fermeture avec des sessions en cours",
        "Closing a window while agent sessions are still running":
            "Fermer une fenêtre alors que des sessions d’agent sont encore en cours",
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
        "Search sessions…": "Rechercher des sessions…",
        "Search sessions": "Rechercher des sessions",
        "Close search": "Fermer la recherche",
        "A session is working": "Une session travaille",
        "Collapse all groups": "Réduire tous les groupes",
        "Expand all groups": "Développer tous les groupes",
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
        "{n} sessions": "{n} sessions",
        "Finished a run": "A terminé une exécution",
        "Default: the desktop's message sound": "Par défaut : le son de message du bureau",
        "Silent": "Silencieux",
        "Bell": "Cloche",
        "Complete": "Terminé",
        "Message": "Message",
        "Information": "Information",
        "Zen": "Zen",
        "Soft": "Doux",
        "Glass": "Verre",
        "Confirmation": "Confirmation",
        "Pluck": "Pincement",
        "The desktop's “{event}” sound": "Le son « {event} » du bureau",
        "Ships with Collins: {source} (CC0)": "Fourni avec Collins : {source} (CC0)",
        "Rang the bell": "A sonné la cloche",
        "In-app notifications": "Notifications dans l'application",
        "Card theme": "Thème de la carte",
        "The in-app card's own light or dark, whatever the app is": "Clair ou sombre pour la carte elle-même, quelle que soit l'application",
        "Follow app": "Suivre l'application",
        "Show a message from another session inside the window while Collins is focused. Off sends every notification to the desktop": "Afficher un message d'une autre session dans la fenêtre tant que Collins a le focus. Désactivé, chaque notification va au bureau",
        "Sound": "Son",
        "Custom…": "Personnalisé…",
        "Play the notification sound": "Jouer le son de notification",
        "Choose a different sound file": "Choisir un autre fichier son",
        "Bells from other sessions": "Cloches des autres sessions",
        "A terminal bell from a session you aren't looking at posts a notification and plays the sound. Off keeps the desktop's beep": "Une cloche de terminal d'une session que vous ne regardez pas publie une notification et joue le son. Désactivé, le bip du bureau est conservé",
        "Announce finished runs": "Annoncer les exécutions terminées",
        "Also notify when a session's run finishes, not only when it asks for you": "Notifier aussi quand l'exécution d'une session se termine, pas seulement quand elle vous demande",
        "Check for updates": "Rechercher des mises à jour",
        "Ask GitHub once a day whether a newer Collins is out, and notify you when one is. Through your gh login, or anonymously": "Demander à GitHub une fois par jour si un Collins plus récent est sorti, et vous prévenir quand c'est le cas. Via votre connexion gh, ou anonymement",
        "Collins {version} is available": "Collins {version} est disponible",
        "You're running {version}. Click to open the release on GitHub": "Vous utilisez {version}. Cliquez pour ouvrir la version sur GitHub",
        "Sound needs GStreamer ({package}); the desktop's beep is used instead": "Le son nécessite GStreamer ({package}) ; le bip du bureau est utilisé à la place",
        "Choose a notification sound": "Choisir un son de notification",
        "Sound files": "Fichiers audio",
        "Notifications": "Notifications",
        "1 unread notification": "1 notification non lue",
        "{n} unread notifications": "{n} notifications non lues",
        "just now": "à l’instant",
        "{n}s ago": "il y a {n} s",
        "{n}m ago": "il y a {n} min",
        "{n}h ago": "il y a {n} h",
        "yesterday": "hier",
        "{n}d ago": "il y a {n} jours",
        "{body} ×{n}": "{body} ×{n}",
        "Untitled session": "Session sans titre",
        "Mark all read": "Tout marquer comme lu",
        "Mark every notification read": "Marquer toutes les notifications comme lues",
        "Clear": "Vider",
        "Remove every notification": "Retirer toutes les notifications",
        "Unread": "Non lues",
        "Earlier": "Plus tôt",
        "No notifications": "Aucune notification",
        "Messages from sessions you aren't looking at, and bells, land here.": "Les messages des sessions que vous ne regardez pas, et les sonneries, arrivent ici.",
        "Mark read": "Marquer comme lue",
        "Remove": "Retirer",
        "Sound: {name}": "Son : {name}",
        "Preferences…": "Préférences…",
        "Show/hide notifications": "Afficher/masquer les notifications",
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
        "Right-click to open on GitHub": "Clic droit pour ouvrir sur GitHub",
        "Open on GitHub": "Ouvrir sur GitHub",
        "Git pull failed": "Échec de git pull",
        "git was not found on PATH.": "git est introuvable dans le PATH.",
        "git exited with status {code}": "git s'est terminé avec le statut {code}",
        "Pulled {project} — {summary}": "{project} mis à jour — {summary}",
        "Pulled {project}": "{project} mis à jour",
        "Checkout {branch}": "Basculer sur {branch}",
        "Checkout default branch": "Basculer sur la branche par défaut",
        "Git checkout failed": "Échec de git checkout",
        "Checked out {branch} in {project}": "{branch} extraite dans {project}",
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
        "Back to files": "Retour aux fichiers",
        "Single column when narrow": "Une seule colonne quand c’est étroit",
        "An editor column this many pixels wide or narrower shows the file tree and the open file one at a time, with a back button beside the tabs (0 = always side by side)":
            "Une colonne d’éditeur de cette largeur en pixels ou moins affiche l’arborescence et le fichier ouvert l’un après l’autre, avec un bouton de retour à côté des onglets (0 = toujours côte à côte)",
        "Open File": "Ouvrir un fichier",
        "Open a file…": "Ouvrir un fichier…",
        "Indexing project files…": "Indexation des fichiers du projet…",
        "No files found in this project.": "Aucun fichier trouvé dans ce projet.",
        "Project is large — only the first {count} files are searchable.":
            "Le projet est volumineux — seuls les premiers {count} fichiers sont consultables.",
        "Agent files": "Fichiers de l’agent",
        "Open {name} in the editor": "Ouvrir {name} dans l’éditeur",
        "Session behavior": "Comportement des sessions",
        "Composer": "Compositeur",
        "Built-in MCP tools": "Outils MCP intégrés",
        "Every enabled tool's definition rides in each session's context, "
        "read_terminal sends the panel's text into the conversation, and a "
        "session start_session starts is titled like any other. Turning one "
        "off takes effect immediately; sessions already running are only "
        "offered the tool again once they restart":
            "La définition de chaque outil activé accompagne le contexte de "
            "chaque session, read_terminal envoie le texte du panneau dans la "
            "conversation, et une session lancée par start_session est titrée "
            "comme toute autre. La désactivation prend effet immédiatement ; les "
            "sessions déjà lancées ne retrouvent l’outil qu’à leur prochain "
            "démarrage",
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
        "Send notifications": "Envoyer des notifications",
        "notify_user — a card in the window or a desktop notification, titled with the session; clicking it opens the tab":
            "notify_user — une carte dans la fenêtre ou une notification de bureau au nom de la session ; un clic ouvre l’onglet",
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
        'Add application':
            'Ajouter une application',
        'Search applications…':
            'Rechercher des applications…',
        'Caffeine Mode is on':
            'Caffeine Mode est activé',
        "That file isn't an image Collins can display.":
            "Ce fichier n'est pas une image que Collins peut afficher.",
        'Until idle':
            "Jusqu'à inactivité",
        'Indefinitely':
            'Indéfiniment',
        '1 hour':
            '1 heure',
        '{n} hours':
            '{n} heures',
        'Caffeine Mode is dozing until a session works again — then the computer and screen will stay awake':
            "Caffeine Mode somnole jusqu'à ce qu'une session travaille à nouveau — l'ordinateur et l'écran resteront alors éveillés",
        'Caffeine Mode is dozing until a session works again — then the computer will stay awake, the screen may turn off':
            "Caffeine Mode somnole jusqu'à ce qu'une session travaille à nouveau — l'ordinateur restera alors éveillé, l'écran peut s'éteindre",
        'Caffeine Mode is on while sessions are working — the computer and screen will stay awake':
            "Caffeine Mode est activé pendant que des sessions travaillent — l'ordinateur et l'écran resteront éveillés",
        'Caffeine Mode is on while sessions are working — the computer will stay awake, the screen may turn off':
            "Caffeine Mode est activé pendant que des sessions travaillent — l'ordinateur restera éveillé, l'écran peut s'éteindre",
        'Caffeine Mode is on — the computer and screen will stay awake':
            "Caffeine Mode est activé — l'ordinateur et l'écran resteront éveillés",
        'Caffeine Mode is on — the computer will stay awake, the screen may turn off':
            "Caffeine Mode est activé — l'ordinateur restera éveillé, l'écran peut s'éteindre",
        'Caffeine Mode: keep the computer awake and the screen on':
            "Caffeine Mode : garder l'ordinateur éveillé et l'écran allumé",
        'Caffeine Mode: keep the computer awake, letting the screen turn off':
            "Caffeine Mode : garder l'ordinateur éveillé, en laissant l'écran s'éteindre",
        'Caffeine Mode turns off in {time} — computer and screen stay awake':
            "Caffeine Mode s'éteint dans {time} — l'ordinateur et l'écran restent éveillés",
        'Caffeine Mode turns off in {time} — computer stays awake, screen may turn off':
            "Caffeine Mode s'éteint dans {time} — l'ordinateur reste éveillé, l'écran peut s'éteindre",
        'Send':
            'Envoyer',
        'The user declined this action.':
            "L'utilisateur a refusé cette action.",
        'Switch the effort level for this session':
            "Changer le niveau d'effort de cette session",
        "couldn't save a copy of the dropped image":
            "impossible d'enregistrer une copie de l'image déposée",
        "skipped {n} item that isn't a local file":
            "{n} élément ignoré qui n'est pas un fichier local",
        "couldn't reference {n} dropped file name":
            'impossible de référencer {n} nom de fichier déposé',
        "couldn't save a copy of the pasted image":
            "impossible d'enregistrer une copie de l'image collée",
        'Effort':
            'Effort',
        'Copied to clipboard':
            'Copié dans le presse-papiers',
        'Rename folder':
            'Renommer le dossier',
        'Rename file':
            'Renommer le fichier',
        'Enter a new name for “{name}”.':
            'Saisissez un nouveau nom pour « {name} ».',
        'Rename':
            'Renommer',
        'Move editor to {name}?':
            "Déplacer l'éditeur vers {name} ?",
        'This session is now working in {path}. One open file has unsaved changes and also exists there — choose what happens to it.':
            "Cette session travaille maintenant dans {path}. Un fichier ouvert a des modifications non enregistrées et existe aussi là-bas — choisissez ce qu'il en advient.",
        'Stay':
            'Rester',
        'Go on editing this file, where your unsaved changes belong':
            'Continuer à modifier ce fichier, où se trouvent vos modifications non enregistrées',
        'Take edits':
            'Emporter les modifications',
        'Move this tab to the new copy, keeping your unsaved changes — saving will write them over whatever that copy holds':
            "Déplacer cet onglet vers la nouvelle copie en gardant vos modifications non enregistrées — l'enregistrement écrasera ce que contient cette copie",
        'Use new':
            'Utiliser la nouvelle',
        'Open the new copy and discard your unsaved changes':
            'Ouvrir la nouvelle copie et abandonner vos modifications non enregistrées',
        "Don't Move":
            'Ne pas déplacer',
        'Move Editor':
            "Déplacer l'éditeur",
        'Do you trust this folder?':
            'Faites-vous confiance à ce dossier ?',
        '{agent} will be able to read, edit and execute files in\n\n{path}\n\nand everything inside it, including any worktrees it creates there. Open it only if this is a project you created or otherwise trust — like your own code, a well-known open source project, or work from your team.':
            "{agent} pourra lire, modifier et exécuter des fichiers dans\n\n{path}\n\net tout ce qu'il contient, y compris les worktrees qu'il y crée. Ouvrez-le seulement s'il s'agit d'un projet que vous avez créé ou auquel vous faites confiance — comme votre propre code, un projet open source bien connu, ou le travail de votre équipe.",
        'Trust and open':
            'Faire confiance et ouvrir',
        'Generating icon…':
            "Génération de l'icône…",
        'At sidebar size':
            'À la taille de la barre latérale',
        'Optional adjustments, e.g. “make it blue”':
            'Ajustements facultatifs, p. ex. « rends-la bleue »',
        'Regenerate':
            'Régénérer',
        'Default model':
            'Modèle par défaut',
        "Model for this dialog's runs; Preferences sets the default":
            'Modèle des exécutions de cette boîte de dialogue ; les Préférences fixent le défaut',
        'Default ({model})':
            'Par défaut ({model})',
        'Generate Icon':
            'Générer une icône',
        'the generated SVG could not be rendered':
            "le SVG généré n'a pas pu être rendu",
        'Icon generation failed: {error}':
            "Échec de la génération de l'icône : {error}",
        'Saving failed: {error}':
            "Échec de l'enregistrement : {error}",
        'Close other tabs':
            'Fermer les autres onglets',
        'Close tabs to the right':
            'Fermer les onglets à droite',
        'Close all tabs':
            'Fermer tous les onglets',
        'Add to chat':
            'Ajouter à la discussion',
        "Couldn't rename {name}: {message}":
            'Impossible de renommer « {name} » : {message}',
        'A name is needed to rename {name}.':
            'Un nom est requis pour renommer « {name} ».',
        "“{new_name}” isn't a name — renaming can't move things elsewhere.":
            "« {new_name} » n'est pas un nom — le renommage ne peut pas déplacer les choses ailleurs.",
        '“{new_name}” already exists here.':
            '« {new_name} » existe déjà ici.',
        '{name} is no longer there.':
            "« {name} » n'est plus là.",
        "{name} can't be renamed to something outside this project.":
            '« {name} » ne peut pas être renommé vers un emplacement hors de ce projet.',
        "There's nothing on the clipboard to paste here.":
            "Il n'y a rien dans le presse-papiers à coller ici.",
        "{count} item couldn't be pasted.":
            "{count} élément n'a pas pu être collé.",
        "{name} can't be pasted into itself.":
            '« {name} » ne peut pas être collé dans lui-même.',
        'That folder is no longer there.':
            "Ce dossier n'est plus là.",
        "{name} can't be pasted outside this project.":
            '« {name} » ne peut pas être collé hors de ce projet.',
        'There are already too many copies of {name} here.':
            'Il y a déjà trop de copies de « {name} » ici.',
        "Couldn't paste {name}: {message}":
            'Impossible de coller « {name} » : {message}',
        "{name} couldn't be decoded as an image.":
            "« {name} » n'a pas pu être décodé comme une image.",
        'Session moved to {name}':
            'Session déplacée vers {name}',
        'Follow':
            'Suivre',
        'Image':
            'Image',
        'Cut':
            'Couper',
        '{n} session(s) in {p} project(s) have their transcripts moved to the trash, where they can be restored. Sessions archived with their whole project — and originals a backgrounded fork replaced — are included.':
            "{n} session(s) dans {p} projet(s) voient leurs transcriptions mises à la corbeille, d'où elles peuvent être restaurées. Les sessions archivées avec leur projet entier — et les originales remplacées par une bifurcation passée en arrière-plan — sont incluses.",
        '{project} — {n} of {total}':
            '{project} — {n} sur {total}',
        '…and {p} other project(s) — {n} session(s)':
            '…et {p} autre(s) projet(s) — {n} session(s)',
        '{p} of these project(s) lose every session they have.':
            '{p} de ces projets perdent toutes leurs sessions.',
        'Open, every check passed':
            'Ouverte, toutes les vérifications réussies',
        'Checks still running':
            'Vérifications en cours',
        'A check failed':
            'Une vérification a échoué',
        'A reviewer is waiting on a reply':
            'Un relecteur attend une réponse',
        'Draft, and the branch conflicts':
            'Brouillon, et la branche a des conflits',
        'Merged':
            'Fusionnée',
        'Collins is better with the GitHub CLI':
            'Collins est meilleur avec la CLI GitHub',
        "Collins follows the pull requests your sessions open — and acts on them — through gh, GitHub's own command-line tool, which isn't installed here.":
            "Collins suit les pull requests que vos sessions ouvrent — et agit dessus — via gh, l'outil en ligne de commande de GitHub, qui n'est pas installé ici.",
        "Collins follows the pull requests your sessions open — and acts on them — through gh, GitHub's own command-line tool, which is installed here but never signed in.":
            "Collins suit les pull requests que vos sessions ouvrent — et agit dessus — via gh, l'outil en ligne de commande de GitHub, qui est installé ici mais jamais connecté.",
        'Not now':
            'Pas maintenant',
        'Get the GitHub CLI':
            'Obtenir la CLI GitHub',
        'Copy command':
            'Copier la commande',
        "With it, every session's pull requests carry their status:":
            'Avec lui, les pull requests de chaque session portent leur statut :',
        '…and a click on one does something about it:':
            "…et un clic sur l'une d'elles agit en conséquence :",
        "Don't show this again":
            'Ne plus afficher ceci',
        'Install it from cli.github.com — Collins picks it up the next time it starts.':
            'Installez-le depuis cli.github.com — Collins le détecte à son prochain démarrage.',
        'Run this once in any terminal. Collins asks for no login of its own.':
            "Exécutez ceci une fois dans n'importe quel terminal. Collins ne demande aucune connexion propre.",
        'Keyboard Bindings':
            'Raccourcis clavier',
        'Reset All':
            'Tout réinitialiser',
        'Put every shortcut back to its default':
            'Remettre chaque raccourci à sa valeur par défaut',
        'Click a row to change its shortcut':
            'Cliquez sur une ligne pour changer son raccourci',
        'Also bound to: {actions}':
            'Aussi assigné à : {actions}',
        'Unbound':
            'Non assigné',
        'Reset to default':
            'Rétablir la valeur par défaut',
        'Reset every shortcut?':
            'Réinitialiser tous les raccourcis ?',
        'All of your custom keyboard bindings are replaced by the defaults.':
            'Tous vos raccourcis clavier personnalisés sont remplacés par les valeurs par défaut.',
        '{chord} is already in use':
            '{chord} est déjà utilisé',
        'It is bound to {actions}. Move it to {action}?':
            'Il est assigné à {actions}. Le déplacer vers {action} ?',
        'Move Shortcut':
            'Déplacer le raccourci',
        'unbound':
            'non assigné',
        'Set shortcut for “{action}”':
            'Définir le raccourci pour « {action} »',
        'Press the new key combination. Currently: {current}.\nBackspace removes the binding; Escape keeps it.':
            'Appuyez sur la nouvelle combinaison de touches. Actuellement : {current}.\nRetour arrière supprime le raccourci ; Échap le conserve.',
        'Tabs and windows':
            'Onglets et fenêtres',
        'Panels':
            'Panneaux',
        'Application':
            'Application',
        'New session':
            'Nouvelle session',
        'Quick switcher':
            'Sélecteur rapide',
        'Archive the current session':
            'Archiver la session actuelle',
        'Undo the last archive':
            'Annuler le dernier archivage',
        'Open the pull request page':
            'Ouvrir la page de la pull request',
        "Unbound by default; the sidebar's search button does the same.":
            'Non assigné par défaut ; le bouton de recherche de la barre latérale fait de même.',
        'Close tab':
            "Fermer l'onglet",
        'Next tab':
            'Onglet suivant',
        'Previous tab':
            'Onglet précédent',
        'Toggle the tab marker':
            "Basculer le marqueur d'onglet",
        'Show/hide the sidebar':
            'Afficher/masquer la barre latérale',
        'Show/hide the terminal panel':
            'Afficher/masquer le panneau de terminal',
        'Clear the terminal panel':
            'Vider le panneau de terminal',
        'Move the panel tab to the other side':
            "Déplacer l'onglet du panneau de l'autre côté",
        'Show/hide the composer':
            'Afficher/masquer le rédacteur',
        'Show/hide the attachments gallery':
            'Afficher/masquer la galerie des pièces jointes',
        "Swap the panel's sides":
            'Échanger les côtés du panneau',
        'Unbound by default.':
            'Non assigné par défaut.',
        'Move the panel tab to the other strip':
            "Déplacer l'onglet du panneau vers l'autre bande",
        'Show/hide the editor':
            "Afficher/masquer l'éditeur",
        'Quick open a file':
            "Ouverture rapide d'un fichier",
        'Focus the editor':
            "Donner le focus à l'éditeur",
        'Save the file':
            'Enregistrer le fichier',
        'In the editor.':
            "Dans l'éditeur.",
        'Find in the file':
            'Rechercher dans le fichier',
        'Copy the selection':
            'Copier la sélection',
        'With easy copy and paste on; without a selection the key reaches the terminal.':
            'Avec le copier-coller simplifié activé ; sans sélection la touche atteint le terminal.',
        'With easy copy and paste on.':
            'Avec le copier-coller simplifié activé.',
        'Copy (terminal-style)':
            'Copier (façon terminal)',
        'Paste (terminal-style)':
            'Coller (façon terminal)',
        'Find in the terminal':
            'Rechercher dans le terminal',
        'Insert a newline in the prompt':
            'Insérer un saut de ligne dans le prompt',
        'Zoom in':
            'Zoom avant',
        'Zoom out':
            'Zoom arrière',
        'Reset zoom':
            'Réinitialiser le zoom',
        'Keyboard bindings':
            'Raccourcis clavier',
        "Couldn't display image":
            "Impossible d'afficher l'image",
        'Open in Editor':
            "Ouvrir dans l'éditeur",
        'Low':
            'Faible',
        'Medium':
            'Moyen',
        'High':
            'Élevé',
        'Extra high':
            'Très élevé',
        'Max':
            'Max',
        'This model has no effort setting':
            "Ce modèle n'a pas de réglage d'effort",
        'New chat':
            'Nouvelle discussion',
        'Model to start this session on':
            'Modèle avec lequel démarrer cette session',
        'Effort level to start this session at':
            "Niveau d'effort avec lequel démarrer cette session",
        'New git worktree':
            'Nouveau worktree git',
        'Work in a fresh worktree of this project, apart from its uncommitted changes':
            "Travailler dans un worktree neuf de ce projet, à l'écart de ses modifications non validées",
        'Empty Session':
            'Session vide',
        'Start the session with no prompt':
            'Démarrer la session sans prompt',
        'Drag to move this tab: drop on an edge to split, on a strip to join':
            'Glissez pour déplacer cet onglet : déposez sur un bord pour scinder, sur une bande pour regrouper',
        'Restore this tab to its size and place in the panel':
            'Restaurer cet onglet à sa taille et sa place dans le panneau',
        'Close Tab':
            "Fermer l'onglet",
        'Overlay this tab over the whole session':
            'Superposer cet onglet sur toute la session',
        'Move this tab to the other side':
            "Déplacer cet onglet de l'autre côté",
        'Close tab with a running command?':
            "Fermer l'onglet avec une commande en cours ?",
        'Move to':
            'Déplacer vers',
        'Split Left':
            'Scinder à gauche',
        'Split Right':
            'Scinder à droite',
        'Split Up':
            'Scinder en haut',
        'Split Down':
            'Scinder en bas',
        'Close tabs with running commands?':
            'Fermer les onglets avec des commandes en cours ?',
        'A command is still running in one of these tabs and will be terminated.':
            "Une commande est encore en cours d'exécution dans l'un de ces onglets et sera interrompue.",
        'Close Tabs':
            'Fermer les onglets',
        'Address unresolved comments':
            'Traiter les commentaires non résolus',
        'Send “{prompt}” to this session':
            'Envoyer « {prompt} » à cette session',
        'Open a pull request':
            'Ouvrir une pull request',
        'Fix errors & resolve conflicts':
            'Corriger les erreurs et résoudre les conflits',
        'Address the CI errors':
            'Traiter les erreurs de CI',
        'Fix errors':
            'Corriger les erreurs',
        'Resolve conflicts':
            'Résoudre les conflits',
        'Mark ready for review':
            'Marquer comme prête pour relecture',
        'Take {slug} out of draft':
            'Sortir {slug} du brouillon',
        'Ready':
            'Prête',
        'Ask Claude for a review':
            'Demander une relecture à Claude',
        'Comment “{comment}” on {slug}':
            'Commenter « {comment} » sur {slug}',
        'Merge when checks pass':
            'Fusionner quand les vérifications passent',
        'Turn on auto-merge for {slug}':
            'Activer la fusion automatique pour {slug}',
        'Merge {slug} when its checks pass?':
            'Fusionner {slug} quand ses vérifications passent ?',
        'GitHub merges it as soon as every required check has passed. You can still cancel auto-merge on the pull request page.':
            'GitHub la fusionne dès que toutes les vérifications requises ont réussi. Vous pouvez encore annuler la fusion automatique sur la page de la pull request.',
        'Enable auto-merge':
            'Activer la fusion automatique',
        'Auto-Merge':
            'Fusion auto',
        'Merge pull request':
            'Fusionner la pull request',
        'Merge {slug} now':
            'Fusionner {slug} maintenant',
        'Merge {slug}?':
            'Fusionner {slug} ?',
        'Merge':
            'Fusionner',
        'Disable auto-merge':
            'Désactiver la fusion automatique',
        'Stop GitHub from merging {slug} when its checks pass':
            'Empêcher GitHub de fusionner {slug} quand ses vérifications passent',
        'Disable Auto-Merge':
            'Désactiver la fusion auto',
        'Its checks have passed. This merges the pull request on GitHub now.':
            'Ses vérifications ont réussi. Ceci fusionne la pull request sur GitHub maintenant.',
        "Its checks haven't all passed. This merges the pull request on GitHub now, if the repository lets it.":
            "Ses vérifications n'ont pas toutes réussi. Ceci fusionne la pull request sur GitHub maintenant, si le dépôt le permet.",
        'Merge and archive session':
            'Fusionner et archiver la session',
        'Merge {slug} now, then archive this session':
            'Fusionner {slug} maintenant, puis archiver cette session',
        'Merge {slug} and archive this session?':
            'Fusionner {slug} et archiver cette session ?',
        'The session is archived once the merge lands — you can bring it back with Undo, or from “Show archived”.':
            'La session est archivée une fois la fusion effectuée — vous pouvez la récupérer avec Annuler, ou depuis « Afficher les archivées ».',
        'Merge & archive':
            'Fusionner et archiver',
        'Mark ready & merge when checks pass':
            'Marquer prête et fusionner quand les vérifications passent',
        'Take {slug} out of draft, then turn on auto-merge':
            'Sortir {slug} du brouillon, puis activer la fusion automatique',
        'Mark {slug} ready and merge it when its checks pass?':
            'Marquer {slug} prête et la fusionner quand ses vérifications passent ?',
        'Ready & auto-merge':
            'Prête et fusion auto',
        'Ready & Auto-Merge':
            'Prête et fusion auto',
        'Mark ready & merge':
            'Marquer prête et fusionner',
        'Take {slug} out of draft, then merge it now':
            'Sortir {slug} du brouillon, puis la fusionner maintenant',
        'Mark {slug} ready and merge it?':
            'Marquer {slug} prête et la fusionner ?',
        'Ready & merge':
            'Prête et fusionner',
        'Ready & Merge':
            'Prête et fusionner',
        'Mark ready, merge & archive session':
            'Marquer prête, fusionner et archiver la session',
        'Take {slug} out of draft, merge it now, then archive this session':
            'Sortir {slug} du brouillon, la fusionner maintenant, puis archiver cette session',
        'Mark {slug} ready, merge it and archive this session?':
            'Marquer {slug} prête, la fusionner et archiver cette session ?',
        'Ready, merge & archive':
            'Prête, fusionner et archiver',
        'The pull request is marked ready for review first.':
            "La pull request est d'abord marquée prête pour relecture.",
        'Close pull request':
            'Fermer la pull request',
        'Close {slug} without merging':
            'Fermer {slug} sans fusionner',
        'Close {slug}?':
            'Fermer {slug} ?',
        'The pull request is closed without merging. Its branch and its comments stay, and it can be reopened on GitHub.':
            'La pull request est fermée sans fusion. Sa branche et ses commentaires restent, et elle peut être rouverte sur GitHub.',
        "{url} doesn't look like a pull request.":
            '{url} ne ressemble pas à une pull request.',
        "Collins doesn't know how to do that.":
            'Collins ne sait pas faire cela.',
        'Merge conflicts':
            'Conflits de fusion',
        'Refresh':
            'Actualiser',
        'Search settings…':
            'Rechercher des réglages…',
        'Search settings':
            'Rechercher des réglages',
        'No settings found':
            'Aucun réglage trouvé',
        'Try a different search.':
            'Essayez une autre recherche.',
        'Tab drag handles':
            'Poignées de glissement des onglets',
        'Drag any panel tab by its handle to move, reorder, or split it. Relies on GTK internals — turn off to fall back to plain tab dragging plus a drag grip on each panel':
            "Glissez n'importe quel onglet de panneau par sa poignée pour le déplacer, le réordonner ou le scinder. Repose sur des rouages internes de GTK — désactivez pour revenir au glissement d'onglet simple plus une prise de glissement sur chaque panneau",
        'Project icon size':
            'Taille des icônes de projet',
        'Size of the project and folder icons in the sidebar':
            'Taille des icônes de projet et de dossier dans la barre latérale',
        'Start new sessions in a git worktree':
            'Démarrer les nouvelles sessions dans un worktree git',
        "Git projects only; each new session works in its own fresh worktree, so it won't see uncommitted local changes. Right-click a project header to override per project":
            "Projets git seulement ; chaque nouvelle session travaille dans son propre worktree neuf, et ne voit donc pas les modifications locales non validées. Clic droit sur l'en-tête d'un projet pour l'ajuster par projet",
        "Follow Claude's own session names":
            'Suivre les noms de session de Claude',
        'Rename sessions whenever Claude names or renames them — /rename and its automatic titles; manually renamed sessions keep their name':
            'Renommer les sessions chaque fois que Claude les nomme ou renomme — /rename et ses titres automatiques ; les sessions renommées à la main gardent leur nom',
        'Exact busy tracking from the agent':
            "Suivi d'activité exact depuis l'agent",
        "Read Claude Code's own progress announcements for the sidebar's working indicator, instead of only inferring from terminal output (fully applies to newly opened tabs)":
            "Lire les annonces de progression de Claude Code pour l'indicateur de travail de la barre latérale, au lieu de seulement déduire depuis la sortie du terminal (pleinement appliqué aux onglets nouvellement ouverts)",
        'Poll for background sessions':
            'Sonder les sessions en arrière-plan',
        'Fallback: check the agent CLI every 20 seconds in case the yellow guide lines stop updating on their own':
            "Solution de repli : interroger la CLI de l'agent toutes les 20 secondes au cas où les lignes-guides jaunes cessent de se mettre à jour d'elles-mêmes",
        'Typing opens the composer':
            'Taper ouvre le rédacteur',
        "Start typing at an agent's empty prompt and the composer opens with what you typed. A dialog, a menu and the CLI's own /, !, # and @ keep their keys":
            "Commencez à taper au prompt vide d'un agent et le rédacteur s'ouvre avec ce que vous avez tapé. Une boîte de dialogue, un menu et les /, !, # et @ propres à la CLI gardent leurs touches",
        'Right-click aims spell-check':
            'Le clic droit vise la correction orthographique',
        'Right-clicking a misspelled word in the composer offers corrections for that word. Off: corrections follow the text cursor instead, and a right-click never moves it':
            'Un clic droit sur un mot mal orthographié dans le rédacteur propose des corrections pour ce mot. Désactivé : les corrections suivent le curseur de texte, et un clic droit ne le déplace jamais',
        'Max width':
            'Largeur maximale',
        'Stop growing past this width and center in the tab instead (0 = no limit)':
            "Cesser de grandir au-delà de cette largeur et se centrer dans l'onglet (0 = sans limite)",
        'Footer apps':
            'Applications du pied de page',
        "Buttons in each tab's footer that open the tab's directory":
            "Boutons dans le pied de page de chaque onglet qui ouvrent le répertoire de l'onglet",
        'Add application…':
            'Ajouter une application…',
        'Pull requests':
            'Pull requests',
        'Text size':
            'Taille du texte',
        'Reading-text size in the pull request panel, as a percentage of the app font; buttons and menus keep the app size':
            "Taille du texte de lecture dans le panneau de pull request, en pourcentage de la police de l'application ; boutons et menus gardent la taille de l'application",
        'Show embedded images':
            'Afficher les images intégrées',
        'Render the images a description or comment embeds, and the changed image files, as pictures; click one to open it full size. Off, they stay links and patches, and opening a pull request downloads nothing':
            "Afficher comme images celles qu'une description ou un commentaire intègre, ainsi que les fichiers d'image modifiés ; cliquez sur l'une pour l'ouvrir en taille réelle. Désactivé, elles restent des liens et des correctifs, et ouvrir une pull request ne télécharge rien",
        'Confirm before merging':
            'Confirmer avant de fusionner',
        'Ask before merging a pull request, enabling auto-merge, or merging and archiving the session. Off, the click merges; closing a pull request unmerged still asks either way':
            "Demander avant de fusionner une pull request, d'activer la fusion automatique, ou de fusionner et archiver la session. Désactivé, le clic fusionne ; fermer une pull request sans la fusionner demande toujours confirmation",
        'Attach pull requests named in prompts':
            'Attacher les pull requests nommées dans les prompts',
        "Put every pull request a new session's first prompt mentions on that session's row, without waiting for the agent to touch it":
            "Placer chaque pull request mentionnée par le premier prompt d'une nouvelle session sur la ligne de cette session, sans attendre que l'agent y touche",
        'Rename sessions after their pull requests':
            "Renommer les sessions d'après leurs pull requests",
        'Retitle a session to match the newest pull request opened in it; manually renamed sessions keep their name':
            "Retitrer une session d'après la plus récente pull request qui y est ouverte ; les sessions renommées à la main gardent leur nom",
        'Refresh pull requests at launch':
            'Actualiser les pull requests au lancement',
        "Ask GitHub about every listed session's pull requests once on startup, so the marks in the sidebar start out current rather than as they were left":
            "Interroger GitHub une fois au démarrage sur les pull requests de chaque session listée, pour que les marques de la barre latérale démarrent à jour plutôt que telles qu'elles ont été laissées",
        'Caffeine Mode':
            'Caffeine Mode',
        'Keep screen on':
            "Garder l'écran allumé",
        'Hold the screen on as well as keeping the computer awake. Off lets the screen turn off as usual, while an unattended agent still keeps the computer from sleeping':
            "Maintenir l'écran allumé en plus de garder l'ordinateur éveillé. Désactivé, l'écran s'éteint comme d'habitude, tandis qu'un agent sans surveillance empêche toujours l'ordinateur de dormir",
        'Until idle grace period':
            "Délai de grâce de Jusqu'à inactivité",
        'How many minutes Until idle keeps the computer awake after the last session stops working; any session picking work back up restarts the wait':
            "Combien de minutes Jusqu'à inactivité garde l'ordinateur éveillé après que la dernière session a cessé de travailler ; toute session qui reprend le travail relance l'attente",
        'Turn on at launch':
            'Activer au lancement',
        'Start with Caffeine Mode already on, keeping the computer awake until you turn it off from the header':
            "Démarrer avec Caffeine Mode déjà activé, gardant l'ordinateur éveillé jusqu'à ce que vous le désactiviez depuis l'en-tête",
        'Turn off after':
            'Désactiver après',
        'Open in a window on small screens':
            'Ouvrir dans une fenêtre sur petits écrans',
        'On screens this many pixels wide or narrower (after display scaling), the editor opens in its own window instead of a panel (0 = always open as a panel)':
            "Sur les écrans de cette largeur en pixels ou moins (après mise à l'échelle de l'affichage), l'éditeur s'ouvre dans sa propre fenêtre au lieu d'un panneau (0 = toujours ouvrir en panneau)",
        'Show status icon':
            "Afficher l'icône d'état",
        'Shows Collins in the top bar, with a menu that jumps to any open session':
            "Affiche Collins dans la barre supérieure, avec un menu qui saute vers n'importe quelle session ouverte",
        'No status-icon support was found in this desktop — GNOME needs an AppIndicator extension':
            "Aucune prise en charge d'icône d'état trouvée dans ce bureau — GNOME nécessite une extension AppIndicator",
        'Nothing on this desktop can show a status icon':
            "Rien sur ce bureau ne peut afficher une icône d'état",
        'Using the claude found on PATH at {path}.':
            'Utilise le claude trouvé dans le PATH à {path}.',
        "claude isn't on PATH — Collins will ask where it is at the next launch.":
            "claude n'est pas dans le PATH — Collins demandera où il est au prochain lancement.",
        'How long that launch-time Caffeine Mode runs before it turns itself off. Until idle never does: it holds the computer awake while any session is working (and {n} minute past), dozing in between':
            "Durée pendant laquelle ce Caffeine Mode de lancement tourne avant de se désactiver tout seul. Jusqu'à inactivité ne le fait jamais : il garde l'ordinateur éveillé tant qu'une session travaille (et {n} minute au-delà), somnolant entre-temps",
        'Move up':
            'Monter',
        'Move down':
            'Descendre',
        'No apps configured':
            'Aucune application configurée',
        'Before':
            'Avant',
        'After':
            'Après',
        'Back to the pull requests':
            'Retour aux pull requests',
        'View in Collins':
            'Voir dans Collins',
        "Open this pull request's page beside the session":
            'Ouvrir la page de cette pull request à côté de la session',
        'View unresolved comments':
            'Voir les commentaires non résolus',
        "Open this pull request's page at its first unresolved thread":
            'Ouvrir la page de cette pull request à son premier fil non résolu',
        "Collins couldn't run that action.":
            "Collins n'a pas pu exécuter cette action.",
        '{action} failed':
            'Échec de {action}',
        'Pull request':
            'Pull request',
        'Merging when checks pass':
            'Fusion quand les vérifications passent',
        "The GitHub CLI (gh) isn't installed, or isn't on PATH.":
            "La CLI GitHub (gh) n'est pas installée, ou n'est pas dans le PATH.",
        "Collins couldn't run gh.":
            "Collins n'a pas pu exécuter gh.",
        'gh exited with status {code}.':
            "gh s'est terminé avec le statut {code}.",
        'and {n} more':
            'et {n} de plus',
        'Approved':
            'Approuvée',
        'Changes requested':
            'Modifications demandées',
        'Review dismissed':
            'Relecture rejetée',
        'Commented':
            'Commenté',
        'Reload this pull request':
            'Recharger cette pull request',
        'Conversation':
            'Conversation',
        'Files':
            'Fichiers',
        "Couldn't load this pull request — is the GitHub CLI signed in?":
            'Impossible de charger cette pull request — la CLI GitHub est-elle connectée ?',
        'Nothing loaded yet.':
            "Rien de chargé pour l'instant.",
        'Merges {head} into {base}':
            'Fusionne {head} dans {base}',
        '{n} file':
            '{n} fichier',
        'No comments yet.':
            'Pas encore de commentaires.',
        'No description provided.':
            'Aucune description fournie.',
        'No changed files.':
            'Aucun fichier modifié.',
        'Checks ({n})':
            'Vérifications ({n})',
        'Checks':
            'Vérifications',
        'More actions':
            "Plus d'actions",
        'Right-click for more actions':
            "Clic droit pour plus d'actions",
        'Add a comment':
            'Ajouter un commentaire',
        'Request changes':
            'Demander des modifications',
        'Approve':
            'Approuver',
        'Comment':
            'Commenter',
        'Comment on {slug}':
            'Commenter sur {slug}',
        'Approve {slug}':
            'Approuver {slug}',
        'Request changes on {slug}':
            'Demander des modifications sur {slug}',
        'Address comments':
            'Traiter les commentaires',
        'Request review':
            'Demander une relecture',
        'Outdated':
            'Obsolète',
        'The code this thread commented on has changed':
            'Le code commenté par ce fil a changé',
        'Resolved':
            'Résolu',
        'Reply':
            'Répondre',
        'Reply in this thread':
            'Répondre dans ce fil',
        'Unresolve':
            'Rouvrir',
        'Resolve':
            'Résoudre',
        'Reopen this thread':
            'Rouvrir ce fil',
        'Mark this thread resolved':
            'Marquer ce fil comme résolu',
        'Post reply':
            'Publier la réponse',
        'no diff — binary or too large':
            'pas de diff — binaire ou trop volumineux',
        '{n} line':
            '{n} ligne',
        'Show more':
            'Afficher plus',
        'Show less':
            'Afficher moins',
        'New session in {path}':
            'Nouvelle session dans {path}',
        'Expand':
            'Développer',
        'Collapse':
            'Réduire',
        'New Thread':
            'Nouveau fil',
        'Discard draft and close tab':
            "Abandonner le brouillon et fermer l'onglet",
        'Discard draft':
            'Abandonner le brouillon',
        'Backgrounding is unavailable until this session is registered and any handoff in progress finishes':
            "Le passage en arrière-plan est indisponible tant que cette session n'est pas enregistrée et qu'un transfert en cours n'est pas terminé",
        'This session has no tab open.':
            "Cette session n'a aucun onglet ouvert.",
        'Delete archived sessions…':
            'Supprimer les sessions archivées…',
        'Add project':
            'Ajouter un projet',
        'Refresh session list and pull requests':
            'Actualiser la liste des sessions et les pull requests',
        'Chats':
            'Discussions',
        'Draft':
            'Brouillon',
        'Refreshing pull requests…':
            'Actualisation des pull requests…',
        'Open in new window':
            'Ouvrir dans une nouvelle fenêtre',
        'Move to new window':
            'Déplacer vers une nouvelle fenêtre',
        'Rename to match PR':
            "Renommer d'après la PR",
        'Rename to match PR #{number}':
            "Renommer d'après la PR #{number}",
        'Repair session link':
            'Réparer le lien de session',
        'New session in new window':
            'Nouvelle session dans une nouvelle fenêtre',
        'New session here (no worktree)':
            'Nouvelle session ici (sans worktree)',
        'New session here (in a worktree)':
            'Nouvelle session ici (dans un worktree)',
        'New sessions use a worktree':
            'Les nouvelles sessions utilisent un worktree',
        'Git pull ({branch})':
            'Git pull ({branch})',
        'Git pull':
            'Git pull',
        'Remove project from sidebar':
            'Retirer le projet de la barre latérale',
        'replaced':
            'remplacée',
        'Open composer':
            'Ouvrir le rédacteur',
        "the agent didn't start — your prompt is kept in the composer":
            "l'agent n'a pas démarré — votre prompt est conservé dans le rédacteur",
        'Every pull request this session has opened':
            'Chaque pull request que cette session a ouverte',
        'Show/hide terminal panel':
            'Afficher/masquer le panneau de terminal',
        'Move terminals to {name}?':
            'Déplacer les terminaux vers {name} ?',
        'This session started in a worktree at {path}. The terminal open beside it is still in the project directory — change its directory to the worktree? A terminal running a command is left alone.':
            'Cette session a démarré dans un worktree à {path}. Le terminal ouvert à côté est encore dans le répertoire du projet — changer son répertoire vers le worktree ? Un terminal exécutant une commande est laissé tel quel.',
        'Change Directory':
            'Changer de répertoire',
        '{n} terminal is running a command and stayed where it was':
            '{n} terminal exécute une commande et est resté où il était',
        'Effort: {level}':
            'Effort : {level}',
        'Click to switch the effort level':
            "Cliquer pour changer le niveau d'effort",
        'No pull request found for this branch':
            'Aucune pull request trouvée pour cette branche',
        "Re-check this branch's pull requests":
            'Revérifier les pull requests de cette branche',
        "Look for this branch's pull request":
            'Chercher la pull request de cette branche',
        "Add to chat: the agent isn't running in this tab":
            "Ajouter à la discussion : l'agent ne s'exécute pas dans cet onglet",
        "Add to chat isn't available for this file":
            "Ajouter à la discussion n'est pas disponible pour ce fichier",
        "skipped {n} dropped item that isn't a local file":
            "{n} élément déposé ignoré qui n'est pas un fichier local",
        "Composer: the input box holds a paste Collins can't read":
            'Rédacteur : la zone de saisie contient un collage que Collins ne peut pas lire',
        "Effort switch: the agent isn't running in this tab":
            "Changement d'effort : l'agent ne s'exécute pas dans cet onglet",
        "This session isn't at an empty prompt.":
            "Cette session n'est pas à un prompt vide.",
        'Start sessions in the background':
            'Démarrer des sessions en arrière-plan',
        'start_session — spawn a sibling agent in a new background tab, with a prompt':
            "start_session — lance un agent frère dans un nouvel onglet d'arrière-plan, avec un prompt",
        'Read the terminal panel':
            'Lire le panneau de terminal',
        "read_terminal — the panel tabs' text and scrollback, your own typing included":
            "read_terminal — le texte et l'historique des onglets du panneau, votre propre saisie incluse",
        'Run commands in the terminal panel':
            'Exécuter des commandes dans le panneau de terminal',
        'run_in_terminal — type a command into an idle panel tab (or a new one) and run it':
            "run_in_terminal — tape une commande dans un onglet de panneau inactif (ou un nouveau) et l'exécute",
        'Default (latest Haiku)':
            'Par défaut (dernier Haiku)',
        'Default (latest Sonnet)':
            'Par défaut (dernier Sonnet)',
        'Session title model':
            'Modèle des titres de session',
        'Icon generation model':
            "Modèle de génération d'icônes",
        'Model list':
            'Liste des modèles',
        'Checking…':
            'Vérification…',
        'Ask Anthropic for the model list now, rather than waiting for the saved one to age out':
            "Demander la liste des modèles à Anthropic maintenant, plutôt que d'attendre que la liste enregistrée expire",
        "Couldn't reach Anthropic — offering the CLI's aliases (opus, sonnet, haiku)":
            'Impossible de joindre Anthropic — propose les alias de la CLI (opus, sonnet, haiku)',
        "Couldn't reach Anthropic — still showing the list fetched {when}":
            'Impossible de joindre Anthropic — affiche encore la liste récupérée {when}',
        '{count} models, updated {when}':
            '{count} modèles, mise à jour {when}',
        'Waiting for this session to be registered — backgrounding it now would leave the agent with no way back to it':
            "En attente de l'enregistrement de cette session — la passer en arrière-plan maintenant laisserait l'agent sans moyen d'y revenir",
        'Another session is still being handed to the background — one at a time':
            'Une autre session est encore en cours de passage en arrière-plan — une à la fois',
        'New chat (scratch folder)':
            'Nouvelle discussion (dossier temporaire)',
        'Close window with {n} active session(s)?':
            'Fermer la fenêtre avec {n} session(s) active(s) ?',
        'Agents are asked to exit cleanly first; other running commands will be terminated.':
            "Les agents sont d'abord invités à quitter proprement ; les autres commandes en cours seront interrompues.",
        'Backgrounding sessions…':
            'Passage des sessions en arrière-plan…',
        'Quit Now':
            'Quitter maintenant',
        'Handing each session to a background agent, one at a time, so every one is paired with the agent it becomes. {done} of {total} done.':
            "Chaque session est confiée à un agent d'arrière-plan, une à la fois, pour que chacune soit appariée à l'agent qu'elle devient. {done} sur {total} faites.",
        'New session in {project} (no worktree)':
            'Nouvelle session dans {project} (sans worktree)',
        'New session in {project} (in a worktree)':
            'Nouvelle session dans {project} (dans un worktree)',
        'Could not create chat directory':
            'Impossible de créer le répertoire de discussion',
        'Trust and add':
            'Faire confiance et ajouter',
        'Discard draft?':
            'Abandonner le brouillon ?',
        '“{label}” will be forgotten, along with any terminal panel it kept.':
            "« {label} » sera oublié, ainsi que tout panneau de terminal qu'il conservait.",
        'Discard':
            'Abandonner',
        "Couldn't send that to the session":
            "Impossible d'envoyer cela à la session",
        'Close tab with an active session?':
            "Fermer l'onglet avec une session active ?",
        "The agent is asked to exit cleanly first; the command running in this tab's terminal panel will be terminated.":
            "L'agent est d'abord invité à quitter proprement ; la commande en cours dans le panneau de terminal de cet onglet sera interrompue.",
        'The agent is asked to exit cleanly first.':
            "L'agent est d'abord invité à quitter proprement.",
        "A command is still running in this tab's terminal panel and will be terminated.":
            "Une commande est encore en cours d'exécution dans le panneau de terminal de cet onglet et sera interrompue.",
        "Backgrounding isn't available yet: this session hasn't been registered, so a detached agent would have no way back to it.":
            "Le passage en arrière-plan n'est pas encore disponible : cette session n'a pas été enregistrée, donc un agent détaché n'aurait aucun moyen d'y revenir.",
        "Backgrounding isn't available right now: another session is still being handed to the background.":
            "Le passage en arrière-plan n'est pas disponible pour le moment : une autre session est encore en cours de passage en arrière-plan.",
        'No matching agent':
            'Aucun agent correspondant',
        'No background agent matches this session — either its agent is gone, or more than one candidate matched and guessing would link the wrong one. The transcript itself is intact.':
            "Aucun agent d'arrière-plan ne correspond à cette session — soit son agent a disparu, soit plusieurs candidats correspondaient et deviner lierait le mauvais. La transcription elle-même est intacte.",
        'Session linked':
            'Session liée',
        'Linked to its running background agent.':
            "Liée à son agent d'arrière-plan en cours d'exécution.",
        'Nothing to repair':
            'Rien à réparer',
        'This session is already its own background agent. Opening it attaches to that agent.':
            "Cette session est déjà son propre agent d'arrière-plan. L'ouvrir se rattache à cet agent.",
        'No pull request is linked to this session yet':
            "Aucune pull request n'est encore liée à cette session",
        'Undo':
            'Annuler',
        'Archived “{name}”':
            '« {name} » archivée',
        'Archived {n} sessions':
            '{n} sessions archivées',
        'Delete {n} archived session(s)?':
            'Supprimer {n} session(s) archivée(s) ?',
        'Keep the {p} emptied project(s) in the sidebar':
            'Garder les {p} projet(s) vidé(s) dans la barre latérale',
        'Manage and resume your AI coding agent sessions.\n\nUnofficial community tool — not affiliated with or endorsed by Anthropic.':
            "Gérez et reprenez vos sessions d'agent de codage IA.\n\nOutil communautaire non officiel — sans affiliation ni approbation d'Anthropic.",
    },
}

_HEADER = (
    "# Modified from the original agent-session-manager\n"
    "# (https://github.com/r4nd3l/agent-session-manager, GPL-3.0) in the ghackett\n"
    "# fork. Last modified: 2026-08-30. Full change history: git log for this file.\n"
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
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


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
