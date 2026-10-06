"""Whole GitHub profile as ONE animated SVG: starfield + meteors behind the name,
bio, tech logos, languages and live GitHub stats.

GitHub renders an SVG <img> with its own CSS animations but blocks every
external fetch from inside it, so the pixel font (Pixel Operator, CC0), the
tech logos and the sprites are embedded as data: URIs, and the stats are baked
in at generation time. .github/workflows/profile.yml re-runs this hourly so the
numbers stay current.

    python3 scripts/make_profile_svg.py        ->  profile.svg (repo root)

Stats come from the GitHub GraphQL API with the token in GH_STATS_TOKEN (or
GITHUB_TOKEN); without one it falls back to the local `gh` CLI session.
"""
import base64
import json
import os
import random
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
USER = "OscarAreiza"

# ---- content ----------------------------------------------------------------
TITLE = "Oscar Areiza"
BIO = [
    "Passionate about Technology, Coding, Web Development,",
    "Cloud and DevOps Culture.",
    "Systems Engineering student at CORHUILA · Junior DevOps.",
]
SECTION = "Technologies"
TECH = [  # (icon file stem, label)
    ("java", "Java"), ("html", "HTML5"), ("css", "CSS"), ("python", "Python"),
    ("postgres", "PostgreSQL"), ("mysql", "MySQL"), ("terraform", "Terraform"),
    ("aws", "AWS"), ("gcp", "Google Cloud"), ("github", "GitHub"),
]
LANG_SECTION = "Languages"
LANGUAGES = ["Spanish - Native", "English - B2"]
# Animated pixel-art sprites flanking Languages: transparent sprite sheets
# (frames side by side, built from the GIFs) stepped with CSS.
SPRITES = [  # (file, frames, frame_w, frame_h, ms_per_frame, side)
    ("jake_sprite.png", 5, 150, 144, 100, "left"),
    ("bmo_sprite.png", 8, 150, 150, 150, "right"),
]
STATS_SECTION = "Stats"
RICK = ("rick_sprite.png", 29, 280, 266, 60)  # (file, frames, frame_w, frame_h, ms_per_frame)
CHART_ZOOM = 1.3   # charts shown 30% bigger than drawn, text included

# ---- layout -----------------------------------------------------------------
W = 1000
H = 1360
BG = "#0b0f17"
TEAL = "0,201,167"

# ---- sky --------------------------------------------------------------------
SEED = 11
METEOR_PERIOD = 8.0
METEORS = 18
LAYERS = [  # far -> near
    dict(count=80, min_r=0.3, max_r=0.7, min_o=0.15, max_o=0.45, drift=90),
    dict(count=40, min_r=0.6, max_r=1.0, min_o=0.25, max_o=0.65, drift=55),
    dict(count=16, min_r=0.9, max_r=1.4, min_o=0.40, max_o=0.85, drift=32),
]
TWINKLE_CLASSES = 5
PALETTES = [
    ("255,255,255", "200,240,255", TEAL),
    ("255,255,255", "190,220,255", "120,160,255"),
    ("220,255,248", "120,235,210", TEAL),
]


# ---- stats ------------------------------------------------------------------
# Everything is account-wide (every repo the account touched: personal, every
# org), never scoped to one organization. What the token can see bounds it:
# the Actions default token only sees public repos, GH_STATS_TOKEN sees private.
ACTIVITY_WEEKS = 13   # ~3 months for the charts

STATS_QUERY = """
query($login: String!, $reviewed: String!) {
  user(login: $login) {
    pullRequests { totalCount }
    merged: pullRequests(states: MERGED) { totalCount }
    contributionsCollection {
      totalCommitContributions
      contributionCalendar { totalContributions }
    }
  }
  reviewed: search(query: $reviewed, type: ISSUE, first: 1) { issueCount }
}"""

