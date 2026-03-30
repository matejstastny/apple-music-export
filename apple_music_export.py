#!/usr/bin/env python3

"""
Apple Music Library Export/Import Tool

Exports your entire Apple Music library (all songs + playlists) to JSON,
and re-imports them after a region switch or subscription change.

Requirements:
  - macOS with Music.app
  - Python 3.7+
  - Grant automation access when macOS prompts you

Usage:
  python3 apple_music_export.py export [-o backup.json]
  python3 apple_music_export.py import [-i backup.json] [--dry-run]
  python3 apple_music_export.py import [-i backup.json] --playlists-only

Recommended workflow:
  1. Run 'export' BEFORE cancelling your subscription
  2. Switch region, re-subscribe to Apple Music
  3. Manually re-add songs (use the generated .txt checklist)
  4. Run 'import --playlists-only' to recreate all playlists
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TMP_FILE = os.path.join(tempfile.gettempdir(), "am_export_tmp.json")


# ── colors & icons ───────────────────────────────────────────────────────

NO_COLOR = not sys.stdout.isatty() or os.environ.get("NO_COLOR")


def _c(code: str, text: str) -> str:
    return text if NO_COLOR else f"\033[{code}m{text}\033[0m"


def bold(t: str) -> str:    return _c("1", t)
def dim(t: str) -> str:     return _c("2", t)
def red(t: str) -> str:     return _c("31", t)
def green(t: str) -> str:   return _c("32", t)
def yellow(t: str) -> str:  return _c("33", t)
def blue(t: str) -> str:    return _c("34", t)
def magenta(t: str) -> str: return _c("35", t)
def cyan(t: str) -> str:    return _c("36", t)


def bold_green(t: str) -> str:   return _c("1;32", t)
def bold_blue(t: str) -> str:    return _c("1;34", t)
def bold_cyan(t: str) -> str:    return _c("1;36", t)
def bold_yellow(t: str) -> str:  return _c("1;33", t)
def bold_red(t: str) -> str:     return _c("1;31", t)
def bold_magenta(t: str) -> str: return _c("1;35", t)


# Nerd Font icons
ICON_MUSIC     = "\uf001"   #  music note
ICON_PLAYLIST  = "\uf0cb"   #  list
ICON_CHECK     = "\uf00c"   #  checkmark
ICON_CROSS     = "\uf00d"   #  cross
ICON_WARN      = "\uf071"   #  warning triangle
ICON_HEART     = "\uf004"   #  heart
ICON_APPLE     = "\uf179"   #  apple logo
ICON_FILE      = "\uf15b"   #  file
ICON_FOLDER    = "\uf07b"   #  folder
ICON_SEARCH    = "\uf002"   #  search
ICON_DOWNLOAD  = "\uf019"   #  download
ICON_UPLOAD    = "\uf093"   #  upload
ICON_ARROW     = "\uf061"   #  arrow right
ICON_COG       = "\uf013"   #  gear/cog
ICON_DISC      = "\uf51f"   #  compact disc
ICON_BOLT      = "\uf0e7"   #  lightning bolt
ICON_INFO      = "\uf05a"   #  info circle


class log:
    """Formatted terminal output with Nerd Font icons and ANSI colors."""

    @staticmethod
    def _line(icon: str, color_fn, msg: str):
        print(f" {color_fn(icon)}  {msg}")

    @staticmethod
    def banner():
        title = f"  {ICON_APPLE} Apple Music Export/Import Tool"
        width = 47
        padding = max(0, width - len(title))
        box_top = dim("╭" + "─" * width + "╮")
        box_mid = dim("│") + bold_magenta(title + " " * padding) + dim("│")
        box_bot = dim("╰" + "─" * width + "╯")
        print(f"\n{box_top}\n{box_mid}\n{box_bot}")

    @staticmethod
    def section(title: str):
        print(f"\n {bold_cyan(ICON_BOLT)}  {bold(title)}")

    @staticmethod
    def info(msg: str):
        log._line(ICON_INFO, cyan, msg)

    @staticmethod
    def success(msg: str):
        log._line(ICON_CHECK, green, msg)

    @staticmethod
    def warn(msg: str):
        log._line(ICON_WARN, yellow, msg)

    @staticmethod
    def error(msg: str):
        log._line(ICON_CROSS, red, msg)

    @staticmethod
    def file(msg: str):
        log._line(ICON_FILE, blue, msg)

    @staticmethod
    def music(msg: str):
        log._line(ICON_MUSIC, green, msg)

    @staticmethod
    def playlist(msg: str):
        log._line(ICON_PLAYLIST, green, msg)

    @staticmethod
    def done(msg: str):
        log._line(ICON_CHECK, bold_green, msg)

    @staticmethod
    def step(msg: str):
        print(f"  {cyan(ICON_ARROW)}  {msg}")

    @staticmethod
    def write(msg: str):
        sys.stdout.write(msg)
        sys.stdout.flush()

    @staticmethod
    def blank():
        print()

    @staticmethod
    def summary(lines: list[tuple[str, str, str]]):
        """Print a summary box. Each line is (icon, color_name, text)."""
        color_map = {
            "green": bold_green, "blue": bold_blue, "cyan": bold_cyan,
            "yellow": bold_yellow, "red": bold_red, "magenta": bold_magenta,
        }
        width = 45
        top_right_dashes = width - 11
        print(f"\n{dim('╭──')} {bold('Summary')} {dim('─' * top_right_dashes + '╮')}")
        for icon, color_name, text in lines:
            c = color_map.get(color_name, bold_cyan)
            content = f"  {c(icon)}  {text}"
            visible = len(icon) + 2 + len(text) + 2
            pad = width - visible
            print(f"{dim('│')}{content}{' ' * max(pad, 1)}{dim('│')}")
        print(f"{dim('╰' + '─' * width + '╯')}")


# ── helpers ──────────────────────────────────────────────────────────────


def jxa(script: str, timeout: int = 600) -> str:
    """Run a JXA (JavaScript for Automation) script via osascript."""
    r = subprocess.run(
        ["osascript", "-l", "JavaScript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"JXA error: {r.stderr.strip()}")
    return r.stdout.strip()


def osascript(script: str, timeout: int = 300) -> str:
    """Run an AppleScript via osascript."""
    r = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"AppleScript error: {r.stderr.strip()}")
    return r.stdout.strip()


def ensure_music_running():
    """Make sure Music.app is running."""
    log.info("Launching Music app ...")
    jxa('Application("Music").activate(); delay(2);')
    log.success("Music app is ready.")
    log.blank()


def escape_as(s: str) -> str:
    """Escape a string for embedding in an AppleScript double-quoted string."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ── export ───────────────────────────────────────────────────────────────


