#!/usr/bin/env python3
"""Build static share pages for posts and comments, so link previews (X, iMessage, Slack, …) show the
actual post or comment instead of the generic site preview.

    python3 tools/build-shares.py            # rebuild everything that changed
    python3 tools/build-shares.py --force    # re-render every card image

Output (all committed to the repo, served by GitHub Pages):
  p/<post id>/index.html       post stub     -> redirects visitors to /#post-<id>
  c/<comment id>/index.html    post comment  -> /#c-<id>
  ac/<comment id>/index.html   article comment -> /#ac-<id>
  share/<kind>-<id>.png|jpg    1200x630 preview image (post photo, or a rendered text card)
  share/manifest.json          what was built (content hashes), so unchanged items are skipped

Each stub has server-rendered og:/twitter: tags (crawlers don't run JS) plus an instant JS redirect
(and a <noscript> meta refresh) for real visitors.

Data comes from Supabase's REST API with the site's public (publishable) anon key, so only rows the
public can read are ever published. Anything that disappears (deleted, or no longer public) has its stub
and image removed on the next run. The GitHub Action .github/workflows/share-pages.yml runs this every
few minutes and commits the result.
"""
import base64, hashlib, html, io, json, os, re, shutil, sys, unicodedata, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://johnnyskyride.com"
FONTS = os.path.join(ROOT, "tools", "fonts")
SHARE = os.path.join(ROOT, "share")
KINDS = {"post": "p", "comment": "c", "acomment": "ac"}
OWNER_HANDLE = "johnnyskyride"
FORCE = "--force" in sys.argv
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
UA = "johnnyskyride-share-builder/1.0"

# ---------------------------------------------------------------- data
def sb_config():
    src = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    url = re.search(r'const SB_URL\s*=\s*"([^"]+)"', src).group(1)
    key = re.search(r'const SB_KEY\s*=\s*"([^"]+)"', src).group(1)
    return url.rstrip("/"), key

def rest(table, select, order="created_at.asc"):
    url, key = SB
    out, start, page = [], 0, 1000
    while True:
        req = urllib.request.Request(f"{url}/rest/v1/{table}?select={select}&order={order}",
                                     headers={"apikey": key, "Authorization": f"Bearer {key}", "Range": f"{start}-{start+page-1}", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            if r.status not in (200, 206): raise RuntimeError(f"{table}: HTTP {r.status}")
            rows = json.load(r)
        if not isinstance(rows, list): raise RuntimeError(f"{table}: unexpected response")
        out += rows
        if len(rows) < page: return out
        start += page

def fetch_bytes(u, limit=15_000_000):
    req = urllib.request.Request(u, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read(limit + 1)
    if len(data) > limit: raise RuntimeError("too large")
    return data

def data_uri_bytes(s):
    m = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", s or "", re.S)
    if not m: return None
    return base64.b64decode(m.group(3)) if m.group(2) else urllib.parse.unquote_to_bytes(m.group(3))

# ---------------------------------------------------------------- text
EMBED = "\n---embed---\n"
def split_embed(body):
    body = body or ""
    if EMBED not in body: return body, None
    text, raw = body.split(EMBED, 1)
    try: emb = json.loads(raw)
    except Exception: emb = None
    return text, emb if isinstance(emb, dict) else None

def strip_fmt(t):
    # same rules as the site's stripFormatting(): **bold**, *italic*, _italic_, ***both***
    t = re.sub(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", r"\1", t, flags=re.S)
    t = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"\1", t, flags=re.S)
    t = re.sub(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])", r"\1", t, flags=re.S)
    t = re.sub(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])", r"\1", t, flags=re.S)
    return t

def fmt_segments(t):
    """Split text into (text, bold, italic) runs following the site's markdown rules."""
    pat = re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*|\*\*(?=\S)(.+?)(?<=\S)\*\*|(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])|(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])", re.S)
    out, pos = [], 0
    for m in pat.finditer(t):
        if m.start() > pos: out.append((t[pos:m.start()], False, False))
        if m.group(1) is not None: out.append((m.group(1), True, True))
        elif m.group(2) is not None: out.append((m.group(2), True, False))
        else: out.append((m.group(3) or m.group(4), False, True))
        pos = m.end()
    if pos < len(t): out.append((t[pos:], False, False))
    return out