PRS_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { createdAt mergedAt repository { name } } }
  }
}"""


def gql(query: str, variables: dict) -> dict:
    token = os.environ.get("GH_STATS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": query, "variables": variables}).encode(),
            headers={"Authorization": f"bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    else:
        args = ["gh", "api", "graphql", "-f", f"query={query}"]
        for k, v in variables.items():
            if v is not None:
                args += ["-f", f"{k}={v}"]
        data = json.loads(subprocess.run(args, check=True, capture_output=True, text=True).stdout)
    if data.get("errors"):
        raise SystemExit(f"GitHub API error: {data['errors']}")
    return data["data"]


def fetch_stats() -> list[tuple[str, str]]:
    d = gql(STATS_QUERY, {"login": USER, "reviewed": f"type:pr reviewed-by:{USER}"})
    u, c = d["user"], d["user"]["contributionsCollection"]
    return [
        (f"{u['pullRequests']['totalCount']:,}", "Pull Requests"),
        (f"{u['merged']['totalCount']:,}", "Merged PRs"),
        (f"{d['reviewed']['issueCount']:,}", "Reviewed PRs"),
        (f"{c['totalCommitContributions']:,}", "Commits (1y)"),
        (f"{c['contributionCalendar']['totalContributions']:,}", "Contributions (1y)"),
    ]


def fetch_recent_prs() -> list[dict]:
    """The account's PRs created or merged in the last ACTIVITY_WEEKS weeks, any repo."""
    import datetime as dt
    since = (dt.date.today() - dt.timedelta(weeks=ACTIVITY_WEEKS)).isoformat()
    q, after, prs = f"author:{USER} is:pr updated:>={since}", None, []
    while True:
        s = gql(PRS_QUERY, {"q": q, "after": after})["search"]
        prs += [n for n in s["nodes"] if n]
        if not s["pageInfo"]["hasNextPage"]:
            return prs
        after = s["pageInfo"]["endCursor"]