def export_library(output: str):
    log.banner()
    ensure_music_running()

    # ── tracks (batch property fetch — fast even for 10k+ libraries) ──
    log.section("Exporting tracks")
    track_script = f'''
    ObjC.import('Foundation');
    const Music = Application('Music');
    const lib = Music.libraryPlaylists[0];
    const t = lib.tracks;

    // Batch-fetch every property in one Apple Event each (very fast)
    const names        = t.name();
    const artists      = t.artist();
    const albums       = t.album();
    const albumArtists = t.albumArtist();
    const trackNums    = t.trackNumber();
    const discNums     = t.discNumber();
    const genres       = t.genre();
    const years        = t.year();
    const durations    = t.duration();
    const ratings      = t.rating();
    const playCounts   = t.playedCount();
    const kinds        = t.kind();

    // loved() can fail on older macOS — wrap it
    let lovedArr;
    try {{ lovedArr = t.loved(); }} catch(e) {{ lovedArr = new Array(names.length).fill(false); }}

    const n = names.length;
    const tracks = new Array(n);
    for (let i = 0; i < n; i++) {{
        tracks[i] = {{
            name:          names[i]        || "",
            artist:        artists[i]      || "",
            album:         albums[i]       || "",
            album_artist:  albumArtists[i] || "",
            track_number:  trackNums[i]    || 0,
            disc_number:   discNums[i]     || 0,
            genre:         genres[i]       || "",
            year:          years[i]        || 0,
            duration:      durations[i]    || 0,
            loved:         !!lovedArr[i],
            rating:        ratings[i]      || 0,
            play_count:    playCounts[i]   || 0,
            kind:          kinds[i]        || "",
        }};
    }}

    const s = $.NSString.alloc.initWithUTF8String(JSON.stringify(tracks));
    s.writeToFileAtomicallyEncodingError("{TMP_FILE}", true, $.NSUTF8StringEncoding, null);
    n;
    '''

    jxa(track_script)
    with open(TMP_FILE, encoding="utf-8") as f:
        tracks = json.load(f)
    log.music(f"{len(tracks):,} tracks exported")

    # ── playlists ────────────────────────────────────────────────────
    log.section("Exporting playlists")
    playlist_script = f'''
    ObjC.import('Foundation');
    const Music = Application('Music');
    const pls = Music.userPlaylists();
    const out = [];

    for (const p of pls) {{
        // Skip smart/auto-generated playlists
        try {{ if (p.smart()) continue; }} catch(e) {{}}

        let name;
        try {{ name = p.name(); }} catch(e) {{ continue; }}

        let names = [], artists = [], albums = [];
        try {{
            const pt = p.tracks;
            names   = pt.name();
            artists = pt.artist();
            albums  = pt.album();
        }} catch(e) {{}}

        const tracks = [];
        for (let i = 0; i < names.length; i++) {{
            tracks.push({{
                name:   names[i]   || "",
                artist: artists[i] || "",
                album:  albums[i]  || "",
            }});
        }}
        out.push({{ name: name, track_count: tracks.length, tracks: tracks }});
    }}

    const s = $.NSString.alloc.initWithUTF8String(JSON.stringify(out));
    s.writeToFileAtomicallyEncodingError("{TMP_FILE}", true, $.NSUTF8StringEncoding, null);
    out.length;
    '''

    jxa(playlist_script)
    with open(TMP_FILE, encoding="utf-8") as f:
        playlists = json.load(f)
    log.playlist(f"{len(playlists):,} playlists exported")

    # ── assemble and save ────────────────────────────────────────────
    library = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "track_count": len(tracks),
        "playlist_count": len(playlists),
        "tracks": tracks,
        "playlists": playlists,
    }

    with open(output, "w", encoding="utf-8") as f:
        json.dump(library, f, indent=2, ensure_ascii=False)

    # ── human-readable checklist ─────────────────────────────────────
    checklist = Path(output).with_suffix(".txt")
    with open(checklist, "w", encoding="utf-8") as f:
        f.write(f"Apple Music Library Export  —  {library['exported_at']}\n")
        f.write("=" * 60 + "\n\n")

        f.write(f"ALL SONGS ({len(tracks)})\n")
        f.write("-" * 40 + "\n")
        sorted_tracks = sorted(
            tracks,
            key=lambda x: (x["artist"].lower(), x["album"].lower(),
                           x["disc_number"], x["track_number"]),
        )
        current_artist = None
        for t in sorted_tracks:
            if t["artist"] != current_artist:
                current_artist = t["artist"]
                f.write(f"\n  {current_artist}\n")
            loved = "  [loved]" if t["loved"] else ""
            f.write(f"    {t['name']}  —  {t['album']}{loved}\n")

        f.write(f"\n\nPLAYLISTS ({len(playlists)})\n")
        f.write("-" * 40 + "\n")
        for p in playlists:
            f.write(f"\n  >> {p['name']}  ({p['track_count']} tracks)\n")
            for t in p["tracks"]:
                f.write(f"      {t['artist']}  —  {t['name']}\n")

    # ── detect local files ───────────────────────────────────────────
    local_files = [
        t for t in tracks
        if t["kind"] and "apple music" not in t["kind"].lower()
        and "purchased" not in t["kind"].lower()
    ]

    # ── saved files ──────────────────────────────────────────────────
    log.section("Saved files")
    log.file(bold(output))
    log.file(bold(str(checklist)))

    if local_files:
        local_path = Path(output).with_suffix(".local_files.txt")
        with open(local_path, "w", encoding="utf-8") as f:
            f.write("Local files (not from Apple Music streaming) — back these up!\n")
            f.write("=" * 60 + "\n\n")
            for t in local_files:
                f.write(f"  {t['artist']} — {t['name']}  [{t['album']}]  ({t['kind']})\n")
        log.file(bold(str(local_path)))

    # ── summary ──────────────────────────────────────────────────────
    summary_lines = [
        (ICON_MUSIC, "magenta", f"{len(tracks):,} tracks"),
        (ICON_PLAYLIST, "cyan", f"{len(playlists):,} playlists"),
    ]
    if local_files:
        summary_lines.append(
            (ICON_WARN, "yellow", f"{len(local_files):,} local files (back up manually!)")
        )
    loved_count = sum(1 for t in tracks if t["loved"])
    if loved_count:
        summary_lines.append((ICON_HEART, "red", f"{loved_count:,} loved tracks"))
    log.summary(summary_lines)

    if local_files:
        log.blank()
        log.warn("Local files can't be restored from Apple Music.")
        log.warn(f"Back up {bold('~/Music/Music/Media/')} separately.")

    # cleanup
    try:
        os.unlink(TMP_FILE)
    except OSError:
        pass

    log.blank()
    log.done(bold_green("Export complete!"))
    log.blank()


