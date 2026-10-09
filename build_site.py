#!/usr/bin/env python3
"""
Release Tower: render data/report-<slug>.json into static HTML under site/.
index.html = first artist in releases.json; each artist also gets site/<slug>.html.
Pure Python string rendering, no JS framework, no runtime fetches, so Vercel just serves files.
"""
import json, html, pathlib, datetime

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE / "site"
SITE.mkdir(exist_ok=True)
TODAY = datetime.date.today()


def e(s):
    return html.escape(str(s if s is not None else ""))


def fmt(n, plus=False):
    if n is None:
        return "n/a"
    n = float(n)
    sign = "+" if plus and n > 0 else ""
    if abs(n) >= 1e6:
        return f"{sign}{n/1e6:.1f}M"
    if abs(n) >= 1e3:
        return f"{sign}{n/1e3:.0f}k"
    return f"{sign}{n:.0f}"


def nice_date(s):
    return datetime.date.fromisoformat(s[:10]).strftime("%b %-d") if s else ""


def spark(series, w=120, h=28):
    pts = [v for v in series if v is not None]
    if not pts:
        return ""
    if len(pts) == 1:
        return f'<svg class="spark" viewBox="0 0 {w} {h}" aria-hidden="true"><circle cx="{w-4}" cy="{h/2}" r="3" fill="currentColor"/></svg>'
    lo, hi = min(pts), max(pts)
    rng = (hi - lo) or 1
    step = (w - 8) / (len(pts) - 1)
    coords = [(4 + i * step, h - 4 - (v - lo) / rng * (h - 8)) for i, v in enumerate(pts)]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords))
    area = path + f" L{coords[-1][0]:.1f},{h} L{coords[0][0]:.1f},{h} Z"
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" aria-hidden="true">'
            f'<path d="{area}" class="spark-area"/><path d="{path}" class="spark-line"/>'
            f'<circle cx="{coords[-1][0]:.1f}" cy="{coords[-1][1]:.1f}" r="2.5" fill="currentColor"/></svg>')


TREND_LABEL = {"decaying": ("Decaying", "bad"), "softening": ("Softening", "warn"), "growing": ("Growing", "good"),
               "steady": ("Steady", "neutral"), "baseline": ("Baseline", "muted")}