def oneline(t, n):
    t = re.sub(r"\s+", " ", t or "").strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"

# ---------------------------------------------------------------- card rendering (Pillow)
from PIL import Image, ImageDraw, ImageFont, ImageOps
from fontTools.ttLib import TTFont

EMOJI_PATHS = ["/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf", os.path.join(FONTS, "NotoColorEmoji.ttf")]
class Fonts:
    def __init__(self):
        f = lambda n: os.path.join(FONTS, n)
        self.chains = {
            (False, False): [f("Roboto-Regular.ttf"), f("NotoSans-Regular.ttf"), f("NotoSansJP-subset-Regular.otf"), f("DejaVuSans.ttf")],
            (True, False): [f("Roboto-Bold.ttf"), f("NotoSans-Bold.ttf"), f("NotoSansJP-subset-Bold.otf"), f("DejaVuSans.ttf")],
            (False, True): [f("Roboto-Italic.ttf"), f("NotoSans-Regular.ttf"), f("NotoSansJP-subset-Regular.otf"), f("DejaVuSans.ttf")],
            (True, True): [f("Roboto-BoldItalic.ttf"), f("NotoSans-Bold.ttf"), f("NotoSansJP-subset-Bold.otf"), f("DejaVuSans.ttf")],
        }
        self.cmaps, self.cache = {}, {}
        self.emoji_path = next((p for p in EMOJI_PATHS if os.path.exists(p)), None)
        if self.emoji_path: self.cmaps[self.emoji_path] = set(TTFont(self.emoji_path, lazy=True).getBestCmap())
        for chain in self.chains.values():
            for p in chain:
                if p not in self.cmaps: self.cmaps[p] = set(TTFont(p, lazy=True).getBestCmap())
        self.emoji_cache = {}
    def font(self, path, size):
        k = (path, size)
        if k not in self.cache: self.cache[k] = ImageFont.truetype(path, size)
        return self.cache[k]
    def pick(self, ch, bold, italic):
        cp = ord(ch)
        for p in self.chains[(bold, italic)]:
            if cp in self.cmaps[p]: return p
        return self.chains[(bold, italic)][-1]
    def is_emoji(self, cluster):
        return bool(self.emoji_path) and ord(cluster[0]) in self.cmaps[self.emoji_path] and (
            ord(cluster[0]) > 0x2100 or "\ufe0f" in cluster or "\u20e3" in cluster) and not unicodedata.category(cluster[0]).startswith("L")
    def emoji_img(self, cluster, size):
        k = (cluster, size)
        if k not in self.emoji_cache:
            ef = self.font(self.emoji_path, 109)
            im = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((10, 10), cluster, font=ef, embedded_color=True)
            bb = im.getbbox()
            if not bb: self.emoji_cache[k] = None
            else:
                im = im.crop(bb); s = size / max(im.size)
                self.emoji_cache[k] = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        return self.emoji_cache[k]

def clusters(s):
    """Rough grapheme clusters: base char + variation selectors / ZWJ sequences / skin tones / keycaps."""
    out, i = [], 0
    while i < len(s):
        j = i + 1
        while j < len(s) and (s[j] in "\ufe0f\ufe0e\u20e3" or 0x1F3FB <= ord(s[j]) <= 0x1F3FF or s[j] == "\u200d" or (s[j - 1] == "\u200d")
                              or (0x1F1E6 <= ord(s[i]) <= 0x1F1FF and j == i + 1 and 0x1F1E6 <= ord(s[j]) <= 0x1F1FF) or 0xE0020 <= ord(s[j]) <= 0xE007F):
            j += 1
        out.append(s[i:j]); i = j
    return out

