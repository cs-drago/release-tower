#!/usr/bin/env python3
"""
Release Tower: turn daily snapshots into a report + plain-English brief per artist.

Reads  data/snapshots/<slug>/*.json  (from collect.py)
Writes data/report-<slug>.json

No LLM, no network. Every sentence in the brief is generated from a rule over the numbers,
so it says the same thing for the same data and never invents a fact.
"""
import json, glob, datetime, pathlib, statistics

HERE = pathlib.Path(__file__).resolve().parent
TODAY = datetime.date.today()


def d(s):
    return datetime.date.fromisoformat(s[:10])


def fmt(n):
    if n is None:
        return "n/a"
    n = float(n)
    if abs(n) >= 1e6:
        return f"{n/1e6:.1f}M"
    if abs(n) >= 1e3:
        return f"{n/1e3:.0f}k"
    return f"{n:.0f}"


def pct(a, b):
    if not a or not b:
        return None
    return (a - b) / b * 100


def hours_between(a, b):
    """Elapsed hours between two snapshots (collected_at), falling back to date difference."""
    try:
        ta = datetime.datetime.fromisoformat(a["collected_at"]); tb = datetime.datetime.fromisoformat(b["collected_at"])
        return max((tb - ta).total_seconds() / 3600, 1)
    except Exception:
        return max((d(b["date"]) - d(a["date"])).days * 24, 24)


def per_day(cur, prev, hours):
    """Delta normalized to a 24-hour rate, so a skipped day doesn't look like a spike."""
    if cur is None or prev is None:
        return None
    return round((cur - prev) * 24 / hours)


def load_snaps(slug):
    files = sorted(glob.glob(str(HERE / "data" / "snapshots" / slug / "*.json")))
    return [json.load(open(f)) for f in files]


def repair_snaps(snaps):
    """If a snapshot has no play counts (scraper returned None), carry forward the last known good
    value per track and mark the snapshot stale so deltas are skipped, never negative."""
    last_good = {}
    for s in snaps:
        good = any(t.get("plays") for t in s["track_plays"].values())
        s["plays_stale"] = not good
        for tid, t in s["track_plays"].items():
            if t.get("plays") is None:
                t["plays"] = last_good.get(tid)
            else:
                last_good[tid] = t["plays"]
    return snaps


def last_good_snap(snaps, before_idx):
    for i in range(before_idx - 1, -1, -1):
        if not snaps[i].get("plays_stale"):
            return snaps[i]
    return None


def track_rows(cfg, snaps):
    snaps = repair_snaps(snaps)
    latest, first = snaps[-1], snaps[0]
    prev = None if latest.get("plays_stale") else last_good_snap(snaps, len(snaps) - 1)
    week = snaps[-8] if len(snaps) > 7 else None
    single_ids = {s["track_id"]: s for s in cfg["release"]["released_singles"]}
    ep_id = cfg["release"].get("ep_album_id") or latest.get("detected_ep_album_id")
    # Spotify re-lists earlier singles as bonus tracks on later single releases (same recording,
    # same play count, different track id). Keep one row per song: the earliest release.
    def norm(n):
        return (n or "").lower().replace("\u2019", "'").strip()
    seen, versions = {}, {}
    for tid, t in latest["track_plays"].items():
        key = norm(t.get("name"))
        versions.setdefault(key, []).append(tid)
        rel = t.get("album_release") or "9999"
        if key not in seen or rel < (latest["track_plays"][seen[key]].get("album_release") or "9999"):
            seen[key] = tid
    keep = set(seen.values())
    single_names = {norm(s["name"]) for s in cfg["release"]["released_singles"]}
    rows = []
    for tid, t in latest["track_plays"].items():
        if tid not in keep:
            continue
        plays = t.get("plays") or 0
        rel = t.get("album_release") or ""
        days = max((TODAY - d(rel)).days, 1) if rel else None
        vids = versions[norm(t.get("name"))]
        # popularity: Spotify scores each version separately; the canonical one carries the real score
        pop = max((latest["track_popularity"].get(v) for v in vids if latest["track_popularity"].get(v) is not None), default=None)
        pop_first = max((first["track_popularity"].get(v) for v in vids if first["track_popularity"].get(v) is not None), default=None)
        row = {
            "id": tid, "name": t["name"], "album": t.get("album"), "release_date": rel,
            "plays": plays, "days_out": days,
            "avg_per_day": round(plays / days) if days else None,
            "popularity": pop,
            "pop_change": (pop - pop_first) if (pop is not None and pop_first is not None) else None,
            "delta_1d": per_day(plays, prev["track_plays"].get(tid, {}).get("plays"), hours_between(prev, latest)) if prev and tid in prev["track_plays"] else None,
            "delta_7d": (plays - (week["track_plays"].get(tid, {}).get("plays") or 0)) if week and tid in week["track_plays"] else None,
        }
        if tid in single_ids or norm(t.get("name")) in single_names:
            row["kind"] = "campaign single"
        elif ep_id and t.get("album") and cfg["release"]["title"].lower() in (t.get("album") or "").lower():
            row["kind"] = "EP track"
        elif t.get("album_release") and t["album_release"] >= (TODAY - datetime.timedelta(days=120)).isoformat():
            row["kind"] = "recent"
        else:
            row["kind"] = "catalog"
        # decay flag: prefer real 7-day vs prior-7-day comparison when we have 15+ snapshots
        row["trend"] = "baseline"
        if len(snaps) >= 15:
            w1 = plays - (snaps[-8]["track_plays"].get(tid, {}).get("plays") or plays)
            w0 = (snaps[-8]["track_plays"].get(tid, {}).get("plays") or 0) - (snaps[-15]["track_plays"].get(tid, {}).get("plays") or 0)
            change = pct(w1, w0)
            row["week_over_week_pct"] = round(change, 1) if change is not None else None
            if change is not None:
                row["trend"] = "decaying" if change < -30 else "softening" if change < -10 else "growing" if change > 10 else "steady"
        elif row["pop_change"] is not None and len(snaps) >= 3:
            row["trend"] = "decaying" if row["pop_change"] <= -4 else "softening" if row["pop_change"] <= -2 else "growing" if row["pop_change"] >= 2 else "steady"
        rows.append(row)
    # sort: campaign singles by release date desc, then EP, then catalog by plays
    order = {"EP track": 0, "campaign single": 1, "recent": 2, "catalog": 3}
    rows.sort(key=lambda r: (order[r["kind"]], -(r["plays"] or 0)))
    return rows