def render(rep, nav):
    r = rep
    b = r["brief"]
    days = b["days_to_release"]
    rel = r["release"]
    if days > 0:
        phase, count = "Pre-release", f"{days} days out"
    elif days == 0:
        phase, count = "Release day", "Today"
    elif days > -8:
        phase, count = "Release week", f"Day {-days + 1}"
    else:
        phase, count = "Post-release", f"{-days} days old"

    # ---- brief
    brief_html = "".join(f"<p>{e(p)}</p>" for p in b["paragraphs"])
    actions_html = "".join(f"<li>{e(a)}</li>" for a in b["actions"])

    # ---- tracks
    rows = []
    for t in r["tracks"]:
        lab, cls = TREND_LABEL.get(t["trend"], ("", "muted"))
        wow = t.get("week_over_week_pct")
        trend_txt = f"{lab}" + (f" {wow:+.0f}% wk/wk" if wow is not None else (f" pop {t['pop_change']:+d}" if t.get("pop_change") else ""))
        rows.append(
            f"<tr class='kind-{t['kind'].replace(' ', '-')}'>"
            f"<td><div class='tname'>{e(t['name'])}</div><div class='tsub'>{e(t['kind'])} · {e(t['album'])} · {nice_date(t['release_date'])}</div></td>"
            f"<td class='num'>{'<span class=muted>under 1k</span>' if not t['plays'] else fmt(t['plays'])}</td>"
            f"<td class='num'>{'<span class=muted>new</span>' if not t['plays'] else fmt(t['avg_per_day'])}</td>"
            f"<td class='num'>{fmt(t['delta_1d'], True) if t['delta_1d'] is not None else '<span class=muted>day 1</span>'}</td>"
            f"<td class='num'>{t['popularity'] if t['popularity'] is not None else 'n/a'}</td>"
            f"<td><span class='pill {cls}'>{e(trend_txt)}</span></td></tr>")
    tracks_html = "".join(rows)

    # ---- audience
    aud = []
    for m in r["audience"]:
        if m["value"] is None:
            continue
        d1 = m["delta_1d"]
        sub = (f"{fmt(d1, True)} per day, latest" if d1 is not None else f"first read {r['snapshot_dates'][0]}")
        aud.append(f"<div class='card metric'><div class='mlabel'>{e(m['label'])}</div>"
                   f"<div class='mvalue'>{fmt(m['value'])}</div><div class='msub'>{e(sub)}</div>{spark(m['series'])}</div>")
    aud_html = "".join(aud)

    # ---- cities vs tour
    cities = []
    for c in r["cities"]:
        tag = "<span class='pill good'>routed</span>" if c["tour_date"] else "<span class='pill warn'>no date</span>"
        cities.append(f"<tr><td>{e(c['city'])}<span class='tsub'> {e(c['region'] or c['country'])}</span></td>"
                      f"<td class='num'>{fmt(c['listeners'])}</td><td>{tag if r['tour'] else ''}</td></tr>")
    cities_html = "".join(cities)

    # ---- shortform
    sf = r.get("shortform")
    sf_html = ""
    if sf:
        vids = "".join(
            f"<tr><td>{nice_date(v['posted'])}</td><td class='vtext'><a href='{e(v['url'])}' rel='noopener'>{e(v['text'][:90]) or 'video'}</a></td>"
            f"<td class='num'>{fmt(v['plays'])}</td><td class='num'>{fmt(v['likes'])}</td></tr>" for v in sf["recent"])
        pinned = ", ".join(f"{fmt(v['plays'])} views" for v in sf["pinned_or_older"])
        sf_html = f"""
        <section class="card">
          <h2>Short-form pulse <span class="hsub">TikTok, last 21 days</span></h2>
          <div class="kv"><span>Followers</span><b>{fmt(sf['followers'])}</b><span>Median views, recent posts</span><b>{fmt(sf['recent_median_plays'])}</b><span>Pinned posts</span><b>{e(pinned) or 'none'}</b></div>
          <div class="tablewrap"><table><thead><tr><th>Posted</th><th>Post</th><th class="num">Views</th><th class="num">Likes</th></tr></thead><tbody>{vids}</tbody></table></div>
        </section>"""

    # ---- checklist
    ck = []
    for c in r["checklist"]:
        cls = {"done": "good", "due now": "warn", "upcoming": "muted"}[c["state"]]
        ck.append(f"<tr><td>{e(c['item'])}<div class='tsub'>{e(c['evidence'])}</div></td><td>{nice_date(c['due'])}</td>"
                  f"<td><span class='pill {cls}'>{e(c['state'])}</span></td></tr>")
    ck_html = "".join(ck)

    # ---- tour
    tr = []
    for t in r["tour"]:
        dt = datetime.date.fromisoformat(t["date"])
        soon = "soon" if 0 <= (dt - TODAY).days <= 14 else ""
        pill = "<span class='pill good'>sold out</span>" if t["sold_out"] else "<span class='pill muted'>on sale</span>"
        tr.append(f"<tr class='{soon}'><td>{dt.strftime('%a %b %-d')}</td><td>{e(t['city'])}</td><td class='tsub'>{e(t['venue'])}</td>"
                  f"<td>{pill}</td></tr>")
    tour_html = "".join(tr)

    sold = sum(1 for t in r["tour"] if t["sold_out"])
    nav_html = " ".join(f"<a href='{e(h)}' class='{'on' if h == nav['current'] else ''}'>{e(n)}</a>" for n, h in nav["links"])
    sp_url = f"https://open.spotify.com/artist/{r['spotify_artist_id']}"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{e(r['artist'])} Release Tower</title>