# ── import ───────────────────────────────────────────────────────────────


def import_library(
    input_path: str,
    dry_run: bool = False,
    playlists_only: bool = False,
    songs_only: bool = False,
):
    log.banner()

    with open(input_path, "r", encoding="utf-8") as f:
        library = json.load(f)

    log.file(f"Loaded backup: {bold(f'{library["track_count"]:,}')} tracks, "
             f"{bold(f'{library["playlist_count"]:,}')} playlists "
             f"{dim(f'(exported {library["exported_at"]})')}")
    log.blank()

    ensure_music_running()

    if dry_run:
        log.info(bold_yellow("DRY RUN") + " — no changes will be made")
        log.blank()

    not_found = []

    # ── check which songs are missing from current library ───────────
    if not playlists_only:
        log.section("Scanning library")
        scan_script = '''
        ObjC.import('Foundation');
        const Music = Application('Music');
        let lib;
        try { lib = Music.libraryPlaylists[0]; } catch(e) { "0"; }
        const names = lib.tracks.name();
        const artists = lib.tracks.artist();
        const pairs = [];
        for (let i = 0; i < names.length; i++) {
            pairs.push((names[i] || "") + "\\t" + (artists[i] || ""));
        }
        pairs.join("\\n");
        '''
        raw = jxa(scan_script)
        current = set()
        for line in raw.split("\n"):
            if "\t" in line:
                current.add(line.strip().lower())

        log.info(f"Current library: {bold(f'{len(current):,}')} tracks")

        missing = []
        found = 0
        for t in library["tracks"]:
            key = f"{t['name']}\t{t['artist']}".lower()
            if key in current:
                found += 1
            else:
                missing.append(t)

        log.success(f"{found:,} tracks already in library")
        if missing:
            log.error(f"{bold_red(f'{len(missing):,}')} tracks are {bold_red('MISSING')}")
            not_found = missing
        else:
            log.done(bold_green("All tracks accounted for!"))

    # ── recreate playlists ───────────────────────────────────────────
    if not songs_only:
        log.section("Recreating playlists")

        for pl in library["playlists"]:
            pname = pl["name"]
            ptracks = pl["tracks"]
            log.write(f" {magenta(ICON_PLAYLIST)}  {bold(pname)} {dim(f'({len(ptracks)} tracks)')} ")

            if dry_run:
                print(yellow("skipped"))
                continue

            safe = escape_as(pname)

            # Create playlist if needed
            try:
                osascript(f'''
                    tell application "Music"
                        try
                            get user playlist "{safe}"
                        on error
                            make new user playlist with properties {{name:"{safe}"}}
                        end try
                    end tell
                ''')
            except Exception as e:
                print(red(f"{ICON_CROSS} ERROR: {e}"))
                continue

            # Add matching tracks from current library into the playlist
            added = 0
            errors = 0
            for i, t in enumerate(ptracks):
                tname = escape_as(t["name"])
                tartist = escape_as(t["artist"])

                script = f'''
                    tell application "Music"
                        try
                            set results to (search library playlist 1 for "{tname}" only songs)
                            repeat with r in results
                                if name of r is "{tname}" and artist of r is "{tartist}" then
                                    duplicate r to user playlist "{safe}"
                                    return "OK"
                                end if
                            end repeat
                            return "MISS"
                        on error errMsg
                            return "ERR"
                        end try
                    end tell
                '''

                try:
                    result = osascript(script, timeout=30)
                    if result == "OK":
                        added += 1
                except Exception:
                    errors += 1

                # Progress dot every 25 tracks
                if (i + 1) % 25 == 0:
                    log.write(".")

            ratio = f"{added}/{len(ptracks)}"
            if added == len(ptracks):
                print(green(f"{ICON_CHECK} {ratio}"))
            elif added > 0:
                err_str = f"  {dim(f'({errors} errors)')}" if errors else ""
                print(yellow(f"{ICON_WARN} {ratio}") + err_str)
            else:
                print(red(f"{ICON_CROSS} {ratio}"))

    # ── save missing-tracks report ───────────────────────────────────
    if not_found:
        stem = Path(input_path).stem

        missing_json = f"{stem}_missing.json"
        with open(missing_json, "w", encoding="utf-8") as f:
            json.dump(not_found, f, indent=2, ensure_ascii=False)

        missing_txt = f"{stem}_missing.txt"
        with open(missing_txt, "w", encoding="utf-8") as f:
            f.write("Songs to manually re-add to Apple Music\n")
            f.write("=" * 50 + "\n")
            f.write("Search for each in Music app and click '+' to add.\n\n")
            sorted_missing = sorted(not_found, key=lambda x: (x["artist"].lower(), x["album"].lower()))
            current_artist = None
            for t in sorted_missing:
                if t["artist"] != current_artist:
                    current_artist = t["artist"]
                    f.write(f"\n{current_artist}\n")
                f.write(f"  {t['name']}  —  {t['album']}\n")

        log.section("Missing tracks")
        log.warn(f"{bold_yellow(f'{len(not_found):,}')} tracks need to be re-added manually")
        log.file(f"Checklist:  {bold(missing_txt)}")
        log.file(f"JSON:       {bold(missing_json)}")

        log.blank()
        print(bold(" Next steps:"))
        log.step(" Open Music app with an active Apple Music subscription")
        log.step(" Search for each song in the checklist and click '+' to add")
        log.step(f" Re-run: {bold_cyan('python3 apple_music_export.py import --playlists-only')}")

    log.blank()
    log.done(bold_green("Import complete!"))
    log.blank()