def audience(snaps):
    latest, first = snaps[-1], snaps[0]
    prev = snaps[-2] if len(snaps) > 1 else None

    def series(path):
        out = []
        for s in snaps:
            v = s
            for k in path:
                v = v.get(k) if isinstance(v, dict) else None
            out.append(v)
        return out

    def metric(label, path):
        vals = series(path)
        cur = vals[-1]
        return {"label": label, "value": cur,
                "delta_1d": per_day(cur, vals[-2], hours_between(prev, latest)) if prev and cur is not None and vals[-2] is not None else None,
                "delta_total": (cur - vals[0]) if cur is not None and vals[0] is not None and len(vals) > 1 else None,
                "series": vals}

    return [
        metric("Spotify monthly listeners", ["spotify", "monthly_listeners"]),
        metric("Spotify followers", ["spotify", "followers"]),
        metric("Spotify artist popularity", ["spotify", "popularity"]),
        metric("Instagram followers", ["instagram", "followers"]),
        metric("TikTok followers", ["tiktok", "fans"]),
    ]


def cities_vs_tour(cfg, latest):
    tour_cities = {t["city"].lower() for t in cfg.get("tour", [])}
    alias = {"new york city": {"new york city", "new york", "brooklyn"}, "los angeles": {"los angeles", "la"},
             "boston": {"boston", "cambridge"}, "dallas": {"dallas", "fort worth"}}
    out = []
    for c in latest["spotify"].get("top_cities", []):
        name = c["city"].lower()
        names = alias.get(name, {name})
        has = any(n in tour_cities for n in names) or any(name in alias.get(tc, set()) for tc in tour_cities)
        out.append({"city": c["city"], "region": c.get("region"), "country": c.get("country"),
                    "listeners": c.get("listeners"), "tour_date": has})
    return out


def checklist(cfg):
    rel = d(cfg["release"]["date"])
    out = []
    for c in cfg.get("checklist", []):
        due = rel - datetime.timedelta(days=c["due_days_before"])
        state = c["status"]
        if state != "done":
            state = "due now" if due <= TODAY else "upcoming"
        out.append({**c, "due": due.isoformat(), "state": state})
    return out


def shortform(latest):
    tt = latest.get("tiktok") or {}
    vids = tt.get("recent") or []
    if not vids:
        return None
    by_date = sorted(vids, key=lambda v: v.get("posted") or "", reverse=True)
    recent = [v for v in by_date if v.get("posted") and d(v["posted"]) >= TODAY - datetime.timedelta(days=21)]
    older = [v for v in by_date if v not in recent]
    med_recent = statistics.median([v["plays"] for v in recent if v.get("plays")]) if recent else None
    return {"followers": tt.get("fans"), "hearts": tt.get("hearts"),
            "recent_count": len(recent), "recent_median_plays": med_recent,
            "pinned_or_older": [v for v in older if (v.get("plays") or 0) > 1_000_000][:3],
            "recent": recent[:8]}