class Run:  # a measured piece of a line
    __slots__ = ("text", "font", "emoji", "w", "color", "pad")
def layout(F, segments, size, maxw, color, max_lines):
    """Word-wrap formatted segments. Returns list of lines (each a list of Run), and whether text was cut."""
    tokens = []  # (piece, bold, italic) where piece is a word, a space or "\n"
    for text, b, it in segments:
        for part in re.split(r"(\n| +)", text):
            if part: tokens.append((part, b, it))
    def measure(piece, b, it):
        runs, cur, curfont = [], "", None
        def flush():
            nonlocal cur
            if cur:
                r = Run(); r.text = cur; r.font = F.font(curfont, size); r.emoji = None; r.color = color
                r.w = r.font.getlength(cur); runs.append(r); cur = ""
        for cl in clusters(piece):
            if F.is_emoji(cl):
                flush(); img = F.emoji_img(cl, int(size * 1.02))
                if img is None: continue
                r = Run(); r.text = cl; r.font = None; r.emoji = img; r.color = color; r.w = img.width + size * 0.08; runs.append(r)
                continue
            p = F.pick(cl[0], b, it)
            if p != curfont: flush(); curfont = p
            cur += cl
        flush()
        return runs
    lines, line, lw, cut = [], [], 0.0, False
    def push():
        nonlocal line, lw
        while line and line[-1].text.strip() == "" and line[-1].emoji is None: lw -= line[-1].w; line.pop()
        lines.append(line); line, lw = [], 0.0
    for piece, b, it in tokens:
        if piece == "\n": push(); continue
        runs = measure(piece, b, it); w = sum(r.w for r in runs)
        if piece.strip() == "":
            if line: line += runs; lw += w
            continue
        if lw + w > maxw and line: push()
        if w > maxw:  # a very long word: break by characters
            for r in runs:
                for ch in ([r] if r.emoji else [r.text[i:i+1] for i in range(len(r.text))]):
                    rr = ch if isinstance(ch, Run) else measure(ch, b, it)[0]
                    if lw + rr.w > maxw and line: push()
                    line.append(rr); lw += rr.w
            continue
        line += runs; lw += w
    if line: push()
    while lines and not lines[-1]: lines.pop()
    # collapse runs of blank lines
    compact = []
    for l in lines:
        if not l and compact and not compact[-1]: continue
        compact.append(l)
    lines = compact
    if len(lines) > max_lines:
        lines, cut = lines[:max_lines], True
    return lines, cut

def draw_lines(F, canvas, lines, x, y, size, lh, ellipsis_color):
    d = ImageDraw.Draw(canvas)
    for i, line in enumerate(lines):
        cx = x
        for r in line:
            if r.emoji is not None:
                pad = getattr(r, "pad", 0) or 0
                canvas.alpha_composite(r.emoji, (int(cx + pad), int(y + (lh - r.emoji.height) / 2 + size * 0.04)))
            else:
                d.text((cx, y + (lh - size) / 2 - size * 0.06), r.text, font=r.font, fill=r.color)
            cx += r.w
        y += lh
    return y

# Verified stars, same shape/colours as the site's <svg class="star"> (viewBox 0 0 24 24)
STAR_PTS = [(12, 2), (14.9, 8.9), (22.3, 9.5), (16.7, 14.3), (18.4, 21.5), (12, 17.8), (5.6, 21.5), (7.3, 14.3), (1.7, 9.5), (9.1, 8.9)]
STAR_COLORS = {"gold": (0xF5, 0xC5, 0x18, 255), "silver": (0xC0, 0xC0, 0xC0, 255)}
_star_cache = {}
def star_img(kind, px):
    k = (kind, px)
    if k not in _star_cache:
        ss = 8; S = px * ss
        im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(im).polygon([(x / 24 * S, y / 24 * S) for x, y in STAR_PTS], fill=STAR_COLORS[kind])
        _star_cache[k] = im.resize((px, px), Image.LANCZOS)
    return _star_cache[k]
