#!/usr/bin/env python3
"""
Release Tower: daily snapshot collector.

One run = one dated snapshot per artist in releases.json, saved to data/snapshots/<slug>/<YYYY-MM-DD>.json.
Sources:
  - Spotify Web API (official): artist followers + popularity, per-track popularity, new albums/EPs
  - Apify sourabhbgp/spotify-scraper: monthly listeners, top cities, cumulative play counts per track
  - Apify apify/instagram-profile-scraper: followers, post count
  - Apify clockworks/tiktok-scraper: fans, hearts, recent videos with play counts

Credentials come from environment only (project-root .env for APIFY_TOKEN, demo/aandr-radar/.env for Spotify).
Nothing secret is ever printed.

Usage:  python3 collect.py [--only the-paradox] [--skip-social]
"""
import os, sys, json, datetime, argparse, pathlib
import httpx
from dotenv import load_dotenv

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
load_dotenv(HERE / ".env")                              # this folder (see .env.example)
load_dotenv(ROOT / ".env")                              # optional: a parent-folder .env
load_dotenv(ROOT / "demo" / "aandr-radar" / ".env")     # optional: legacy location

APIFY = os.getenv("APIFY_TOKEN", "").strip()
SP_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SP_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
if not (APIFY and SP_ID and SP_SECRET):
    sys.exit("Missing credentials in environment (APIFY_TOKEN / SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET).")

TODAY = datetime.date.today().isoformat()


def spotify_client() -> httpx.Client:
    r = httpx.post("https://accounts.spotify.com/api/token", data={"grant_type": "client_credentials"},
                   auth=(SP_ID, SP_SECRET), timeout=20)
    r.raise_for_status()
    return httpx.Client(base_url="https://api.spotify.com/v1",
                        headers={"Authorization": "Bearer " + r.json()["access_token"]}, timeout=30)


def apify(actor: str, payload: dict, timeout=170):
    """Run an actor synchronously. Never raises: a timeout or HTTP error returns [] and the run continues."""
    try:
        r = httpx.post(f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
                       params={"token": APIFY, "timeout": timeout}, json=payload, timeout=timeout + 30)
    except Exception as ex:
        print(f"  [{actor}] request failed: {type(ex).__name__}")
        return []
    if r.status_code not in (200, 201):
        print(f"  [{actor}] HTTP {r.status_code}: {r.text[:200]}")
        return []
    try:
        return r.json()
    except Exception:
        return []


def handle_from_links(links, name):
    for l in links or []:
        if (l.get("name") or "").upper() == name:
            return l.get("url", "").split("?")[0].rstrip("/").split("/")[-1].lstrip("@")
    return None