<meta name="description" content="Daily release monitor for {e(r['artist'])}: what is working, what is fading, what to do this week.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;700;800&family=Source+Sans+3:wght@400;600&family=JetBrains+Mono:wght@500&display=swap">
<style>
:root{{--bg:#F3F5F7;--surface:#FFFFFF;--ink:#14202B;--muted:#5C6B78;--line:#DDE3E8;--accent:#C96A1B;--good:#1F7A5C;--bad:#B23A3A;--warn:#C96A1B;--chip:#EEF1F4;--shadow:0 1px 2px rgba(20,32,43,.06)}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#0F161D;--surface:#161F29;--ink:#E7EDF2;--muted:#94A3B1;--line:#26313C;--accent:#E9944A;--good:#3FB58F;--bad:#E06B6B;--warn:#E9944A;--chip:#1E2A36;--shadow:none}}}}
:root[data-theme="dark"]{{--bg:#0F161D;--surface:#161F29;--ink:#E7EDF2;--muted:#94A3B1;--line:#26313C;--accent:#E9944A;--good:#3FB58F;--bad:#E06B6B;--warn:#E9944A;--chip:#1E2A36;--shadow:none}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Source Sans 3",system-ui,sans-serif;font-size:16px;line-height:1.5}}
a{{color:inherit}}
.wrap{{max-width:1100px;margin:0 auto;padding:24px 16px 64px;overflow-x:hidden}}
nav{{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:20px;font-size:14px}}
nav a{{text-decoration:none;padding:6px 12px;border-radius:999px;background:var(--chip);color:var(--muted)}}
nav a.on{{background:var(--ink);color:var(--bg)}}
header{{display:grid;grid-template-columns:1fr auto;gap:16px;align-items:end;margin-bottom:24px}}
.eyebrow{{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600}}
h1{{overflow-wrap:anywhere;font-family:Archivo,sans-serif;font-weight:800;font-size:clamp(28px,4vw,42px);line-height:1.05;margin:4px 0 6px;text-wrap:balance}}
.sub{{color:var(--muted)}}
.count{{text-align:right}}
.count .big{{font-family:Archivo,sans-serif;font-weight:700;font-size:clamp(22px,3vw,32px);line-height:1}}
.count .phase{{color:var(--accent);font-weight:600;font-size:13px;letter-spacing:.06em;text-transform:uppercase}}
.grid{{display:grid;gap:16px;grid-template-columns:repeat(12,minmax(0,1fr))}}
.card{{min-width:0;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:20px;box-shadow:var(--shadow)}}
.span12{{grid-column:span 12}}.span8{{grid-column:span 8}}.span6{{grid-column:span 6}}.span4{{grid-column:span 4}}
@media (max-width:820px){{.span8,.span6,.span4{{grid-column:span 12}}header{{grid-template-columns:1fr}}.count{{text-align:left}}}}
h2{{font-family:Archivo,sans-serif;font-weight:700;font-size:18px;margin:0 0 12px}}
.hsub{{font-family:"Source Sans 3",sans-serif;font-weight:400;font-size:14px;color:var(--muted);margin-left:8px}}
.brief p{{margin:0 0 12px;max-width:72ch}}
.brief p:first-child{{font-size:18px;font-weight:600}}
.actions{{margin:0;padding-left:0;list-style:none;counter-reset:a}}
.actions li{{counter-increment:a;position:relative;padding:10px 0 10px 40px;border-top:1px solid var(--line)}}
.actions li::before{{content:counter(a);position:absolute;left:0;top:10px;width:26px;height:26px;border-radius:50%;background:var(--accent);color:#fff;font-family:"JetBrains Mono",monospace;font-size:13px;display:grid;place-items:center}}
.tablewrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:15px}}
th{{text-align:left;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600;padding:6px 8px;border-bottom:1px solid var(--line)}}
td{{padding:9px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
tr:last-child td{{border-bottom:0}}
.num{{text-align:right;font-family:"JetBrains Mono",monospace;font-size:14px;font-variant-numeric:tabular-nums;white-space:nowrap}}
th.num{{text-align:right}}
.tname{{font-weight:600}}.tsub{{font-size:13px;color:var(--muted)}}
.pill{{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap;background:var(--chip);color:var(--muted)}}
.pill.good{{background:color-mix(in srgb,var(--good) 14%,transparent);color:var(--good)}}
.pill.bad{{background:color-mix(in srgb,var(--bad) 14%,transparent);color:var(--bad)}}
.pill.warn{{background:color-mix(in srgb,var(--warn) 16%,transparent);color:var(--warn)}}
.muted{{color:var(--muted)}}
.metrics{{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}}
.metric .mlabel{{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:600}}
.metric .mvalue{{font-family:Archivo,sans-serif;font-weight:700;font-size:28px;line-height:1.1;margin:4px 0 2px;font-variant-numeric:tabular-nums}}
.metric .msub{{font-size:13px;color:var(--muted)}}
.spark{{display:block;width:100%;height:28px;margin-top:8px;color:var(--accent)}}
.spark-line{{fill:none;stroke:currentColor;stroke-width:1.5}}.spark-area{{fill:currentColor;opacity:.12}}
.kv{{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:14px;margin-bottom:12px}}
.kv span{{color:var(--muted)}}.kv b{{font-variant-numeric:tabular-nums}}
tr.soon td{{background:color-mix(in srgb,var(--accent) 8%,transparent)}}
.vtext a{{text-decoration:none;color:var(--ink)}}.vtext a:hover{{text-decoration:underline}}
footer{{margin-top:28px;font-size:13px;color:var(--muted);max-width:80ch}}
footer p{{margin:0 0 8px}}
:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
</style>
</head>
<body>
<div class="wrap">
<nav>{nav_html}</nav>
<header>
  <div>
    <div class="eyebrow">Release Tower · {e(r['label'])}</div>
    <h1>{e(r['artist'])}: {e(rel['title'])}</h1>
    <div class="sub">{e(rel['type'])} · {datetime.date.fromisoformat(rel['date']).strftime('%B %-d, %Y')} · <a href="{sp_url}" rel="noopener">Spotify</a>{' · <a href="' + e(rel['presave_url']) + '" rel="noopener">Pre-save</a>' if rel.get('presave_url') else ''} · updated {e(r['generated_at'][:16].replace('T', ' '))}</div>
  </div>
  <div class="count"><div class="phase">{phase}</div><div class="big">{count}</div><div class="sub">day {len(r['snapshot_dates'])} of tracking</div></div>
</header>

<div class="grid">
  <section class="card span8 brief">
    <h2>This week's read <span class="hsub">written from the numbers below, {e(b['generated'])}</span></h2>
    {brief_html}
  </section>
  <section class="card span4">
    <h2>Do this week</h2>
    <ol class="actions">{actions_html}</ol>
  </section>

  <section class="card span12">
    {'<p class="pill warn" style="margin:0 0 10px">Play counts did not update on the latest read; showing the last good values. Rates are paused until the next successful read.</p>' if r.get('plays_stale') else ''}
    <h2>Momentum <span class="hsub">Spotify play counts from the public pages. "Per day" is the rate since the previous read{(' (' + str(r['hours_since_prev']) + 'h ago)') if r.get('hours_since_prev') else ''}.</span></h2>
    <div class="tablewrap"><table>
      <thead><tr><th>Track</th><th class="num">Plays</th><th class="num">Avg / day</th><th class="num">Per day, latest</th><th class="num">Popularity</th><th>Trend</th></tr></thead>
      <tbody>{tracks_html}</tbody>
    </table></div>
  </section>

  <section class="card span12">
    <h2>Audience</h2>
    <div class="metrics">{aud_html}</div>
  </section>

  <section class="card span6">
    <h2>Where the listeners are <span class="hsub">vs. tour routing</span></h2>
    <div class="tablewrap"><table><thead><tr><th>City</th><th class="num">Monthly listeners</th><th></th></tr></thead><tbody>{cities_html}</tbody></table></div>
  </section>

  <section class="card span6">
    <h2>Release checklist <span class="hsub">observable from outside; internal items flagged</span></h2>
    <div class="tablewrap"><table><thead><tr><th>Item</th><th>Due</th><th>State</th></tr></thead><tbody>{ck_html or '<tr><td class=muted colspan=3>No checklist for this release.</td></tr>'}</tbody></table></div>
  </section>

  {sf_html.replace('<section class="card">', '<section class="card span6">') if sf_html else ''}

  {'<section class="card span6"><h2>Tour <span class="hsub">' + str(sold) + ' of ' + str(len(r['tour'])) + ' sold out</span></h2><div class="tablewrap"><table><tbody>' + tour_html + '</tbody></table></div></section>' if r['tour'] else ''}
</div>

<footer>
  <p><b>Method.</b> {e(b['notes'])}</p>
  <p><b>Sources.</b> Spotify Web API (followers, popularity, discography) · public Spotify artist and album pages (monthly listeners, top cities, play counts) · public Instagram and TikTok profiles. Collected once a day by a script; the brief is generated from rules over the numbers, not written by a language model.</p>
  {'<p><b>Catalog note.</b> ' + e(r['catalog_note']) + '</p>' if r.get('catalog_note') else ''}
  <p>Built by Chris Drago. <a href="https://chris-drago.vercel.app" rel="noopener">chris-drago.vercel.app</a></p>
</footer>
</div>
</body>
</html>"""


def main():
    cfg = json.load(open(HERE / "releases.json"))
    reports = []
    for a in cfg["artists"]:
        p = HERE / "data" / f"report-{a['slug']}.json"
        if p.exists():
            reports.append(json.load(open(p)))
    links = [(r["artist"], "index.html" if i == 0 else f"{r['slug']}.html") for i, r in enumerate(reports)]
    for i, r in enumerate(reports):
        fn = "index.html" if i == 0 else f"{r['slug']}.html"
        out = render(r, {"links": links, "current": fn})
        (SITE / fn).write_text(out)
        print("wrote", SITE / fn, len(out), "bytes")


if __name__ == "__main__":
    main()