def star_run(kind, size, gap_before=True):
    r = Run(); img = star_img(kind, max(8, round(size * 0.82)))
    r.text = "★"; r.font = None; r.emoji = img; r.color = None
    r.w = img.width + (size * 0.28 if gap_before else 0) + size * 0.12
    r.pad = size * 0.28 if gap_before else 0
    return r
def text_runs(F, text, size, color, bold=False):
    lines, _ = layout(F, [(text, bold, False)], size, 10_000, color, 1)
    return lines[0] if lines else []
def fit_line(runs, maxw, F, size):
    """Trim a single line of runs to maxw, adding an ellipsis."""
    if sum(r.w for r in runs) <= maxw: return runs
    out = list(runs)
    e = Run(); e.text = "…"; e.font = F.font(F.chains[(False, False)][0], size); e.emoji = None; e.color = MUTED; e.w = e.font.getlength("…")
    while out and sum(r.w for r in out) + e.w > maxw:
        last = out[-1]
        if last.emoji is None and len(last.text) > 1:
            nr = Run(); nr.text = last.text[:-1]; nr.font = last.font; nr.emoji = None; nr.color = last.color; nr.w = nr.font.getlength(nr.text); out[-1] = nr
        else: out.pop()
    return out + [e]

def circle(img, d):
    img = ImageOps.fit(img.convert("RGB"), (d * 3, d * 3), Image.LANCZOS)
    m = Image.new("L", (d * 3, d * 3), 0); ImageDraw.Draw(m).ellipse((0, 0, d * 3 - 1, d * 3 - 1), fill=255)
    out = Image.new("RGBA", (d * 3, d * 3), (0, 0, 0, 0)); out.paste(img, (0, 0), m)
    return out.resize((d, d), Image.LANCZOS)

