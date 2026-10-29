# Google Photos → HTML

A Python script that turns a **shared Google Photos album** into a
[publicalbum](https://www.publicalbum.org/) HTML widget (carousel), ready to paste into a web page.

It fetches **every** photo in the album, including those past the first 300, by following Google's pagination.

```
   ____ ____  _           _               _   _ _____ __  __ _
  / ___|  _ \| |__   ___ | |_ ___  ___   | | | |_   _|  \/  | |
 | |  _| |_) | '_ \ / _ \| __/ _ \/ __|  | |_| | | | | |\/| | |
 | |_| |  __/| | | | (_) | || (_) \__ \  |  _  | | | | |  | | |___
  \____|_|   |_| |_|\___/ \__\___/|___/  |_| |_| |_| |_|  |_|_____|

  › Opening link https://photos.app.goo.gl/XXXXXXXXXXXXXXXXX
  ✔ Album found: My album
  ✔ Expected total: 750 photo(s) (announced by Google)

  ✔ Page   1:  300 photo(s)   total 300
  ✔ Page   2:  300 photo(s)   total 600
  ✔ Page   3:  150 photo(s)   total 750
  ████████████████████████████████████████ 100.0%  750/750  page 3

  ✔ 750 photo(s) fetched in 2.4 s
  ✔ File written: /path/to/album.html
```

## Requirements

- Python 3.8 or later
- The `requests` library

```bash
pip install requests
```

## Usage

```bash
python google_photos_urls.py <share_link> [options]
```

The link can be a short link (`https://photos.app.goo.gl/...`) or a full link
(`https://photos.google.com/share/...?key=...`). The album must be shared via link.

### Examples

```bash
# Generate album.html in the current folder
python google_photos_urls.py https://photos.app.goo.gl/XXXXXXXXXXXXXXXXX

# Choose the output file
python google_photos_urls.py https://photos.app.goo.gl/XXXXXXXXXXXXXXXXX -o gallery.html

# Customize the title, description and image size
python google_photos_urls.py https://photos.app.goo.gl/XXXXXXXXXXXXXXXXX \
    --title "Summer 2025" --description "A few memories" --size w2560-h1440
```

### Options

| Option | Description | Default |
|---|---|---|
| `-o`, `--output` | Generated HTML file | `album.html` |
| `--title` | Title shown in the widget | album title |
| `--description` | Description shown in the widget | `Shared album · Tap to view!` |
| `--size` | Size suffix appended to each image | `w1920-h1080` |
| `--total` | Expected number of photos, if Google doesn't announce it | auto-detected |
| `--debug` | Save Google's raw responses to `debug_album/` | off |

Useful `--size` values: `w1920-h1080` (Full HD), `w2560-h1440`, `w1280`
(width only), `d` (original file).

## Generated file

```html
<script src="https://cdn.jsdelivr.net/npm/publicalbum@latest/embed-ui.min.js" async></script>
<div class="pa-carousel-widget" style="width:100%; height:100%; display:none;"
  data-link="https://photos.app.goo.gl/XXXXXXXXXXXXXXXXX"
  data-title="My album"
  data-description="Shared album · Tap to view!">
  <object data="https://lh3.googleusercontent.com/pw/AP1Gcz...=w1920-h1080"></object>
  <object data="https://lh3.googleusercontent.com/pw/AP1Gcz...=w1920-h1080"></object>
  ...
</div>
```

Paste this block into your HTML page where the carousel should appear.
Give the parent container a height, otherwise the widget renders with zero height.

## Terminal output

- **Green**: successful steps, progress bar, final summary.
- **Red**: errors (network, unreadable response, album not found) and missing photos.
- **Yellow**: warnings, such as an unknown expected total.

When the expected total is unknown, the progress bar switches to an animated mode without a percentage.

Colors are turned off automatically when output is redirected to a file.
To turn them off manually, set the `NO_COLOR` environment variable.
To force them on, set `FORCE_COLOR`.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | All photos were fetched |
| `1` | Blocking error (invalid link, album not found, file not writable…) |
| `2` | File written, but photos are missing compared to the expected total |

## How it works

1. The short link is followed to the album's full address (`/share/<id>?key=<key>`).
2. The first ~300 photos are read from the data embedded in the page.
3. The following pages are requested from Google Photos' internal API
   (`batchexecute`, RPC `snAcKc`), using the page token returned at each step,
   until no token is left.
4. Duplicates are removed, then the HTML file is written.

## Limitations

- The API used is **unofficial**. Google can change it at any time, and the script will then need updating.
- Only albums **shared via link** are supported. Private albums are not.
- The `lh3.googleusercontent.com` URLs depend on the album being shared: if sharing is turned off, the images will stop loading.

## Troubleshooting

**The script stops at 300 photos or reports "unreadable response"**

Run it again with `--debug`:

```bash
python google_photos_urls.py <link> --debug
```

The files `debug_album/initial_page.html` and `debug_album/page_002.txt`
contain what Google returned, which helps adjust the decoding.

**"No photos found"**

Check that the link opens in a private browsing window, without being signed in to a Google account.

**Garbled characters in the Windows console**

Use Windows Terminal, or run `chcp 65001` before the script to switch the console to UTF-8.