SKILLS_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { repository {
      languages(first: 10, orderBy: {field: SIZE, direction: DESC}) { totalSize edges { size node { name } } }
    } } }
  }
}"""
# GitHub's language names -> what the profile calls them; None = leave out.
SKILL_NAMES = {"HCL": "Terraform", "Dockerfile": "Docker", "Go Template": "Go",
               "Makefile": None, "Smarty": None}
SKILLS_TOP = 7


def fetch_skills() -> list[tuple[str, float]]:
    """What the account uses most, from code: every PR in the last year (any
    repo, any owner) adds its repo's language mix, so a repo with 55 PRs weighs
    55x one with a single PR. Returns (skill, % share), largest first."""
    import collections
    import datetime as dt
    since = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    q, after, score = f"author:{USER} is:pr created:>={since}", None, collections.Counter()
    while True:
        s = gql(SKILLS_QUERY, {"q": q, "after": after})["search"]
        for n in s["nodes"]:
            langs = (n or {}).get("repository", {}).get("languages") or {}
            total = langs.get("totalSize") or 0
            for e in langs.get("edges", []) if total else []:
                name = SKILL_NAMES.get(e["node"]["name"], e["node"]["name"])
                if name:
                    score[name] += e["size"] / total
        if not s["pageInfo"]["hasNextPage"]:
            break
        after = s["pageInfo"]["endCursor"]
    top = score.most_common(SKILLS_TOP)
    total = sum(v for _, v in top) or 1
    return [(name, v / total * 100) for name, v in top]


# Charts are drawn with matplotlib in the profile's palette and pixel font, and
# returned as SVG text (glyphs as paths, so no font is needed to display them).
# Fixed hash salt + no date metadata keep the output byte-identical for
# identical data, so the hourly workflow only commits when activity changed.
MUTED = "#8b98a5"


def _figure(width: int, height: int):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    font_manager.fontManager.addfont(str(ASSETS / "fonts" / "PixelOperator.ttf"))
    plt.rcParams.update({"font.family": "Pixel Operator", "font.size": 14,
                         "svg.hashsalt": "profile", "svg.fonttype": "path"})
    fig, ax = plt.subplots(figsize=(width / 100, height / 100), dpi=100)
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    ax.tick_params(colors=MUTED, length=0, labelsize=13)
    ax.grid(axis="y", color="#ffffff", alpha=0.07)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#30363d")
    return plt, fig, ax


def _svg(plt, fig) -> str:
    import io
    fig.tight_layout(pad=0.4)
    buf = io.StringIO()
    fig.savefig(buf, format="svg", transparent=True, metadata={"Date": None})
    plt.close(fig)
    return buf.getvalue()


def _teal():
    return tuple(int(v) / 255 for v in TEAL.split(","))


def prs_per_week(prs: list[dict], width: int, height: int) -> str:
    """PRs opened vs merged per week over the last ACTIVITY_WEEKS weeks, any repo."""
    import datetime as dt

    import matplotlib.dates as mdates

    today = dt.date.today()
    this_monday = today - dt.timedelta(days=today.weekday())
    weeks = [this_monday - dt.timedelta(weeks=i) for i in range(ACTIVITY_WEEKS - 1, -1, -1)]
    opened, merged = [0] * len(weeks), [0] * len(weeks)

    def bucket(ts: str):
        d = dt.date.fromisoformat(ts[:10])
        i = (d - weeks[0]).days // 7
        return i if 0 <= i < len(weeks) else None

    for pr in prs:
        if (i := bucket(pr["createdAt"])) is not None:
            opened[i] += 1
        if pr["mergedAt"] and (i := bucket(pr["mergedAt"])) is not None:
            merged[i] += 1

    plt, fig, ax = _figure(width, height)
    ax.fill_between(weeks, opened, color=_teal(), alpha=0.15, linewidth=0)
    ax.plot(weeks, opened, color=_teal(), linewidth=2, marker="o", markersize=3.5, label="Opened")
    ax.plot(weeks, merged, color="#ffffff", linewidth=1.6, marker="o", markersize=3, label="Merged",
            linestyle=(0, (4, 2)))
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    top = max(opened + merged)
    ax.set_ylim(bottom=0, top=(top * 1.3 if top else 1))
    ax.margins(x=0.02)
    leg = ax.legend(loc="upper left", frameon=False, fontsize=13, handlelength=1.6)
    for text in leg.get_texts():
        text.set_color("#dde3ea")
    return _svg(plt, fig)




def skills_radar(skills: list[tuple[str, float]], width: int, height: int) -> str:
    """Radar of the most-used skills, each axis labelled with its share."""
    import math

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    _figure(1, 1)[0].close("all")                     # register font + rcParams
    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100)
    fig.patch.set_alpha(0)
    ax = fig.add_subplot(projection="polar")
    ax.set_facecolor("none")
    if not skills:
        return _svg(plt, fig)
    names = [n for n, _ in skills]
    vals = [v for _, v in skills]
    angles = [i / len(vals) * 2 * math.pi for i in range(len(vals))]
    ax.set_theta_offset(math.pi / 2)
    ax.set_theta_direction(-1)
    top = max(vals)
    ax.set_ylim(0, top * 1.08)
    ax.plot(angles + angles[:1], vals + vals[:1], color=_teal(), linewidth=2)
    ax.fill(angles + angles[:1], vals + vals[:1], color=_teal(), alpha=0.22)
    ax.plot(angles, vals, "o", color="#ffffff", markersize=4)
    ax.set_xticks(angles)
    ax.set_xticklabels([f"{n}\n{v:.0f}%" for n, v in skills], color="#dde3ea", fontsize=13)
    ax.tick_params(axis="x", pad=10)
    ax.set_yticks([top * f for f in (0.25, 0.5, 0.75, 1.0)])
    ax.set_yticklabels([])
    ax.grid(color="#ffffff", alpha=0.10)
    ax.spines["polar"].set_color("#30363d")
    return _svg(plt, fig)


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


def build(stats: list[tuple[str, str]], prs: list[dict], skills: list[tuple[str, float]]) -> str:
    rnd = random.Random(SEED)
    css, defs, sky, front = [], [], [], []

    # Fonts: regular + bold, embedded.
    fonts = ASSETS / "fonts"
    css.append(
        "@font-face{font-family:PixelOp;font-weight:400;"
        f"src:url(data:font/ttf;base64,{b64(fonts / 'PixelOperator.ttf')}) format('truetype')}}"
        "@font-face{font-family:PixelOp;font-weight:700;"
        f"src:url(data:font/ttf;base64,{b64(fonts / 'PixelOperator-Bold.ttf')}) format('truetype')}}"
        "text{font-family:PixelOp,monospace;-webkit-font-smoothing:none;font-smooth:never}"
    )

    defs.append('<radialGradient id="head"><stop offset="0" stop-color="#fff"/>'
                '<stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>')
    for p, (a, b, c) in enumerate(PALETTES):
        defs.append(
            f'<linearGradient id="trail{p}" x1="1" y1="0" x2="0" y2="0">'
            f'<stop offset="0" stop-color="rgb({a})"/>'
            f'<stop offset="0.25" stop-color="rgb({b})" stop-opacity="0.7"/>'
            f'<stop offset="0.6" stop-color="rgb({c})" stop-opacity="0.25"/>'
            f'<stop offset="1" stop-color="rgb({c})" stop-opacity="0"/></linearGradient>')
    defs.append('<filter id="shadow" x="-10%" y="-40%" width="120%" height="180%">'
                f'<feDropShadow dx="0" dy="0" stdDeviation="5" flood-color="{BG}" flood-opacity="0.95"/>'
                '</filter>')

    # Stars: twinkle + parallax drift, drawn twice one width apart for a seamless loop.
    css.append("@keyframes tw{0%,100%{opacity:var(--o)}50%{opacity:calc(var(--o)*0.15)}}")
    for k in range(TWINKLE_CLASSES):
        css.append(f".tw{k}{{animation:tw {2 + k}s ease-in-out infinite}}")
    css.append(f"@keyframes drift{{from{{transform:translateX(0)}}to{{transform:translateX(-{W}px)}}}}")
    for li, L in enumerate(LAYERS):
        stars = [(rnd.uniform(0, W), rnd.uniform(0, H), rnd.uniform(L["min_r"], L["max_r"]),
                  rnd.uniform(L["min_o"], L["max_o"]), rnd.randrange(TWINKLE_CLASSES), rnd.uniform(0, 6))
                 for _ in range(L["count"])]

        def draw(dx, stars=stars):
            return "".join(
                f'<circle cx="{x + dx:.1f}" cy="{y:.1f}" r="{r:.2f}" fill="#fff" class="tw{t}" '
                f'style="--o:{o:.2f};opacity:{o:.2f};animation-delay:-{d:.2f}s"/>'
                for x, y, r, o, t, d in stars)

        css.append(f".l{li}{{animation:drift {L['drift']}s linear infinite}}")
        sky.append(f'<g class="l{li}">{draw(0)}{draw(W)}</g>')

    # Meteors: evenly spaced launches with jitter, so any frame has some in flight.
    step = METEOR_PERIOD / METEORS
    for i in range(METEORS):
        delay = (i * step + rnd.uniform(0, step * 0.8)) % METEOR_PERIOD
        angle = 28 + rnd.uniform(0, 20)
        x0, y0 = W * (0.15 + rnd.uniform(0, 0.85)), H * rnd.uniform(-0.1, 0.6)
        length, width = 60 + rnd.uniform(0, 170), 0.7 + rnd.uniform(0, 1.4)
        active, dist = rnd.uniform(0.7, 1.6), rnd.uniform(350, 950)
        pal = rnd.choices(range(len(PALETTES)), weights=[6, 2, 2])[0]
        pct = active / METEOR_PERIOD * 100
        sky.append(
            f'<g transform="translate({x0:.1f} {y0:.1f}) rotate({180 - angle:.1f})"><g class="m m{i}">'
            f'<rect x="{-length:.1f}" y="{-width / 2:.2f}" width="{length:.1f}" height="{width:.2f}" '
            f'rx="{width / 2:.2f}" fill="url(#trail{pal})"/>'
            f'<circle r="{3 + width * 1.5:.1f}" fill="url(#head)"/></g></g>')
        css.append(
            f"@keyframes s{i}{{0%{{transform:translateX(0);opacity:0}}"
            f"{pct * 0.08:.2f}%{{opacity:1}}{pct * 0.75:.2f}%{{opacity:1}}"
            f"{pct:.2f}%,100%{{transform:translateX({dist:.0f}px);opacity:0}}}}"
            f".m{i}{{opacity:0;animation:s{i} {METEOR_PERIOD}s linear -{delay:.2f}s infinite}}")

    cx = W / 2

    def header(y: int, label: str) -> str:
        return (f'<text x="{cx}" y="{y}" font-size="32" font-weight="700" fill="rgb({TEAL})">'
                f'&gt; {label} &lt;</text>')

    # Name, bio, Technologies header.
    front.append('<g class="txt" filter="url(#shadow)" text-anchor="middle">')
    front.append(f'<text x="{cx}" y="92" font-size="64" font-weight="700" fill="#fff" '
                 f'letter-spacing="2">{TITLE}<tspan class="cursor" fill="rgb({TEAL})">_</tspan></text>')
    for n, line in enumerate(BIO):
        front.append(f'<text x="{cx}" y="{140 + n * 28}" font-size="24" fill="rgb(200,240,255)">{line}</text>')
    front.append(header(268, SECTION))
    front.append("</g>")

    # Tech logos.
    icon, icon_step, icon_y = 52, 92, 296
    x_start = cx - icon_step * (len(TECH) - 1) / 2
    front.append('<g class="icons" text-anchor="middle">')
    for n, (stem, label) in enumerate(TECH):
        x = x_start + n * icon_step
        front.append(f'<image x="{x - icon / 2:.1f}" y="{icon_y}" width="{icon}" height="{icon}" '
                     f'href="data:image/svg+xml;base64,{b64(ASSETS / "icons" / f"icon_{stem}.svg")}"/>')
        front.append(f'<text x="{x:.1f}" y="{icon_y + icon + 22}" font-size="16" fill="#dde3ea" '
                     f'filter="url(#shadow)">{label}</text>')
    front.append("</g>")

    # Languages, with the two sprites either side.
    front.append('<g class="icons" filter="url(#shadow)" text-anchor="middle">')
    front.append(header(422, LANG_SECTION))
    for n, line in enumerate(LANGUAGES):
        front.append(f'<text x="{cx}" y="{456 + n * 30}" font-size="24" fill="rgb(200,240,255)">{line}</text>')
    front.append("</g>")
    sprite_size, sprite_gap, sprite_y = 130, 150, 392
    for k, (fname, frames, fw, fh, ms, side) in enumerate(SPRITES):
        h = sprite_size * fh / fw
        x = cx - sprite_gap - sprite_size if side == "left" else cx + sprite_gap
        front.append(f'<svg class="icons" x="{x:.0f}" y="{sprite_y + (sprite_size - h):.0f}" '
                     f'width="{sprite_size}" height="{h:.0f}" viewBox="0 0 {fw} {fh}">'
                     f'<image class="spr{k}" width="{fw * frames}" height="{fh}" style="image-rendering:pixelated" '
                     f'href="data:image/png;base64,{b64(ASSETS / "sprites" / fname)}"/></svg>')
        css.append(f"@keyframes spr{k}{{to{{transform:translateX(-{fw * frames}px)}}}}"
                   f".spr{k}{{animation:spr{k} {frames * ms / 1000}s steps({frames}) infinite}}")

    # Stats: big teal numbers, small labels under, one row like the logos.
    stat_step, stat_y = 180, 618
    s_start = cx - stat_step * (len(stats) - 1) / 2
    front.append('<g class="stats" filter="url(#shadow)" text-anchor="middle">')
    front.append(header(570, STATS_SECTION))
    for n, (value, label) in enumerate(stats):
        x = s_start + n * stat_step
        front.append(f'<text x="{x:.1f}" y="{stat_y}" font-size="40" font-weight="700" '
                     f'fill="rgb({TEAL})">{value}</text>')
        front.append(f'<text x="{x:.1f}" y="{stat_y + 26}" font-size="16" fill="#dde3ea">{label}</text>')
    front.append("</g>")

    # Under the numbers: left column = PRs opened/merged per week, skills radar
    # under it; right column = dancing Rick, centred on both. Charts are drawn at
    # their base size and shown CHART_ZOOM bigger (text included), and revealed
    # left-to-right on load.
    def chart(k: int, svg_text: str, x: float, y: float, w: float, h: float) -> None:
        data = base64.b64encode(svg_text.encode()).decode()
        defs.append(f'<clipPath id="reveal{k}"><rect class="wipe" x="{x:.0f}" y="{y:.0f}" '
                    f'width="{w:.0f}" height="{h:.0f}"/></clipPath>')
        front.append(f'<g clip-path="url(#reveal{k})"><image x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" '
                     f'height="{h:.0f}" href="data:image/svg+xml;base64,{data}"/></g>')

    z, margin, gap = CHART_ZOOM, 25, 15
    col_y, line_w, line_h, radar_w, radar_h = 672, 470, 210, 470, 290
    chart(0, prs_per_week(prs, line_w, line_h), margin, col_y, line_w * z, line_h * z)
    radar_y = col_y + line_h * z + gap
    chart(1, skills_radar(skills, radar_w, radar_h), margin, radar_y, radar_w * z, radar_h * z)
    css.append("@keyframes wipe{from{transform:scaleX(0)}to{transform:scaleX(1)}}"
               ".wipe{transform-box:fill-box;transform-origin:left;animation:wipe 2s ease-out 1.2s both}")

    rick_file, rick_frames, rick_fw, rick_fh, rick_ms = RICK
    col_h = line_h * z + gap + radar_h * z
    right_x = margin + line_w * z + gap
    x = right_x + (W - margin - right_x - rick_fw) / 2
    row_y, row_h = col_y, col_h
    front.append(f'<svg class="stats" x="{x:.0f}" y="{row_y + (row_h - rick_fh) / 2:.0f}" width="{rick_fw}" '
                 f'height="{rick_fh}" viewBox="0 0 {rick_fw} {rick_fh}"><image class="rick" '
                 f'width="{rick_fw * rick_frames}" height="{rick_fh}" '
                 f'href="data:image/png;base64,{b64(ASSETS / "sprites" / rick_file)}"/></svg>')
    css.append(f"@keyframes rick{{to{{transform:translateX(-{rick_fw * rick_frames}px)}}}}"
               f".rick{{animation:rick {rick_frames * rick_ms / 1000}s steps({rick_frames}) infinite}}")

    css.append("@keyframes fadein{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}"
               ".txt{animation:fadein 1.4s ease-out both}"
               ".icons{animation:fadein 1.4s ease-out .5s both}"
               ".stats{animation:fadein 1.4s ease-out .9s both}"
               "@keyframes blink{50%{opacity:0}}.cursor{animation:blink 1s steps(1) infinite}")
    # Reduced motion: everything stops, meteors hidden, content simply shown.
    css.append("@media (prefers-reduced-motion: reduce){*{animation:none!important}.m{opacity:0!important}}")

    aria = (f"{TITLE}. {' '.join(BIO)} {SECTION}: {', '.join(l for _, l in TECH)}. "
            f"{LANG_SECTION}: {', '.join(LANGUAGES)}. "
            f"{STATS_SECTION}: {', '.join(f'{v} {l}' for v, l in stats)}. "
            f"Most used: {', '.join(f'{n} {v:.0f}%' for n, v in skills)}.")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'role="img" aria-label="{aria}"><style>{"".join(css)}</style><defs>{"".join(defs)}</defs>'
            f'<rect width="{W}" height="{H}" fill="{BG}"/>{"".join(sky)}{"".join(front)}</svg>')


if __name__ == "__main__":
    stats = fetch_stats()
    skills = fetch_skills()
    svg = build(stats, fetch_recent_prs(), skills)
    out = ROOT / "profile.svg"
    out.write_text(svg)
    print("ok", out, f"{len(svg) / 1024:.0f} KB", stats, [(n, round(v)) for n, v in skills])