def brief(cfg, snaps, rows, aud, cities, checks, sf):
    """Plain-English weekly brief. Rules over numbers, no adjectives the data can't back."""
    rel = d(cfg["release"]["date"])
    days = (rel - TODAY).days
    title = cfg["release"]["title"]
    kind = cfg["release"]["type"]
    latest = snaps[-1]
    n = len(snaps)
    P = []  # paragraphs
    A = []  # actions

    # Phase
    if days > 0:
        P.append(f"{title} is {days} days out ({rel.strftime('%b %-d')}). "
                 f"{len(cfg['release']['released_singles'])} of {len(cfg['release']['tracklist'])} tracks are already on streaming.")
    elif days == 0:
        new_tracks = [r for r in rows if r["kind"] == "EP track" and not r["plays"]]
        P.append(f"{title} is out today." + (f" The {len(new_tracks)} brand-new tracks do not show play counts yet; Spotify publishes a count once a track passes 1,000 plays, so tomorrow's read is the first real one for them." if new_tracks else ""))
    elif days > -8:
        P.append(f"{title} came out {-days} days ago. This is release week; the numbers below are the first read.")
    else:
        P.append(f"{title} is {-days} days old. The job now is keeping the tracks that are working alive and deciding what the next focus is.")

    ml = latest["spotify"].get("monthly_listeners")
    P[-1] += f" The artist sits at {fmt(ml)} monthly listeners and {fmt(latest['spotify']['followers'])} Spotify followers."

    # Singles read
    singles = [r for r in rows if r["kind"] == "campaign single"]
    if singles:
        newest = min(singles, key=lambda r: (TODAY - d(r["release_date"])).days)
        best = max(singles, key=lambda r: r["avg_per_day"] or 0)
        s = (f"Of the singles, {best['name']} is the one working: {fmt(best['plays'])} plays in {best['days_out']} days, "
             f"about {fmt(best['avg_per_day'])} a day on average, Spotify popularity {best['popularity']}.")
        if newest is not best:
            ratio = (newest["avg_per_day"] or 0) / (best["avg_per_day"] or 1)
            s += (f" The newest single, {newest['name']}, is at {fmt(newest['plays'])} plays after {newest['days_out']} days, "
                  f"roughly {fmt(newest['avg_per_day'])} a day, which is {ratio:.0%} of {best['name']}'s pace.")
            if ratio < 0.4:
                s += " That is a soft lead-in to the release and worth naming out loud rather than hoping it turns."
                A.append(f"Make {best['name']} the anchor in every DSP pitch and ad creative for release week, not {newest['name']}. The data says which one people are choosing.")
        P.append(s)

    # New EP tracks (release week and after)
    ep = [r for r in rows if r["kind"] == "EP track" and r["plays"]]
    if ep and days <= 0:
        ep.sort(key=lambda r: -(r["delta_1d"] or r["avg_per_day"] or 0))
        lead = ep[0]
        rate = lead["delta_1d"] or lead["avg_per_day"]
        beats = [r["name"] for r in singles if (r["delta_1d"] or 0) < (rate or 0)]
        msg = (f"Of the brand-new tracks, {lead['name']} is the one listeners are picking: about {fmt(rate)} plays a day, "
               f"popularity {lead['popularity']} after {-days} days.")
        if beats:
            msg += f" That already outpaces {', '.join(beats)} from the single campaign."
        others = ", ".join(f"{r['name']} ({fmt(r['delta_1d'] or r['avg_per_day'])}/day)" for r in ep[1:])
        if others:
            msg += f" The rest: {others}."
        P.append(msg)
        if beats and days >= -14:
            A.insert(0, f"{lead['name']} is the organic pick off the EP. Get it into the focus-track slot on the artist profile and the DSP follow-up pitches this week, and test it as the next radio or video candidate before the tour starts.")

    # Catalog engines
    cat = [r for r in rows if r["kind"] == "catalog"]
    if cat:
        top = cat[0]
        P.append(f"Catalog is doing real work: {top['name']} still averages {fmt(top['avg_per_day'])} plays a day "
                 f"{top['days_out']} days after release, and the tracked catalog holds {fmt(sum(r['plays'] for r in cat))} plays. "
                 f"A new release lifts catalog if the catalog is easy to find from it.")
        if days > -8:
            A.append(f"Put {top['name']} and the next catalog track on Discovery Mode for release week, and make sure the artist profile pins the new {kind} above them.")

    # Trends (only once we have enough days)
    decaying = [r for r in rows if r["trend"] == "decaying"]
    growing = [r for r in rows if r["trend"] == "growing"]
    if n >= 3:
        if decaying:
            P.append("Decay flags: " + ", ".join(f"{r['name']} ({r.get('week_over_week_pct') or r['pop_change']})" for r in decaying) +
                     ". These are the tracks where a small push now is cheaper than a relaunch later.")
            for r in decaying[:2]:
                A.append(f"{r['name']} is falling. Cheapest intervention first: a UGC push on the hook, then a playlist re-pitch, then paid only if the first two move it.")
        if growing:
            P.append("Growing: " + ", ".join(r["name"] for r in growing) + ".")
    else:
        P.append(f"Trend flags need a week of daily snapshots. This is day {n}; the decay and growth signals switch on automatically as data accumulates.")

    # Cities vs tour
    gaps = [c for c in cities if not c["tour_date"]]
    if cfg.get("tour") and gaps:
        g = gaps[0]
        P.append(f"Audience versus routing: the top listener cities are " +
                 ", ".join(f"{c['city']} ({fmt(c['listeners'])})" for c in cities[:5]) + ". " +
                 (f"{g['city']} is the number one city and has no tour date. " if cities and cities[0] is g else f"{g['city']} has no tour date. ") +
                 "That is either a routing gap or a second-leg market, and either way it is a geo-targeting priority.")
        A.append(f"Run tour-market geo ads in the routed cities from two weeks out, and treat {', '.join(c['city'] for c in gaps[:2])} as the first markets to test for a second leg.")

    # Short-form pulse
    if sf and sf["recent_median_plays"] is not None and sf["pinned_or_older"]:
        big = max(v["plays"] for v in sf["pinned_or_older"])
        P.append(f"Short-form: the last three weeks of TikTok posts are landing around {fmt(sf['recent_median_plays'])} views each, "
                 f"against pinned posts at {fmt(big)}. The account has {fmt(sf['followers'])} followers, so reach is there when the hook is. "
                 f"Release week needs a hook-led post plan, not announcement posts.")
        A.append("Brief the band on three hook-first TikToks for release week that use the strongest 10 seconds of the focus track. Announcement posts are not the format that got them here.")

    # Competitor
    if cfg.get("same_day_releases") and days >= -3:
        P.append("Same day: " + "; ".join(cfg["same_day_releases"]) + ". Expect competition for the same alt-rock editorial slots that week.")

    # Checklist
    due = [c for c in checks if c["state"] == "due now"]
    if due:
        P.append(f"Release ops: {len(due)} checklist items are inside their window and not visible from outside the label: " +
                 "; ".join(c["item"] for c in due[:4]) + ". Worth a two-line confirm with whoever owns them.")

    if not A:
        A.append("Nothing needs a decision this week. Keep the daily snapshot running so the release-week read has a baseline.")

    notes = ("Play counts are cumulative Spotify counts read from the public artist and album pages once a day. "
             "Spotify popularity is Spotify's own 0 to 100 recency-weighted score and moves before play counts do. "
             "Monthly listeners and follower counts come from public profiles. Nothing here uses label-internal data.")
    return {"paragraphs": P, "actions": A[:4], "notes": notes, "days_to_release": days,
            "generated": TODAY.isoformat(), "snapshot_days": n}


