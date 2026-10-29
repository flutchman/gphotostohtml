#!/usr/bin/env python3
"""
Generate an HTML file (publicalbum carousel widget) from a shared
Google Photos album, fetching ALL photos (pagination included).

Usage:
    python google_photos_urls.py https://photos.app.goo.gl/XXXXXXXXXXXXXXXXX
    python google_photos_urls.py <link> -o album.html
    python google_photos_urls.py <link> --title "My album" --size w1920-h1080
    python google_photos_urls.py <link> --total 750   # force the expected total
    python google_photos_urls.py <link> --debug       # save raw responses

Dependency: pip install requests
"""

import argparse
import html
import json
import os
import random
import re
import shutil
import sys
import time

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

BATCH_URL = "https://photos.google.com/_/PhotosUi/data/batchexecute"
RPC_ID = "snAcKc"  # Internal Google Photos RPC: next page of a shared album

DEFAULT_DESCRIPTION = "Shared album"
MAX_PAGES = 500
DEBUG_DIR = "debug_album"

# Fallback: items present in plain text in the initial HTML
PATTERN_PHOTO = re.compile(
    r'\["(AF1Qip[^"]+)",\["(https://lh3\.googleusercontent\.com/[^"]+)",(\d+),(\d+)'
)
PATTERN_OG = r'<meta\s+property="og:{}"\s+content="([^"]*)"'
PATTERN_INIT = re.compile(
    r"AF_initDataCallback\(\{key:\s*'(ds:\d+)'.*?data:(.*?), sideChannel:\s*\{\}\}\);",
    re.DOTALL,
)
# "752 photos", "1,204 items", "38 éléments"… (page description or title)
PATTERN_COUNT = re.compile(
    r"(\d{1,3}(?:[\s  .,]\d{3})*|\d+)\s*(?:photos?|items?|videos?|éléments?|vidéos?)",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Display: colors, splash, progress bar
# --------------------------------------------------------------------------

class Screen:
    GREEN, RED, YELLOW, CYAN, GREY, BOLD, RESET = (
        "\033[92m", "\033[91m", "\033[93m", "\033[96m", "\033[90m", "\033[1m", "\033[0m"
    )

    def __init__(self):
        self.color = (
            sys.stderr.isatty() and "NO_COLOR" not in os.environ
        ) or "FORCE_COLOR" in os.environ
        if self.color and os.name == "nt":
            os.system("")  # enable ANSI escape sequences in the Windows console
        self.bar_active = False

    def c(self, code, text):
        return f"{code}{text}{self.RESET}" if self.color else text

    def _write(self, text):
        self.clear_bar()
        print(text, file=sys.stderr, flush=True)

    def ok(self, text):
        self._write(f"  {self.c(self.GREEN, '✔')} {text}")

    def error(self, text):
        self._write(f"  {self.c(self.RED, '✘')} {self.c(self.RED, text)}")

    def warn(self, text):
        self._write(f"  {self.c(self.YELLOW, '!')} {self.c(self.YELLOW, text)}")

    def info(self, text):
        self._write(f"  {self.c(self.CYAN, '›')} {text}")

    def fatal(self, text):
        self.error(text)
        sys.exit(1)

    def splash(self):
        art = r"""
   ____ ____  _           _               _   _ _____ __  __ _
  / ___|  _ \| |__   ___ | |_ ___  ___   | | | |_   _|  \/  | |
 | |  _| |_) | '_ \ / _ \| __/ _ \/ __|  | |_| | | | | |\/| | |
 | |_| |  __/| | | | (_) | || (_) \__ \  |  _  | | | | |  | | |___
  \____|_|   |_| |_|\___/ \__\___/|___/  |_| |_| |_| |_|  |_|_____|
"""
        print(self.c(self.CYAN, art), file=sys.stderr)
        print(
            self.c(self.GREY, "        Shared Google Photos album  →  publicalbum HTML widget\n"),
            file=sys.stderr,
        )

    def bar(self, current, total, page):
        width = max(10, min(40, shutil.get_terminal_size((80, 20)).columns - 45))
        if total:
            ratio = min(1.0, current / total)
            filled = int(ratio * width)
            bar = "█" * filled + "░" * (width - filled)
            text = f"{ratio * 100:5.1f}%  {current}/{total}"
        else:
            pos = page % width
            bar = ("░" * pos + "███" + "░" * max(0, width - pos - 3))[:width]
            text = f"{current} photo(s)"
        line = f"  {self.c(self.GREEN, bar)} {text}  {self.c(self.GREY, f'page {page}')}"
        if sys.stderr.isatty():
            print("\r\033[K" + line, end="", file=sys.stderr, flush=True)
            self.bar_active = True

    def clear_bar(self):
        if self.bar_active:
            print("\r\033[K", end="", file=sys.stderr, flush=True)
            self.bar_active = False


screen = Screen()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

class Debug:
    def __init__(self, enabled: bool, folder: str = DEBUG_DIR):
        self.enabled = enabled
        self.folder = folder
        if enabled:
            os.makedirs(folder, exist_ok=True)

    def save(self, name: str, content: str):
        if self.enabled:
            with open(os.path.join(self.folder, name), "w", encoding="utf-8") as f:
                f.write(content)


def global_data(page: str, key: str):
    m = re.search(r'"%s":"([^"]*)"' % re.escape(key), page)
    return m.group(1) if m else None


def is_item(x) -> bool:
    """An album item: ["AF1Qip...", ["https://lh3...", width, height, ...], ...]"""
    return (
        isinstance(x, list)
        and len(x) >= 2
        and isinstance(x[0], str)
        and x[0].startswith("AF1Qip")
        and isinstance(x[1], list)
        and x[1]
        and isinstance(x[1][0], str)
        and x[1][0].startswith("https://lh3.googleusercontent.com/")
    )


def find_items(obj, acc=None):
    """Recursively walk a JSON structure and collect album items."""
    if acc is None:
        acc = []
    if is_item(obj):
        acc.append((obj[0], obj[1][0]))
        return acc
    if isinstance(obj, list):
        for e in obj:
            find_items(e, acc)
    elif isinstance(obj, dict):
        for e in obj.values():
            find_items(e, acc)
    return acc


def find_token(data, excluded):
    """Next-page token: a long string at the top level of the response."""
    if not isinstance(data, list):
        return None
    candidates = [
        (i, v) for i, v in enumerate(data)
        if isinstance(v, str)
        and len(v) >= 20
        and v not in excluded
        and not v.startswith(("http", "AF1Qip"))
    ]
    if not candidates:
        return None
    for i, v in candidates:
        if i in (1, 2):
            return v
    return candidates[0][1]


# --------------------------------------------------------------------------
# Initial page
# --------------------------------------------------------------------------

def init_blocks(page: str):
    """Return the decoded AF_initDataCallback blocks: {key: data}."""
    blocks = {}
    for key, raw in PATTERN_INIT.findall(page):
        try:
            blocks[key] = json.loads(raw)
        except json.JSONDecodeError:
            pass
    return blocks


def meta_og(page: str, name: str):
    m = re.search(PATTERN_OG.format(name), page)
    return html.unescape(m.group(1)).strip() if m else None


def extract_title(page: str) -> str:
    title = meta_og(page, "title")
    if not title:
        m = re.search(r"<title>([^<]*)</title>", page)
        title = html.unescape(m.group(1)).strip() if m else "Album"
    title = re.sub(r"\s*-\s*Google Photos\s*$", "", title)
    title = re.sub(r"\s*·\s*[^·]*\d{4}[^·]*$", "", title)
    return title or "Album"


def extract_total(page: str):
    """Total item count announced by the page (description/title), if any."""
    for name in ("description", "title"):
        text = meta_og(page, name) or ""
        m = PATTERN_COUNT.search(text)
        if m:
            return int(re.sub(r"\D", "", m.group(1)))
    return None


# --------------------------------------------------------------------------
# Pagination (batchexecute)
# --------------------------------------------------------------------------

def next_page(session, ctx, token, reqid, debug, num):
    params = {
        "rpcids": RPC_ID,
        "source-path": f"/share/{ctx['album_id']}",
        "hl": "en",
        "soc-app": "165",
        "soc-platform": "1",
        "soc-device": "1",
        "_reqid": str(reqid),
        "rt": "c",
    }
    if ctx.get("fsid"):
        params["f.sid"] = ctx["fsid"]
    if ctx.get("bl"):
        params["bl"] = ctx["bl"]

    rpc_args = json.dumps([ctx["album_id"], token, None, ctx["key"]], separators=(",", ":"))
    body = {"f.req": json.dumps([[[RPC_ID, rpc_args, None, "generic"]]], separators=(",", ":"))}
    if ctx.get("at"):
        body["at"] = ctx["at"]

    resp = session.post(
        BATCH_URL,
        params=params,
        data=body,
        headers={
            **HEADERS,
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://photos.google.com",
            "Referer": ctx["album_url"],
            "X-Same-Domain": "1",
        },
        timeout=30,
    )
    debug.save(f"page_{num:03d}.txt", resp.text)
    resp.raise_for_status()
    return decode_batch(resp.text)


def decode_batch(text: str):
    """Extract the payload of the snAcKc response ()]}' prefix + JSON chunks)."""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue
        try:
            chunk = json.loads(line)
        except json.JSONDecodeError:
            continue
        for e in chunk if isinstance(chunk, list) else []:
            if (
                isinstance(e, list) and len(e) > 2
                and e[0] == "wrb.fr" and e[1] == RPC_ID and isinstance(e[2], str)
            ):
                return json.loads(e[2])
    return None


# --------------------------------------------------------------------------
# Main program
# --------------------------------------------------------------------------

def fetch_album(link: str, debug: Debug, forced_total=None):
    session = requests.Session()
    screen.info(f"Opening link {screen.c(screen.GREY, link)}")
    resp = session.get(link, headers=HEADERS, allow_redirects=True, timeout=30)
    resp.raise_for_status()
    page = resp.text
    debug.save("initial_page.html", page)

    m_id = re.search(r"/share/([^/?#]+)", resp.url)
    m_key = re.search(r"[?&]key=([^&#]+)", resp.url)
    if not m_id or not m_key:
        screen.fatal(f"Unexpected album URL after redirect: {resp.url}")

    title = extract_title(page)
    screen.ok(f"Album found: {screen.c(screen.BOLD, title)}")

    total = forced_total or extract_total(page)
    if total:
        source = "provided" if forced_total else "announced by Google"
        screen.ok(f"Expected total: {screen.c(screen.BOLD, str(total))} photo(s) ({source})")
    else:
        screen.warn("Expected total unknown (use --total to set it)")

    ctx = {
        "album_id": m_id.group(1),
        "key": m_key.group(1),
        "album_url": resp.url,
        "fsid": global_data(page, "FdrFJe"),
        "bl": global_data(page, "cfb2h"),
        "at": global_data(page, "SNlM0e"),
    }
    excluded = {ctx["album_id"], ctx["key"]}

    seen, urls = set(), []

    def add(items):
        n = 0
        for pid, url in items:
            if pid not in seen:
                seen.add(pid)
                urls.append(url)
                n += 1
        return n

    print(file=sys.stderr)

    # Page 1: data embedded in the HTML
    token = None
    for data in init_blocks(page).values():
        items = find_items(data)
        if items:
            add(items)
            token = token or find_token(data, excluded)
    if not urls:  # regex fallback
        add((pid, url) for pid, url, _w, _h in PATTERN_PHOTO.findall(page))

    if urls:
        screen.ok(f"Page {1:>3}: {len(urls):>4} photo(s)   total {len(urls)}")
    else:
        screen.error("Page   1: no photos found")
    screen.bar(len(urls), total, 1)

    # Following pages
    seen_tokens = set()
    reqid = random.randint(100000, 999999)
    num = 2
    failure = None
    while token and token not in seen_tokens and num <= MAX_PAGES:
        seen_tokens.add(token)
        try:
            data = next_page(session, ctx, token, reqid, debug, num)
        except requests.RequestException as e:
            failure = f"Page {num:>3}: network error ({e})"
            break
        if data is None:
            failure = f"Page {num:>3}: unreadable response (run again with --debug)"
            break
        added = add(find_items(data))
        if added == 0:
            break
        screen.ok(f"Page {num:>3}: {added:>4} photo(s)   total {len(urls)}")
        screen.bar(len(urls), total, num)
        token = find_token(data, excluded | seen_tokens)
        reqid += 100000
        num += 1
        time.sleep(0.2)  # be gentle with Google's servers

    screen.clear_bar()
    if failure:
        screen.error(failure)

    return page, title, urls, total


def build_html(link: str, title: str, description: str, urls, size: str) -> str:
    a = lambda s: html.escape(s, quote=True)
    lines = [
        '<script src="https://cdn.jsdelivr.net/npm/publicalbum@latest/embed-ui.min.js" async></script>',
        '<div class="pa-carousel-widget" style="width:100%; height:100%; display:none;"',
        f'  data-link="{a(link)}"',
        f'  data-title="{a(title)}"',
        f'  data-description="{a(description)}">',
    ]
    for url in urls:
        lines.append(f'  <object data="{a(url)}={size}"></object>')
    lines.append("</div>")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Generate a publicalbum HTML widget from a shared Google Photos album."
    )
    parser.add_argument("link", help="Share link (photos.app.goo.gl/... or photos.google.com/share/...)")
    parser.add_argument("-o", "--output", default="album.html", help="Output HTML file (default: album.html)")
    parser.add_argument("--title", help="Displayed title (default: album title)")
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION, help="Displayed description")
    parser.add_argument("--size", default="w1920-h1080", help="Image size suffix (default: w1920-h1080)")
    parser.add_argument("--total", type=int, help="Expected number of photos (if Google doesn't announce it)")
    parser.add_argument("--debug", action="store_true", help=f"Save raw responses to {DEBUG_DIR}/")
    args = parser.parse_args()

    screen.splash()
    start = time.time()

    try:
        page, album_title, urls, total = fetch_album(args.link, Debug(args.debug), args.total)
    except requests.RequestException as e:
        screen.fatal(f"Could not load the album: {e}")
    except KeyboardInterrupt:
        screen.clear_bar()
        screen.fatal("Interrupted by user")

    if not urls:
        screen.fatal("No photos found. Is the link public and valid?")

    title = args.title or album_title
    try:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(build_html(args.link, title, args.description, urls, args.size))
    except OSError as e:
        screen.fatal(f"Could not write {args.output}: {e}")

    elapsed = time.time() - start
    missing = total and len(urls) < total
    print(file=sys.stderr)
    if missing:
        screen.error(f"{len(urls)}/{total} photo(s) fetched, {total - len(urls)} missing")
    else:
        screen.ok(screen.c(screen.GREEN, f"{len(urls)} photo(s) fetched") + f" in {elapsed:.1f} s")
    screen.ok(f"File written: {screen.c(screen.BOLD, os.path.abspath(args.output))}")
    if args.debug:
        screen.info(f"Raw responses saved to {os.path.abspath(DEBUG_DIR)}")
    print(file=sys.stderr)

    sys.exit(2 if missing else 0)


if __name__ == "__main__":
    main()