# ── CLI ──────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Export & import your Apple Music library",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Recommended workflow:
  1. BEFORE cancelling subscription:
       python3 apple_music_export.py export

  2. Switch region, re-subscribe to Apple Music

  3. Manually re-add songs using the .txt checklist as reference

  4. Recreate all playlists automatically:
       python3 apple_music_export.py import --playlists-only
""",
    )
    sub = parser.add_subparsers(dest="command")

    # export
    exp = sub.add_parser("export", help="Export entire library to JSON + checklist")
    exp.add_argument("-o", "--output", default="apple_music_library.json",
                     help="Output file (default: apple_music_library.json)")

    # import
    imp = sub.add_parser("import", help="Import library from a previous export")
    imp.add_argument("-i", "--input", default="apple_music_library.json",
                     help="Input file (default: apple_music_library.json)")
    imp.add_argument("--dry-run", action="store_true",
                     help="Preview without making changes")
    imp.add_argument("--playlists-only", action="store_true",
                     help="Only recreate playlists (assumes songs already re-added)")
    imp.add_argument("--songs-only", action="store_true",
                     help="Only check for missing songs, skip playlists")

    args = parser.parse_args()

    if args.command == "export":
        export_library(args.output)
    elif args.command == "import":
        import_library(args.input, args.dry_run,
                       getattr(args, "playlists_only", False),
                       getattr(args, "songs_only", False))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