def analyze(cfg):
    snaps = load_snaps(cfg["slug"])
    if not snaps:
        return None
    rows = track_rows(cfg, snaps)
    aud = audience(snaps)
    cities = cities_vs_tour(cfg, snaps[-1])
    checks = checklist(cfg)
    sf = shortform(snaps[-1])
    rep = {
        "artist": cfg["name"], "slug": cfg["slug"], "label": cfg["label"],
        "release": cfg["release"], "tour": cfg.get("tour", []), "same_day_releases": cfg.get("same_day_releases", []),
        "catalog_note": cfg.get("catalog_note"),
        "handles": snaps[-1].get("handles", {}), "spotify_artist_id": cfg["spotify_artist_id"],
        "snapshot_dates": [s["date"] for s in snaps],
        "hours_since_prev": round(hours_between(snaps[-2], snaps[-1])) if len(snaps) > 1 else None,
        "plays_stale": bool(snaps[-1].get("plays_stale")), "generated_at": datetime.datetime.now().isoformat(timespec="minutes"),
        "tracks": rows, "audience": aud, "cities": cities, "checklist": checks, "shortform": sf,
        "brief": brief(cfg, snaps, rows, aud, cities, checks, sf),
    }
    json.dump(rep, open(HERE / "data" / f"report-{cfg['slug']}.json", "w"), indent=1)
    return rep


if __name__ == "__main__":
    cfg = json.load(open(HERE / "releases.json"))
    for a in cfg["artists"]:
        rep = analyze(a)
        if rep:
            print(f"== {a['name']}: {len(rep['tracks'])} tracks, {rep['brief']['snapshot_days']} snapshot day(s)")
            for p in rep["brief"]["paragraphs"]:
                print("  ", p)
            for x in rep["brief"]["actions"]:
                print("  ->", x)