def initials_avatar(F, name, d):
    im = Image.new("RGBA", (d, d), (0, 0, 0, 0)); dr = ImageDraw.Draw(im)
    dr.ellipse((0, 0, d - 1, d - 1), fill=(32, 35, 39, 255), outline=(255, 107, 181, 255), width=3)
    ini = "".join(c for c in name if c.isalnum())[:2].upper() or "?"
    f = F.font(F.chains[(True, False)][0], d // 3)
    w = f.getlength(ini); dr.text(((d - w) / 2, d / 2 - d / 6 - 4), ini, font=f, fill=(231, 233, 234, 255))
    return im

PINK, TEXT, MUTED, BORDER = (255, 107, 181, 255), (231, 233, 234, 255), (113, 118, 123, 255), (47, 51, 54, 255)

def render_card(F, item, avatar_img):
    W, H = 1200, 630
    im = Image.new("RGBA", (W, H), (0, 0, 0, 255)); d = ImageDraw.Draw(im)
    d.rounded_rectangle((28, 28, W - 29, H - 29), radius=28, fill=(10, 10, 12, 255), outline=BORDER, width=2)
    pad = 72
    av = 92
    if avatar_img is not None: im.alpha_composite(circle(avatar_img, av), (pad, pad))
    else: im.alpha_composite(initials_avatar(F, item["name"], av), (pad, pad))
    # name + handle
    nx = pad + av + 24
    st = star_run(item["star"], 38) if item.get("star") else None
    name_runs = fit_line(text_runs(F, item["name"], 38, TEXT, True), W - nx - pad - (st.w if st else 0), F, 38)
    draw_lines(F, im, [name_runs + ([st] if st else [])], nx, pad + 2, 38, 48, MUTED)
    sub = "@" + item["handle"] + ("  ·  " + item["context"] if item.get("context") else "")
    cst = star_run(item["context_star"], 26) if item.get("context") and item.get("context_star") else None
    sub_runs = fit_line(text_runs(F, oneline(sub, 80), 26, MUTED), W - nx - pad - (cst.w if cst else 0), F, 26)
    draw_lines(F, im, [sub_runs + ([cst] if cst else [])], nx, pad + 52, 26, 34, MUTED)
    # body text: largest size that fits
    top, bottom = pad + av + 34, H - pad - 44
    q = item.get("quote_obj")
    if q: bottom -= 150
    body = item["text"].strip() or ("▶ Video" if item.get("video") else " ")
    if item.get("video") and item["text"].strip(): body += "  ▶"
    segs = fmt_segments(body)
    for size in (60, 54, 48, 44, 40, 36, 33):
        lh = round(size * 1.3); maxl = max(1, (bottom - top) // lh)
        lines, cut = layout(F, segs, size, W - 2 * pad, TEXT, maxl)
        if not cut: break
    if cut:
        while lines and not lines[-1]: lines.pop()
    if cut and lines:  # add an ellipsis to the last line
        last = lines[-1]
        while last and sum(r.w for r in last) > W - 2 * pad - size: last.pop()
        e = Run(); e.text = "…"; e.font = F.font(F.chains[(False, False)][0], size); e.emoji = None; e.color = MUTED; e.w = e.font.getlength("…"); last.append(e)
    draw_lines(F, im, lines, pad, top, size, lh, MUTED)
    if q:  # quoted post, drawn like the site's embed box
        qy0, qy1 = bottom + 16, bottom + 136
        d.rounded_rectangle((pad, qy0, W - pad, qy1), radius=18, outline=BORDER, width=2, fill=(16, 18, 21, 255))
        qst = star_run(q["star"], 24) if q.get("star") else None
        head = text_runs(F, q["name"], 24, TEXT, True) + ([qst] if qst else []) + text_runs(F, ("  " if not qst else " ") + "@" + q["handle"], 24, MUTED)
        draw_lines(F, im, [fit_line(head, W - 2 * pad - 48, F, 24)], pad + 24, qy0 + 14, 24, 32, MUTED)
        ql, qcut = layout(F, fmt_segments(q["text"]), 28, W - 2 * pad - 48, TEXT, 2)
        if qcut and ql:
            e = Run(); e.text = "…"; e.font = F.font(F.chains[(False, False)][0], 28); e.emoji = None; e.color = MUTED; e.w = e.font.getlength("…")
            while ql[-1] and sum(r.w for r in ql[-1]) > W - 2 * pad - 90: ql[-1].pop()
            ql[-1].append(e)
        draw_lines(F, im, ql, pad + 24, qy0 + 50, 28, 36, MUTED)
    # footer
    f = F.font(F.chains[(True, False)][0], 26)
    foot = "johnnyskyride.com"
    rab = F.emoji_img("🐇", 30) if F.emoji_path else None
    fx = pad
    if rab is not None: im.alpha_composite(rab, (fx, H - pad - 22)); fx += rab.width + 12
    d.text((fx, H - pad - 22), foot, font=f, fill=PINK)
    tag = item.get("tag", "")
    if tag:
        f2 = F.font(F.chains[(False, False)][0], 24); w = f2.getlength(tag)
        d.text((W - pad - w, H - pad - 20), tag, font=f2, fill=MUTED)
    return im.convert("RGB")

def save_png(img, path):
    q = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
    q.save(path, optimize=True)

def save_photo(raw, path):
    img = Image.open(io.BytesIO(raw)); img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((1200, 1200), Image.LANCZOS)
    img.save(path, "JPEG", quality=85, optimize=True, progressive=True)
    return img.size

# ---------------------------------------------------------------- stub page
STUB = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta name="robots" content="noindex, follow">
<link rel="canonical" href="{url}">
<link rel="icon" href="/favicon-32.png">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Johnny Skyride">
<meta property="og:url" content="{url}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{desc}">
<meta property="og:image" content="{image}">
<meta property="og:image:secure_url" content="{image}">
<meta property="og:image:type" content="{image_type}">
<meta property="og:image:width" content="{iw}">
<meta property="og:image:height" content="{ih}">
<meta property="og:image:alt" content="{alt}">
<meta property="article:published_time" content="{published}">
<meta name="twitter:card" content="{card}">
<meta name="twitter:site" content="@johnnyskyride">{creator}
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{desc}">
<meta name="twitter:image" content="{image}">
<meta name="twitter:image:alt" content="{alt}">
<script>location.replace({target_js});</script>
<noscript><meta http-equiv="refresh" content="0; url={target}"></noscript>
<style>body{{background:#000;color:#e7e9ea;font:16px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:600px;margin:40px auto;padding:0 16px}}a{{color:#ff6bb5}}p{{white-space:pre-wrap}}</style>
</head><body>
<p><b>{name_html}</b> <span style="color:#71767b">@{handle_html}</span></p>
<p>{text_html}</p>
<p><a href="{target}">Open on johnnyskyride.com →</a></p>
</body></html>
"""

def esc(s): return html.escape(s or "", quote=True)

# ---------------------------------------------------------------- main
def main():
    global SB
    SB = sb_config()
    posts = rest("posts", "id,author,handle,avatar,body,media,media_type,bot,created_at")
    comments = rest("comments", "id,post_id,parent_id,name,handle,avatar,body,media,media_type,bot,created_at")
    acomments = rest("article_comments", "id,article_id,parent_id,name,handle,avatar,body,bot,created_at")
    articles = rest("articles", "id,title", order="id.asc")
    members = rest("members", "handle,name,avatar,bot", order="handle.asc")
    settings = {r["key"]: r["value"] for r in rest("site_settings", "key,value", order="key.asc")}

    # Safety: a sudden drop to nothing almost certainly means a fetch problem, not mass deletion.
    man_path = os.path.join(SHARE, "manifest.json")
    old = json.load(open(man_path)) if os.path.exists(man_path) else {"items": {}}
    if old["items"] and not (posts or comments) :
        sys.exit("refusing to delete every share page (empty data); aborting")

    mem = {m["handle"].lower(): m for m in members}
    owner_avatar = settings.get("avatar_url") or "/assets/avatar.jpg"
    post_by_id = {p["id"]: p for p in posts}
    cm_by_id = {c["id"]: c for c in comments}
    acm_by_id = {c["id"]: c for c in acomments}
    art_by_id = {a["id"]: a for a in articles}

    # Same rules as the site (index.html): posts -> silver if bot else gold (feed renderer);
    # comments -> commentStar(): gold for @johnnyskyride, silver for bot rows or BOTS names, else none;
    # quoted posts (embeds) -> silver if embed.bot, gold for @johnnyskyride, else none.
    BOTS = ["sukozu", "letty the lead", "6-string", "4-funk", "drummond"]
    def is_bot_name(name, handle):
        n, h = (name or "").lower().strip(), (handle or "").lower().strip()
        return any(n == b or h == b or b in n or b in h for b in BOTS)
    def post_star(p): return "silver" if p.get("bot") else "gold"
    def comment_star(c):
        if (c.get("handle") or "").lower() == OWNER_HANDLE: return "gold"
        if c.get("bot") or is_bot_name(c.get("name"), c.get("handle")): return "silver"
        return None
    def embed_star(e):
        if e.get("bot"): return "silver"
        if (e.get("handle") or "").lower() == OWNER_HANDLE: return "gold"
        return None

    def avatar_src(handle, own):
        if handle and handle.lower() == OWNER_HANDLE: return own or owner_avatar
        m = mem.get((handle or "").lower())
        return own or (m and m.get("avatar")) or ""

    items = []
    for p in posts:
        text, emb = split_embed(p.get("body"))
        if emb and not text.strip(): text = emb.get("body", "") or ""
        quote_obj = {"name": emb.get("author") or emb.get("handle") or "", "handle": emb.get("handle") or "",
                     "text": split_embed(emb.get("body", ""))[0].strip(), "star": embed_star(emb)} if emb and text.strip() else None
        quote = f'quoting @{emb.get("handle","")}: “{oneline(strip_fmt(split_embed(emb.get("body",""))[0]), 90)}”' if emb else ""
        items.append(dict(kind="post", id=p["id"], name=p.get("author") or p.get("handle") or "", handle=p.get("handle") or "",
                          avatar=avatar_src(p.get("handle"), p.get("avatar")), text=text, quote=quote, created=p["created_at"],
                          media=p.get("media"), media_type=p.get("media_type") or "", target=f"/#post-{p['id']}", context="", tag="Post", quote_obj=quote_obj,
                          star=post_star(p), context_star=None))
    for c in comments:
        post = post_by_id.get(c.get("post_id"))
        if not post: continue
        parent = cm_by_id.get(c.get("parent_id")) if c.get("parent_id") else None
        to = parent["handle"] if parent else post.get("handle")
        items.append(dict(kind="comment", id=c["id"], name=c.get("name") or c.get("handle") or "", handle=c.get("handle") or "",
                          avatar=avatar_src(c.get("handle"), c.get("avatar")), text=c.get("body") or "", quote="", created=c["created_at"],
                          media=c.get("media"), media_type=c.get("media_type") or "", target=f"/#c-{c['id']}",
                          context=f"replying to @{to}" if to else "", tag="Reply" if parent else "Comment",
                          star=comment_star(c), context_star=(comment_star(parent) if parent else post_star(post))))
    for c in acomments:
        art = art_by_id.get(c.get("article_id"))
        if not art: continue  # article not public (draft/deleted): never publish
        parent = acm_by_id.get(c.get("parent_id")) if c.get("parent_id") else None
        items.append(dict(kind="acomment", id=c["id"], name=c.get("name") or c.get("handle") or "", handle=c.get("handle") or "",
                          avatar=avatar_src(c.get("handle"), c.get("avatar")), text=c.get("body") or "", quote="", created=c["created_at"],
                          media=None, media_type="", target=f"/#ac-{c['id']}",
                          context=(f"replying to @{parent['handle']}" if parent else f"on “{oneline(art.get('title',''), 40)}”"), tag="Article comment",
                          star=comment_star(c), context_star=(comment_star(parent) if parent else None)))

    F = Fonts()
    os.makedirs(SHARE, exist_ok=True)
    new_items, keep_files, avatar_cache = {}, {"manifest.json"}, {}

    def load_avatar(src):
        if src in avatar_cache: return avatar_cache[src]
        img = None
        try:
            if src.startswith("data:"): raw = data_uri_bytes(src)
            elif src.startswith("/"): raw = open(os.path.join(ROOT, src.lstrip("/")), "rb").read()
            elif src.startswith("https://"): raw = fetch_bytes(src, 8_000_000)
            else: raw = None
            if raw: img = Image.open(io.BytesIO(raw)); img.load()
        except Exception as e: print("avatar failed:", str(e)[:80])
        avatar_cache[src] = img
        return img

    built = changed = 0
    for it in items:
        if not ID_RE.match(it["id"]): continue
        pre = KINDS[it["kind"]]
        key = f"{pre}/{it['id']}"
        is_img = (it["media_type"] or "").startswith("image/") and bool(it["media"])
        is_video = (it["media_type"] or "").startswith("video/")
        it["video"] = is_video
        sig = hashlib.sha256(json.dumps([it["name"], it["handle"], it["text"], it["quote"], it["context"], it["tag"],
                                         hashlib.sha256((it["avatar"] or "").encode()).hexdigest(),
                                         hashlib.sha256((it["media"] or "").encode()).hexdigest() if is_img else "", it.get("quote_obj"), is_video, it.get("star"), it.get("context_star"), 5]).encode()).hexdigest()[:16]
        prev = old["items"].get(key)
        img_name = None
        if prev and prev.get("sig") == sig and not FORCE and os.path.exists(os.path.join(SHARE, prev["image"])) and os.path.exists(os.path.join(ROOT, key, "index.html")):
            img_name, iw, ih = prev["image"], prev["w"], prev["h"]
        else:
            changed += 1
            if is_img:
                try:
                    raw = data_uri_bytes(it["media"]) if it["media"].startswith("data:") else fetch_bytes(it["media"])
                    img_name = f"{pre}-{it['id']}.jpg"
                    iw, ih = save_photo(raw, os.path.join(SHARE, img_name))
                except Exception as e:
                    print("photo failed, using text card:", key, str(e)[:80]); img_name = None
            if not img_name:
                card = render_card(F, it, load_avatar(it["avatar"]) if it["avatar"] else None)
                img_name = f"{pre}-{it['id']}.png"; save_png(card, os.path.join(SHARE, img_name)); iw, ih = 1200, 630
        for ext in (".png", ".jpg"):  # drop a stale image of the other type
            other = f"{pre}-{it['id']}{ext}"
            if other != img_name and os.path.exists(os.path.join(SHARE, other)): os.remove(os.path.join(SHARE, other))
        keep_files.add(img_name)

        plain = strip_fmt(it["text"]).strip()
        desc = oneline(plain + (("  — " + it["quote"]) if it["quote"] else ""), 200) or ("Video post" if is_video else "johnnyskyride.com")
        kind_word = {"post": "", "comment": " · comment", "acomment": " · article comment"}[it["kind"]]
        title = oneline(f"{it['name']} (@{it['handle']}){kind_word}", 90)
        alt = oneline(f"{it['name']}: {plain}", 400) if not is_img else oneline(plain or f"Image posted by {it['name']}", 400)
        url = f"{SITE}/{key}/"
        image = f"{SITE}/share/{img_name}?v={sig[:8]}"
        page = STUB.format(title=esc(title), desc=esc(desc), url=url, image=esc(image), alt=esc(alt),
                           image_type="image/jpeg" if img_name.endswith(".jpg") else "image/png", iw=iw, ih=ih,
                           published=esc(it["created"]), card="summary_large_image",
                           creator="\n<meta name=\"twitter:creator\" content=\"@johnnyskyride\">" if it["handle"].lower() == OWNER_HANDLE else "",
                           target=esc(it["target"]), target_js=json.dumps(it["target"]).replace("<", "\\u003c"),
                           name_html=esc(it["name"]), handle_html=esc(it["handle"]), text_html=esc(plain))
        d = os.path.join(ROOT, key); os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, "index.html")
        if not os.path.exists(fp) or open(fp, encoding="utf-8").read() != page:
            open(fp, "w", encoding="utf-8").write(page)
        new_items[key] = {"sig": sig, "image": img_name, "w": iw, "h": ih}
        built += 1

    # remove stubs/images for anything no longer public
    removed = 0
    for pre in KINDS.values():
        base = os.path.join(ROOT, pre)
        if not os.path.isdir(base): continue
        for name in os.listdir(base):
            if f"{pre}/{name}" not in new_items:
                shutil.rmtree(os.path.join(base, name), ignore_errors=True); removed += 1
        if not os.listdir(base): os.rmdir(base)
    for name in os.listdir(SHARE):
        if name not in keep_files: os.remove(os.path.join(SHARE, name))
    json.dump({"items": dict(sorted(new_items.items()))}, open(man_path, "w"), indent=0, sort_keys=True)
    print(f"share pages: {built} live, {changed} (re)rendered, {removed} removed")

if __name__ == "__main__":
    import urllib.parse
    main()