def collect_artist(sp: httpx.Client, a: dict, skip_social: bool) -> dict:
    aid = a["spotify_artist_id"]
    snap = {"date": TODAY, "collected_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "artist": a["name"], "slug": a["slug"]}

    # Official API: artist + discography + track popularity
    art = sp.get(f"/artists/{aid}").json()
    snap["spotify"] = {"followers": art["followers"]["total"], "popularity": art["popularity"],
                       "genres": art.get("genres", [])}
    albums = sp.get(f"/artists/{aid}/albums", params={"include_groups": "album,single", "limit": 50,
                                                      "market": "US"}).json().get("items", [])
    snap["discography"] = [{"id": x["id"], "name": x["name"], "type": x["album_type"],
                            "release_date": x["release_date"], "total_tracks": x["total_tracks"]} for x in albums]

    # Which albums to pull play counts for: campaign + catalog + anything released in the last 120 days
    want = set(a["release"].get("album_ids_to_track", [])) | set(a.get("catalog_album_ids", []))
    if a["release"].get("ep_album_id"):
        want.add(a["release"]["ep_album_id"])
    cutoff = (datetime.date.today() - datetime.timedelta(days=120)).isoformat()
    for x in albums:
        if x["release_date"] >= cutoff:
            want.add(x["id"])
    # Auto-detect the EP once it appears on Spotify
    if not a["release"].get("ep_album_id"):
        for x in albums:
            if x["name"].lower().startswith(a["release"]["title"].lower()[:12]) and x["total_tracks"] >= 4:
                snap["detected_ep_album_id"] = x["id"]
                want.add(x["id"])

    # Track popularity for every track on the wanted albums
    track_ids = []
    for alb_id in want:
        items = sp.get(f"/albums/{alb_id}/tracks", params={"limit": 50}).json().get("items", [])
        track_ids += [t["id"] for t in items]
    pops = {}
    for i in range(0, len(track_ids), 50):
        chunk = ",".join(track_ids[i:i + 50])
        for t in sp.get("/tracks", params={"ids": chunk}).json().get("tracks", []) or []:
            if t:
                pops[t["id"]] = t["popularity"]
    snap["track_popularity"] = pops

    # Apify: artist page (monthly listeners, cities, socials)
    print(f"  apify spotify artist ...")
    art_rows = apify("sourabhbgp~spotify-scraper",
                     {"mode": "artist", "spotifyUrls": [f"https://open.spotify.com/artist/{aid}"]})
    if art_rows:
        r0 = art_rows[0]
        snap["spotify"]["monthly_listeners"] = r0.get("monthlyListeners")
        snap["spotify"]["world_rank"] = r0.get("worldRank")
        snap["spotify"]["top_cities"] = r0.get("topCities", [])
        snap["spotify"]["external_links"] = r0.get("externalLinks", [])
        snap["spotify"]["top_tracks"] = [{"id": t.get("spotifyId"), "name": t.get("name"),
                                          "plays": t.get("playCount")} for t in r0.get("topTracks", [])]

    # Album metadata from the official API (release dates are reliable there, scrapers vary)
    alb_meta = {x["id"]: (x["name"], x["release_date"]) for x in albums}
    for alb_id in want:
        if alb_id not in alb_meta:
            j = sp.get(f"/albums/{alb_id}").json()
            alb_meta[alb_id] = (j.get("name"), j.get("release_date"))

    # Apify: albums (cumulative play counts per track). Primary actor, then fallback if it returns no counts.
    urls = [f"https://open.spotify.com/album/{i}" for i in sorted(want)]
    print(f"  apify spotify albums ({len(want)}) ...")
    plays = {}
    alb_rows = apify("sourabhbgp~spotify-scraper", {"mode": "album", "spotifyUrls": urls})
    for alb in alb_rows:
        alb_id = (alb.get("spotifyUrl") or "").rstrip("/").split("/")[-1].split("?")[0]
        name, rel = alb_meta.get(alb_id, (alb.get("name"), (alb.get("releaseDate") or "")[:10]))
        for t in alb.get("tracks", []):
            tid = t.get("spotifyId") or t.get("id")
            plays[tid] = {"name": t.get("name"), "plays": t.get("playCount"), "album": name,
                          "album_release": (rel or "")[:10], "label": alb.get("label"), "source": "sourabhbgp"}
    if not any(v.get("plays") for v in plays.values()):
        print("  primary scraper returned no play counts, trying fallback ...")
        alt = []
        for i in range(0, len(urls), 2):   # small chunks: long multi-url runs time out
            alt += apify("automation-lab~spotify-scraper", {"mode": "urls", "urls": urls[i:i + 2], "maxResults": 50}, timeout=240)
        for alb in alt:
            alb_id = alb.get("id") or (alb.get("url") or "").rstrip("/").split("/")[-1]
            name, rel = alb_meta.get(alb_id, (alb.get("name"), ""))
            for t in alb.get("tracks", []):
                tid = t.get("trackId") or t.get("id")
                plays[tid] = {"name": t.get("title") or t.get("name"), "plays": t.get("playCount"), "album": name,
                              "album_release": (rel or "")[:10], "label": alb.get("label"), "source": "automation-lab"}
    snap["track_plays"] = plays
    snap["plays_source"] = next((v["source"] for v in plays.values() if v.get("plays")), None)

    if skip_social:
        return snap

    ig = a.get("instagram") or handle_from_links(snap["spotify"].get("external_links"), "INSTAGRAM")
    tt = a.get("tiktok") or handle_from_links(snap["spotify"].get("external_links"), "TIKTOK")
    snap["handles"] = {"instagram": ig, "tiktok": tt}

    if ig:
        print(f"  apify instagram @{ig} ...")
        rows = apify("apify~instagram-profile-scraper", {"usernames": [ig]})
        if rows:
            p = rows[0]
            snap["instagram"] = {"followers": p.get("followersCount"), "posts": p.get("postsCount"),
                                 "bio_link": p.get("externalUrl"), "verified": p.get("verified")}
    if tt:
        print(f"  apify tiktok @{tt} ...")
        rows = apify("clockworks~tiktok-scraper", {"profiles": [tt], "resultsPerPage": 12,
                                                   "shouldDownloadVideos": False, "shouldDownloadCovers": False})
        if rows:
            am = rows[0].get("authorMeta", {})
            snap["tiktok"] = {"fans": am.get("fans"), "hearts": am.get("heart"), "videos": am.get("video"),
                              "recent": [{"posted": v.get("createTimeISO"), "plays": v.get("playCount"),
                                          "likes": v.get("diggCount"), "comments": v.get("commentCount"),
                                          "shares": v.get("shareCount"), "text": (v.get("text") or "")[:140],
                                          "url": v.get("webVideoUrl")} for v in rows]}
    return snap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--skip-social", action="store_true")
    args = ap.parse_args()
    cfg = json.load(open(HERE / "releases.json"))
    sp = spotify_client()
    for a in cfg["artists"]:
        if args.only and a["slug"] != args.only:
            continue
        print(f"[{TODAY}] {a['name']}")
        try:
            snap = collect_artist(sp, a, args.skip_social)
        except Exception as ex:
            print(f"  FAILED for {a['name']}: {type(ex).__name__}: {str(ex)[:120]}. Keeping previous snapshot.")
            continue
        out = HERE / "data" / "snapshots" / a["slug"]
        out.mkdir(parents=True, exist_ok=True)
        json.dump(snap, open(out / f"{TODAY}.json", "w"), indent=1)
        s = snap["spotify"]
        print(f"  saved. followers {s['followers']:,} · monthly {s.get('monthly_listeners')} · "
              f"tracks with plays {len(snap['track_plays'])}")


if __name__ == "__main__":
    main()
