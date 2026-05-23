#!/usr/bin/env python3
"""
Sub-20 5k Dashboard Generator
Reads Garmin data from local SQLite DB, fetches Strava best efforts + stats,
fetches Notion running plan, and writes index.html.
"""

import json
import os
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

import requests

# ─── Paths & config ────────────────────────────────────────────────────────
GARMIN_DB         = Path.home() / ".garmin-givemydata" / "garmin.db"
STRAVA_TOKENS     = Path.home() / ".strava-mcp" / "tokens.json"
LOUIS_TOKENS      = Path.home() / ".strava-louis" / "tokens.json"
STRAVA_CLIENT_ID  = "248666"
STRAVA_CLIENT_SECRET = os.environ.get(
    "STRAVA_CLIENT_SECRET", "52a741de3d3257c5daaa2e9449fe2a5f35573c02"
)
NOTION_TOKEN      = os.environ.get("NOTION_TOKEN", "")
NOTION_PAGE_ID    = "368a64e59b5a803fbbdcdff622b6e49c"
OUTPUT            = Path(__file__).parent / "index.html"

# Goal constants
GOAL_SECS  = 1200   # 20:00
START_SECS = 1631   # 27:11 — first tracked best effort (Jan 2025)
LOUIS_SECS = 1260   # ~21:00

# ─── Strava helpers ────────────────────────────────────────────────────────

def strava_refresh(token_file=None):
    token_file = Path(token_file) if token_file else STRAVA_TOKENS
    tokens = json.loads(token_file.read_text())
    r = requests.post("https://www.strava.com/oauth/token", data={
        "client_id":     STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "refresh_token": tokens["refresh_token"],
        "grant_type":    "refresh_token",
    })
    r.raise_for_status()
    data = r.json()
    tokens.update({
        "access_token":  data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at":    data["expires_at"],
    })
    token_file.write_text(json.dumps(tokens, indent=2))
    return data["access_token"]


def strava_get(token, path, **params):
    r = requests.get(
        f"https://www.strava.com/api/v3/{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
    )
    r.raise_for_status()
    return r.json()


def fetch_strava():
    token = strava_refresh(STRAVA_TOKENS)

    # Recent activities
    activities = [
        a for a in strava_get(token, "athlete/activities", per_page=25)
        if a.get("sport_type") == "Run"
    ]

    # 5k best efforts — scan activities with distance >= 4km
    best_efforts = []
    for act in activities:
        if act.get("distance", 0) < 4000:
            continue
        detail = strava_get(token, f"activities/{act['id']}", include_all_efforts=True)
        time.sleep(0.25)  # stay well under rate limit
        for e in detail.get("best_efforts", []):
            if e.get("distance") == 5000:
                best_efforts.append({
                    "secs": e["elapsed_time"],
                    "date": act["start_date_local"][:10],
                    "name": act["name"],
                })
    best_efforts.sort(key=lambda x: x["secs"])

    # Athlete stats
    athlete_id = strava_get(token, "athlete")["id"]
    stats = strava_get(token, f"athletes/{athlete_id}/stats")

    # Latest run map — grab summary_polyline from most recent activity
    latest_map = None
    for act in activities[:5]:
        poly = (act.get("map") or {}).get("summary_polyline", "")
        if poly:
            latest_map = {
                "polyline":       poly,
                "name":           act.get("name", "Morning Run"),
                "date":           act["start_date_local"][:10],
                "distance_m":     act.get("distance", 0),
                "moving_time":    act.get("moving_time", 0),
                "avg_hr":         act.get("average_heartrate"),
                "elevation_gain": act.get("total_elevation_gain", 0),
                "activity_id":    act["id"],
            }
            break

    return {
        "activities":   activities[:10],
        "best_efforts": best_efforts,
        "stats":        stats,
        "latest_map":   latest_map,
    }


def fetch_louis_strava():
    """Fetch Louis's Strava data — same method as Jack's. Returns None if not yet connected."""
    if not LOUIS_TOKENS.exists():
        return None
    token = strava_refresh(LOUIS_TOKENS)

    activities = [
        a for a in strava_get(token, "athlete/activities", per_page=50)
        if a.get("sport_type") == "Run"
    ]

    # 5k best efforts — same scan as Jack
    best_efforts = []
    for act in activities:
        if act.get("distance", 0) < 4000:
            continue
        detail = strava_get(token, f"activities/{act['id']}", include_all_efforts=True)
        time.sleep(0.25)
        for e in detail.get("best_efforts", []):
            if e.get("distance") == 5000:
                best_efforts.append({
                    "secs": e["elapsed_time"],
                    "date": act["start_date_local"][:10],
                    "name": act["name"],
                })
    best_efforts.sort(key=lambda x: x["secs"])

    athlete_id = strava_get(token, "athlete")["id"]
    stats = strava_get(token, f"athletes/{athlete_id}/stats")

    return {
        "activities":   activities[:10],
        "best_efforts": best_efforts,
        "stats":        stats,
    }


# ─── Garmin DB helpers ─────────────────────────────────────────────────────

def _db():
    conn = sqlite3.connect(GARMIN_DB)
    conn.row_factory = sqlite3.Row
    return conn


def garmin_sleep(days=5):
    start = (date.today() - timedelta(days=days)).isoformat()
    with _db() as conn:
        rows = conn.execute("""
            SELECT calendar_date,
                   ROUND(sleep_time_seconds / 3600.0, 2)  AS total_hours,
                   deep_sleep_seconds,
                   light_sleep_seconds,
                   rem_sleep_seconds,
                   awake_sleep_seconds,
                   average_hr_sleep                       AS avg_hr,
                   resting_heart_rate,
                   body_battery_change
            FROM   sleep
            WHERE  calendar_date >= ?
            ORDER  BY calendar_date
        """, (start,)).fetchall()
    return [dict(r) for r in rows]


def garmin_steps(days=7):
    start = (date.today() - timedelta(days=days)).isoformat()
    with _db() as conn:
        rows = conn.execute("""
            SELECT calendar_date,
                   total_steps,
                   daily_step_goal
            FROM   daily_summary
            WHERE  calendar_date >= ?
              AND  total_steps IS NOT NULL
            ORDER  BY calendar_date
        """, (start,)).fetchall()
    return [dict(r) for r in rows]


def garmin_recent_runs(days=35):
    start = (date.today() - timedelta(days=days)).isoformat()
    with _db() as conn:
        rows = conn.execute("""
            SELECT start_time_local,
                   activity_name,
                   ROUND(distance_meters / 1000.0, 2) AS distance_km,
                   ROUND(duration_seconds / 60.0, 1)  AS duration_min,
                   average_hr,
                   elevation_gain,
                   location_name
            FROM   activity
            WHERE  activity_type = 'running'
              AND  start_time_local >= ?
            ORDER  BY start_time_local DESC
            LIMIT  12
        """, (start,)).fetchall()
    return [dict(r) for r in rows]


# ─── Notion helper ─────────────────────────────────────────────────────────

def fetch_notion_plan():
    if not NOTION_TOKEN:
        return None
    r = requests.get(
        f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children",
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
        },
        params={"page_size": 100},
    )
    if not r.ok:
        print(f"Notion fetch failed: {r.status_code}")
        return None
    lines = []
    for block in r.json().get("results", []):
        btype = block.get("type", "")
        rt = block.get(btype, {}).get("rich_text", [])
        text = "".join(t.get("plain_text", "") for t in rt)
        if text.strip():
            lines.append(text)
    return lines


# ─── Formatting helpers ────────────────────────────────────────────────────

def mmss(secs):
    if not secs:
        return "–:––"
    m, s = divmod(int(secs), 60)
    return f"{m}:{s:02d}"


def pace_class(pace_secs_per_km):
    if pace_secs_per_km <= 245:   return "pace-great"   # ≤4:05
    if pace_secs_per_km <= 315:   return "pace-good"    # ≤5:15
    if pace_secs_per_km <= 360:   return "pace-ok"      # ≤6:00
    return "pace-easy"


def sleep_bar_html(deep, light, rem, awake):
    total = (deep or 0) + (light or 0) + (rem or 0) + (awake or 0)
    if not total:
        return '<div class="sleep-stage"><div class="stage-light" style="width:100%"></div></div>'
    def pct(v):
        return round((v or 0) / total * 100, 1)
    parts = []
    if deep:  parts.append(f'<div class="stage-deep"  style="width:{pct(deep)}%"></div>')
    if light: parts.append(f'<div class="stage-light" style="width:{pct(light)}%"></div>')
    if rem:   parts.append(f'<div class="stage-rem"   style="width:{pct(rem)}%"></div>')
    if awake: parts.append(f'<div class="stage-wake"  style="width:{pct(awake)}%"></div>')
    return f'<div class="sleep-stage">{"".join(parts)}</div>'


# ─── HTML generation ───────────────────────────────────────────────────────

def build_sleep_rows(sleep_data):
    rows = []
    day_names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    for s in sleep_data[-5:]:
        d = date.fromisoformat(s["calendar_date"])
        label = f"{day_names[d.weekday()]} {d.strftime('%b %-d')}"
        h = int(s["total_hours"] or 0)
        m = int(round(((s["total_hours"] or 0) - h) * 60))
        total_str = f"{h}h {m:02d}m"
        bb = s.get("body_battery_change") or 0
        bb_color = "var(--target)" if bb >= 50 else "var(--warn)" if bb >= 30 else "var(--danger)"
        deep_s  = s.get("deep_sleep_seconds") or 0
        light_s = s.get("light_sleep_seconds") or 0
        rem_s   = s.get("rem_sleep_seconds") or 0
        wake_s  = s.get("awake_sleep_seconds") or 0
        total_s = deep_s + light_s + rem_s + wake_s or 1
        bar = sleep_bar_html(deep_s, light_s, rem_s, wake_s)
        deep_pct = round(deep_s / total_s * 100)
        rem_pct  = round(rem_s  / total_s * 100)
        hr = s.get("avg_hr") or 0
        deep_warn = ' style="color:var(--warn)"' if deep_pct == 0 else ""
        deep_note = "No deep sleep detected" if deep_pct == 0 else f"Deep {deep_pct}% · REM {rem_pct}%"
        rows.append(f"""
    <div class="sleep-row">
      <div class="sleep-row-header">
        <span class="sleep-date">{label}</span>
        <span class="sleep-total">{total_str}</span>
        <span class="sleep-bb" style="color:{bb_color}">BB +{bb}</span>
      </div>
      {bar}
      <div class="sleep-detail"{deep_warn}>{deep_note} · HR avg {int(hr)}bpm</div>
    </div>""")
    return "\n".join(rows)


def build_run_rows(activities, best_efforts_map):
    rows = []
    for act in activities[:10]:
        dt = date.fromisoformat(act["start_time_local"][:10])
        date_str = dt.strftime("%b %-d")
        dist = act["distance_km"] or 0
        dur_min = act["duration_min"] or 0
        name = act.get("activity_name") or "Run"
        location = act.get("location_name") or ""
        if location and location not in name:
            display_name = location
        else:
            display_name = name

        # Pace in secs/km
        if dist > 0 and dur_min > 0:
            pace_s = int((dur_min * 60) / dist)
            pace_str = mmss(pace_s)
            pc = pace_class(pace_s)
        else:
            pace_str, pc = "–", "pace-easy"

        hr = int(act["average_hr"] or 0)
        elev = int(act["elevation_gain"] or 0)
        elev_str = f"+{elev}m" if elev else "—"

        # 5k split if available
        best_5k = best_efforts_map.get(act["start_time_local"][:10], "")
        split_cell = f'<span class="seg-badge">seg</span> {mmss(best_5k)}' if best_5k else "—"

        # Time in MM:SS
        total_s = int(dur_min * 60)
        time_str = f"{int(dur_min//60)}:{int(dur_min%60):02d}" if dur_min >= 60 else mmss(total_s)

        # PR badge
        pr_badge = ""
        if best_efforts_map.get("__pb_date__") == act["start_time_local"][:10]:
            pr_badge = ' <span class="pr-badge">PB</span>'

        rows.append(f"""      <tr>
        <td>{date_str}</td>
        <td>{display_name}{pr_badge}</td>
        <td>{dist:.2f} km</td>
        <td>{split_cell}</td>
        <td><span class="pace-pill {pc}">{pace_str}</span></td>
        <td>{hr} bpm</td>
        <td>{elev_str}</td>
      </tr>""")
    return "\n".join(rows)


def build_steps_chart_data(steps_data):
    labels = [f'"{date.fromisoformat(s["calendar_date"]).strftime("%b %-d")}"' for s in steps_data]
    counts = [s["total_steps"] or 0 for s in steps_data]
    goals  = [s["daily_step_goal"] or 10000 for s in steps_data]
    colors = [
        '"rgba(34,197,94,0.7)"' if c >= g else '"rgba(249,115,22,0.5)"'
        for c, g in zip(counts, goals)
    ]
    return (
        f"[{','.join(labels)}]",
        f"[{','.join(str(c) for c in counts)}]",
        f"[{','.join(str(g) for g in goals)}]",
        f"[{','.join(colors)}]",
    )


def build_map_section(latest_map, best_efforts):
    if not latest_map:
        return "", ""
    poly = latest_map["polyline"]
    dist_km = round(latest_map["distance_m"] / 1000, 2)
    run_date = date.fromisoformat(latest_map["date"]).strftime("%b %-d, %Y")
    hr = int(latest_map["avg_hr"] or 0)
    elev = int(latest_map["elevation_gain"] or 0)
    dist_km_val = latest_map["distance_m"] / 1000
    moving_time = latest_map.get("moving_time") or 0
    pace_s = int(moving_time / dist_km_val) if dist_km_val > 0 and moving_time > 0 else 0

    # Best 5k for that run date
    be_map = {e["date"]: e["secs"] for e in best_efforts}
    split_secs = be_map.get(latest_map["date"])
    split_str = mmss(split_secs) if split_secs else "–"

    # Prefer 5k split for pace display (most representative)
    if split_secs:
        pace_s = split_secs // 5
    pace_str = mmss(pace_s) if pace_s else "–"

    run_name = latest_map["name"]

    html = f"""
<p class="section-title">Latest Run</p>
<div class="card map-card" style="margin-bottom:16px">
  <div class="map-header">
    <div>
      <div class="map-title">{run_name}</div>
      <div class="map-subtitle">{run_date}</div>
    </div>
    <div class="map-stats">
      <div class="map-stat"><div class="map-stat-val" style="color:var(--jack)">{dist_km}<span style="font-size:11px;font-weight:400">km</span></div><div class="map-stat-lbl">Distance</div></div>
      <div class="map-stat"><div class="map-stat-val">{split_str}</div><div class="map-stat-lbl">5k split</div></div>
      <div class="map-stat"><div class="map-stat-val" style="color:var(--jack)">{pace_str}</div><div class="map-stat-lbl">Pace /km</div></div>
      <div class="map-stat"><div class="map-stat-val">+{elev}<span style="font-size:11px;font-weight:400">m</span></div><div class="map-stat-lbl">Elevation</div></div>
      <div class="map-stat"><div class="map-stat-val">{hr}</div><div class="map-stat-lbl">Avg HR</div></div>
    </div>
  </div>
  <div id="runMap"></div>
</div>"""

    poly_js = json.dumps(poly)
    js = f"""
<script>
(function() {{
  function decode(str) {{
    let i=0,lat=0,lng=0,out=[];
    while(i<str.length){{
      let b,shift=0,res=0;
      do{{b=str.charCodeAt(i++)-63;res|=(b&0x1f)<<shift;shift+=5;}}while(b>=0x20);
      lat+=res&1?~(res>>1):res>>1;
      shift=0;res=0;
      do{{b=str.charCodeAt(i++)-63;res|=(b&0x1f)<<shift;shift+=5;}}while(b>=0x20);
      lng+=res&1?~(res>>1):res>>1;
      out.push([lat/1e5,lng/1e5]);
    }}
    return out;
  }}
  const coords=decode({poly_js});
  const map=L.map('runMap',{{zoomControl:true,scrollWheelZoom:false,dragging:true,doubleClickZoom:true}});
  L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_nolabels/{{z}}/{{x}}/{{y}}{{r}}.png',{{maxZoom:19,subdomains:'abcd'}}).addTo(map);
  const route=L.polyline(coords,{{color:'#f97316',weight:3.5,opacity:0.95,lineCap:'round',lineJoin:'round'}}).addTo(map);
  L.circleMarker(coords[0],{{radius:7,fillColor:'#22c55e',color:'#08090d',weight:2.5,fillOpacity:1}}).bindTooltip('Start',{{permanent:false,direction:'right'}}).addTo(map);
  L.circleMarker(coords[coords.length-1],{{radius:7,fillColor:'#ef4444',color:'#08090d',weight:2.5,fillOpacity:1}}).bindTooltip('Finish',{{permanent:false,direction:'right'}}).addTo(map);
  map.fitBounds(route.getBounds(),{{padding:[24,24]}});
}})();
</script>"""
    return html, js


def generate(strava, louis_strava, sleep_data, steps_data, runs, notion_lines):
    best_efforts = strava["best_efforts"]
    stats        = strava["stats"]

    pb = best_efforts[0] if best_efforts else {"secs": 1552, "date": "–", "name": "–"}
    pb_secs  = pb["secs"]
    pb_time  = mmss(pb_secs)
    pb_date  = pb["date"]
    pb_pace  = mmss(pb_secs // 5)

    progress_pct = round((START_SECS - pb_secs) / (START_SECS - GOAL_SECS) * 100, 1)
    gap_secs  = pb_secs - GOAL_SECS
    gap_str   = mmss(gap_secs)
    gap_km    = mmss(pb_secs // 5 - GOAL_SECS // 5)
    saved_str = mmss(START_SECS - pb_secs)

    # Strava stats
    ytd_runs  = stats.get("ytd_run_totals", {}).get("count", 0)
    ytd_km    = round((stats.get("ytd_run_totals", {}).get("distance", 0) or 0) / 1000, 1)
    r4w_km    = round((stats.get("recent_run_totals", {}).get("distance", 0) or 0) / 1000, 1)

    # Pace trend for chart — top 5 best efforts (oldest first)
    trend = list(reversed(best_efforts[:5]))
    jack_dates = [e["date"] for e in trend]
    jack_secs  = [e["secs"] for e in trend]

    # ── Louis's data ────────────────────────────────────────────────────────
    if louis_strava and louis_strava["best_efforts"]:
        louis_be        = louis_strava["best_efforts"]
        louis_pb        = louis_be[0]
        louis_pb_secs   = louis_pb["secs"]
        louis_pb_time   = mmss(louis_pb_secs)
        louis_pb_date   = louis_pb["date"]
        louis_pb_pace   = mmss(louis_pb_secs // 5)
        louis_gap_secs  = louis_pb_secs - GOAL_SECS
        louis_gap_str   = mmss(louis_gap_secs) if louis_gap_secs > 0 else "Sub-20!"
        louis_ytd_runs  = louis_strava["stats"].get("ytd_run_totals", {}).get("count", 0)
        louis_ytd_km    = round((louis_strava["stats"].get("ytd_run_totals", {}).get("distance", 0) or 0) / 1000, 1)
        louis_r4w_km    = round((louis_strava["stats"].get("recent_run_totals", {}).get("distance", 0) or 0) / 1000, 1)

        # Merge chart dates (union of Jack + Louis best effort dates, sorted)
        louis_trend   = list(reversed(louis_be[:5]))
        louis_by_date = {e["date"]: e["secs"] for e in louis_trend}
        jack_by_date  = {e["date"]: e["secs"] for e in trend}
        all_dates     = sorted(set(jack_dates) | set(louis_by_date))
        trend_labels  = json.dumps(all_dates)
        trend_data    = json.dumps([jack_by_date.get(d) for d in all_dates])
        louis_chart_data = json.dumps([louis_by_date.get(d) for d in all_dates])
        louis_chart_dataset = (
            f"{{label:'Louis',data:{louis_chart_data},"
            f"borderColor:'#3b82f6',backgroundColor:'rgba(59,130,246,0.06)',"
            f"borderWidth:2.5,pointBackgroundColor:'#3b82f6',pointRadius:5,"
            f"tension:0.3,spanGaps:true,fill:false}}"
        )

        # Dynamic snark based on gap
        jack_louis_gap = pb_secs - louis_pb_secs
        if jack_louis_gap < -60:
            snark = f"Currently <strong style='color:var(--louis)'>{mmss(abs(jack_louis_gap))} ahead of Jack</strong>. Enjoy it. The gap is closing fast."
        elif jack_louis_gap < 0:
            snark = f"Ahead by {mmss(abs(jack_louis_gap))}. Don't get comfortable &mdash; Jack is coming."
        elif jack_louis_gap < 30:
            snark = "Neck and neck. This is getting interesting."
        else:
            snark = f"Jack has overtaken Louis by <strong style='color:var(--jack)'>{mmss(jack_louis_gap)}</strong>. The reckoning has arrived."

        louis_card_html = f"""  <div class="card rival-card rival-louis">
    <div class="rival-name">Louis</div>
    <div class="rival-pb" style="color:var(--louis)">{louis_pb_time}</div>
    <div class="rival-meta">{louis_pb_pace} /km &nbsp;&middot;&nbsp; {louis_pb_date} &nbsp;&middot;&nbsp; Strava best effort</div>
    <div class="rival-sub">{louis_ytd_runs} runs &nbsp;&middot;&nbsp; {louis_ytd_km}km YTD &nbsp;&middot;&nbsp; {louis_r4w_km}km last 4 wks</div>
    <div class="rival-delta delta-behind">{louis_gap_str} from goal</div>
    <div class="louis-snark">{snark}</div>
  </div>"""

    else:
        # Louis not yet connected — use placeholder
        trend_labels = json.dumps(jack_dates)
        trend_data   = json.dumps(jack_secs)
        louis_chart_dataset = (
            f"{{label:'Louis',data:{json.dumps(jack_dates)}.map(()=>{LOUIS_SECS}),"
            f"borderColor:'rgba(59,130,246,0.4)',borderDash:[6,4],"
            f"borderWidth:1.5,pointRadius:0,fill:false}}"
        )
        louis_card_html = """  <div class="card rival-card rival-louis">
    <div class="rival-name">Louis</div>
    <div class="rival-pb" style="color:var(--subtle)">~21-ish</div>
    <div class="rival-meta" style="color:var(--subtle)">Minutes. Apparently. Give or take.</div>
    <div class="rival-delta delta-behind" style="opacity:.5">~1 min from goal (?)</div>
    <div class="louis-snark">
      Stuck somewhere in the low 21s, from what we hear &mdash; usually accompanied by a story about a slightly dodgy calf, a hilly route, or the wind being &ldquo;completely against him&rdquo;. Could break 20 any day now. Has been saying this since roughly late 2024. Bless.
    </div>
  </div>"""

    # Steps
    step_labels, step_counts, step_goals, step_colors = build_steps_chart_data(steps_data)
    avg_steps = int(sum(s["total_steps"] or 0 for s in steps_data) / len(steps_data)) if steps_data else 0
    best_step = max(steps_data, key=lambda s: s["total_steps"] or 0) if steps_data else {}
    best_step_val = best_step.get("total_steps", 0) or 0
    best_step_date = date.fromisoformat(best_step["calendar_date"]).strftime("%b %-d") if best_step else "–"
    days_goal_met = sum(1 for s in steps_data if (s["total_steps"] or 0) >= (s["daily_step_goal"] or 1))

    # Sleep HTML
    sleep_rows_html = build_sleep_rows(sleep_data)

    # Build best_efforts_map keyed by date for run table
    be_map = {e["date"]: e["secs"] for e in best_efforts}
    if best_efforts:
        be_map["__pb_date__"] = best_efforts[0]["date"]
    run_rows_html = build_run_rows(runs, be_map)

    # Notion plan note
    plan_source = "(from Notion / Running Plan)"
    if not notion_lines:
        plan_source = "(Notion not configured — set NOTION_TOKEN)"

    sync_date = date.today().strftime("%b %-d, %Y")

    # Map section
    map_html, map_js = build_map_section(strava.get("latest_map"), best_efforts)

    # ── Weekly volume (last 5 weeks) ──────────────────────────────────────
    from collections import defaultdict
    weekly_km = defaultdict(float)
    for act in strava["activities"]:
        d = date.fromisoformat(act["start_date_local"][:10])
        # ISO week start (Monday)
        week_start = d - timedelta(days=d.weekday())
        weekly_km[week_start] += act.get("distance", 0) / 1000
    weeks_sorted = sorted(weekly_km.keys())[-5:]
    week_labels = json.dumps([
        f"{w.strftime('%b %-d')}–{(w+timedelta(days=6)).strftime('%-d')}"
        for w in weeks_sorted
    ])
    week_dist   = json.dumps([round(weekly_km[w], 1) for w in weeks_sorted])

    # ── Write HTML ────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Jack vs Louis — Sub-20 5k</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    :root{{
      --bg:#08090d;--surface:#111218;--border:#1e2030;--muted:#3a3d52;
      --text:#e2e4f0;--subtle:#7a7f9a;
      --jack:#f97316;--jack-dim:#7c3210;--louis:#3b82f6;
      --target:#22c55e;--warn:#eab308;--danger:#ef4444;--radius:12px;
    }}
    body{{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;min-height:100vh;padding:24px;max-width:1100px;margin:0 auto}}
    .grid{{display:grid;gap:16px}} .col-2{{grid-template-columns:1fr 1fr}} .col-3{{grid-template-columns:1fr 1fr 1fr}}
    @media(max-width:860px){{.col-2,.col-3{{grid-template-columns:1fr}}}}
    .card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:20px}}
    .card-label{{font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--subtle);margin-bottom:10px}}
    .section-title{{font-size:11px;font-weight:700;color:var(--subtle);text-transform:uppercase;letter-spacing:.1em;margin:24px 0 10px}}
    header{{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:16px;gap:16px}}
    header h1{{font-size:24px;font-weight:800;line-height:1.2}} header h1 span{{color:var(--target)}}
    .sync-badge{{font-size:11px;color:var(--subtle);background:var(--surface);border:1px solid var(--border);border-radius:20px;padding:5px 12px;white-space:nowrap;flex-shrink:0}}
    .sync-badge b{{color:var(--target)}}
    .intro-banner{{background:linear-gradient(135deg,rgba(249,115,22,.07),rgba(34,197,94,.05));border:1px solid rgba(249,115,22,.2);border-radius:var(--radius);padding:18px 22px;margin-bottom:16px;font-size:14px;line-height:1.7}}
    .intro-banner p+p{{margin-top:8px}} .intro-banner strong{{color:var(--jack)}} .intro-banner em{{color:var(--subtle);font-style:normal}}
    .hero-card{{padding:24px}}
    .hero-times{{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:18px}}
    .hero-time-big{{font-size:60px;font-weight:800;letter-spacing:-2px;color:var(--jack);line-height:1}}
    .hero-time-sub{{font-size:12px;color:var(--subtle);margin-top:4px}}
    .hero-gap{{text-align:right}} .hero-gap-num{{font-size:30px;font-weight:700;color:var(--warn)}} .hero-gap-sub{{font-size:12px;color:var(--subtle)}}
    .progress-track{{position:relative;height:14px;background:var(--muted);border-radius:7px;margin:14px 0 26px}}
    .progress-fill{{height:100%;border-radius:7px;background:linear-gradient(90deg,var(--jack-dim),var(--jack));position:relative}}
    .progress-fill::after{{content:'';position:absolute;right:-1px;top:-3px;width:20px;height:20px;background:var(--jack);border-radius:50%;border:3px solid var(--bg)}}
    .target-line{{position:absolute;right:0;top:-4px;width:3px;height:22px;background:var(--target);border-radius:2px}}
    .milestone-dot{{position:absolute;top:50%;transform:translate(-50%,-50%);width:8px;height:8px;border-radius:50%;background:var(--muted);border:2px solid var(--border)}}
    .progress-labels{{display:flex;justify-content:space-between;font-size:11px;color:var(--subtle);margin-top:-20px}}
    .stats-row{{display:flex;gap:20px;flex-wrap:wrap;margin-top:14px}}
    .stat-item{{flex:1;min-width:70px}} .stat-val{{font-size:20px;font-weight:700}} .stat-lbl{{font-size:11px;color:var(--subtle);margin-top:2px}}
    .rival-card{{position:relative;overflow:hidden}}
    .rival-card::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px}}
    .rival-jack::before{{background:var(--jack)}} .rival-louis::before{{background:var(--louis)}}
    .rival-name{{font-size:17px;font-weight:800;margin-bottom:2px}}
    .rival-jack .rival-name{{color:var(--jack)}} .rival-louis .rival-name{{color:var(--louis)}}
    .rival-pb{{font-size:46px;font-weight:800;letter-spacing:-1px;line-height:1.1}}
    .rival-meta{{font-size:12px;color:var(--subtle);margin-top:4px}} .rival-sub{{font-size:11px;color:var(--muted);margin-top:4px}}
    .rival-delta{{display:inline-block;margin-top:10px;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}}
    .delta-behind{{background:rgba(234,179,8,.15);color:var(--warn)}} .delta-ahead{{background:rgba(34,197,94,.15);color:var(--target)}}
    .louis-snark{{margin-top:12px;padding:10px 12px;border-radius:8px;background:rgba(59,130,246,.07);border-left:3px solid var(--louis);font-size:13px;color:#94a3b8;font-style:italic;line-height:1.5}}
    .chart-wrap{{position:relative}}
    .next-up-card{{background:linear-gradient(135deg,rgba(249,115,22,.08),rgba(249,115,22,.03));border:1px solid rgba(249,115,22,.25)}}
    .next-up-item{{display:flex;align-items:center;gap:12px;padding:10px 0}}
    .next-up-item+.next-up-item{{border-top:1px solid rgba(249,115,22,.1)}}
    .next-up-date{{font-size:11px;color:var(--subtle);min-width:52px}}
    .next-up-desc{{font-size:13px;font-weight:500}} .session-meta{{font-size:11px;color:var(--subtle);margin-top:2px}}
    .session-badge{{flex-shrink:0;font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;margin-top:1px;letter-spacing:.04em;text-transform:uppercase}}
    .badge-intervals{{background:rgba(249,115,22,.15);color:var(--jack)}}
    .badge-easy{{background:rgba(59,130,246,.12);color:#60a5fa}}
    .badge-parkrun{{background:rgba(34,197,94,.12);color:var(--target)}}
    .plan-week{{margin-bottom:18px}}
    .plan-week-title{{font-size:12px;font-weight:700;color:var(--jack);margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}}
    .parkrun-target{{font-size:11px;font-weight:600;padding:2px 8px;border-radius:4px;background:rgba(34,197,94,.1);color:var(--target)}}
    .parkrun-target.overtake{{background:rgba(249,115,22,.15);color:var(--jack)}}
    .plan-session{{display:flex;align-items:flex-start;gap:10px;padding:7px 0}}
    .plan-session+.plan-session{{border-top:1px solid rgba(30,32,48,.5)}}
    .session-text{{font-size:13px;color:var(--text);line-height:1.4}}
    .sleep-row{{margin-bottom:14px}}
    .sleep-row-header{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px}}
    .sleep-date{{font-size:12px;color:var(--subtle)}} .sleep-total{{font-size:14px;font-weight:700}} .sleep-bb{{font-size:12px;font-weight:600}}
    .sleep-stage{{display:flex;height:8px;border-radius:4px;overflow:hidden;gap:2px}}
    .stage-deep{{background:#6366f1}} .stage-light{{background:#64748b}} .stage-rem{{background:#a78bfa}} .stage-wake{{background:var(--warn)}}
    .sleep-detail{{font-size:11px;color:var(--subtle);margin-top:3px}}
    .run-table{{width:100%;border-collapse:collapse;font-size:13px}}
    .run-table th{{text-align:left;padding:6px 10px;color:var(--subtle);font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;border-bottom:1px solid var(--border)}}
    .run-table td{{padding:9px 10px;border-bottom:1px solid rgba(30,32,48,.6);vertical-align:middle}}
    .run-table tr:last-child td{{border-bottom:none}} .run-table tr:hover td{{background:rgba(255,255,255,.02)}}
    .pace-pill{{display:inline-block;padding:2px 8px;border-radius:4px;font-weight:600;font-size:12px}}
    .pace-great{{background:rgba(34,197,94,.15);color:var(--target)}} .pace-good{{background:rgba(249,115,22,.15);color:var(--jack)}}
    .pace-ok{{background:rgba(234,179,8,.12);color:var(--warn)}} .pace-easy{{background:rgba(122,127,154,.12);color:var(--subtle)}}
    .pr-badge{{font-size:10px;font-weight:700;padding:1px 5px;border-radius:3px;background:rgba(249,115,22,.2);color:var(--jack);margin-left:4px}}
    .seg-badge{{font-size:9px;font-weight:600;padding:1px 4px;border-radius:3px;background:rgba(34,197,94,.12);color:var(--target);margin-right:3px}}
    .table-note{{font-size:11px;color:var(--muted);padding:8px 10px 0}}
    .legend{{display:flex;gap:14px;flex-wrap:wrap}}
    .legend-item{{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--subtle)}}
    .legend-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
    footer{{text-align:center;font-size:11px;color:var(--muted);padding:20px 0 8px;line-height:1.8}}
    .map-card{{padding:0;overflow:hidden}}
    .map-header{{padding:14px 20px 12px;display:flex;align-items:center;gap:16px;border-bottom:1px solid var(--border);flex-wrap:wrap}}
    .map-title{{font-size:15px;font-weight:700}}
    .map-subtitle{{font-size:12px;color:var(--subtle);margin-top:2px}}
    .map-stats{{margin-left:auto;display:flex;gap:20px;flex-wrap:wrap}}
    .map-stat{{text-align:right}}
    .map-stat-val{{font-size:14px;font-weight:700}}
    .map-stat-lbl{{font-size:10px;color:var(--subtle);text-transform:uppercase;letter-spacing:.06em;margin-top:1px}}
    #runMap{{height:300px;width:100%;background:#08090d}}
    .leaflet-container{{background:#08090d !important}}
    .leaflet-control-attribution{{display:none !important}}
    .leaflet-bar a{{background:#111218 !important;color:var(--text) !important;border-color:#1e2030 !important}}
    .leaflet-bar a:hover{{background:#1e2030 !important}}
  </style>
</head>
<body>

<header>
  <div>
    <h1>Jack vs Louis &mdash; <span>Sub-20 5k</span></h1>
    <div style="font-size:12px;color:var(--subtle);margin-top:4px">The reckoning. Live-updated daily from Strava, Garmin &amp; Notion.</div>
  </div>
  <div class="sync-badge">Synced: <b>{sync_date}</b></div>
</header>

<div class="intro-banner">
  <p>For too long, <strong>Louis</strong> has occupied a smug position as the faster Sibley. This ends. Jack is on a structured, data-backed, entirely serious campaign to run a 5k in under 20 minutes &mdash; which would, incidentally, comprehensively end Louis&rsquo;s reign at the top of the family fitness charts.</p>
  <p>Jack started from <strong>27:11</strong> in January 2025. He&rsquo;s now at <strong>{pb_time}</strong>, shaving time off with each outing, training with intent, and logging more miles per month than he has in years. Louis, meanwhile, <em>has apparently been &ldquo;almost at 20 minutes&rdquo; for the better part of a year</em>. The gap is closing. The plan is live. The scoreboard is below.</p>
</div>

<div class="card hero-card" style="margin-bottom:16px">
  <div class="card-label">5k Goal Progress &mdash; Strava Best Effort Splits</div>
  <div class="hero-times">
    <div>
      <div class="hero-time-big">{pb_time}</div>
      <div class="hero-time-sub">Current best effort &nbsp;&middot;&nbsp; {pb_date} &nbsp;&middot;&nbsp; Strava segment</div>
    </div>
    <div class="hero-gap">
      <div class="hero-gap-num">&minus;{gap_str}</div>
      <div class="hero-gap-sub">still to cut &nbsp;&middot;&nbsp; need {gap_km}/km faster</div>
    </div>
  </div>
  <div class="progress-track">
    <div class="progress-fill" style="width:{progress_pct}%"></div>
    <div class="milestone-dot" style="left:34%"></div>
    <div class="milestone-dot" style="left:58%"></div>
    <div class="milestone-dot" style="left:79%"></div>
    <div class="target-line"></div>
  </div>
  <div class="progress-labels">
    <span>27:11 <span style="color:var(--muted)">start</span></span>
    <span style="color:var(--muted)">25:00</span>
    <span style="color:var(--muted)">23:00</span>
    <span style="color:var(--muted)">21:30</span>
    <span style="color:var(--target)">20:00 &#10022;</span>
  </div>
  <div class="stats-row">
    <div class="stat-item"><div class="stat-val" style="color:var(--jack)">{pb_pace}<span style="font-size:13px;font-weight:400">/km</span></div><div class="stat-lbl">Current pace</div></div>
    <div class="stat-item"><div class="stat-val" style="color:var(--target)">4:00<span style="font-size:13px;font-weight:400">/km</span></div><div class="stat-lbl">Target pace</div></div>
    <div class="stat-item"><div class="stat-val">{saved_str}</div><div class="stat-lbl">Saved since Jan &rsquo;25</div></div>
    <div class="stat-item"><div class="stat-val">{ytd_runs}</div><div class="stat-lbl">Runs YTD</div></div>
    <div class="stat-item"><div class="stat-val">{ytd_km}<span style="font-size:13px;font-weight:400">km</span></div><div class="stat-lbl">Distance YTD</div></div>
    <div class="stat-item"><div class="stat-val">{r4w_km}<span style="font-size:13px;font-weight:400">km</span></div><div class="stat-lbl">Last 4 weeks</div></div>
  </div>
</div>

<p class="section-title">The Race</p>
<div class="grid col-2" style="margin-bottom:16px">
  <div class="card rival-card rival-jack">
    <div class="rival-name">Jack</div>
    <div class="rival-pb">{pb_time}</div>
    <div class="rival-meta">{pb_pace} /km &nbsp;&middot;&nbsp; {pb_date} &nbsp;&middot;&nbsp; Strava best effort</div>
    <div class="rival-sub">All-time Garmin record: 24:44 (Aug 2023, Waltham Forest) &mdash; it&rsquo;s in there somewhere</div>
    <div class="rival-delta delta-behind">{gap_str} from goal</div>
  </div>
{louis_card_html}
</div>

{map_html}

<p class="section-title">5k Best Effort Progression</p>
<div class="card" style="margin-bottom:16px">
  <div class="card-label">Strava 5k segment splits over time &mdash; toward 20:00</div>
  <div class="chart-wrap" style="height:210px"><canvas id="paceChart"></canvas></div>
  <div style="margin-top:12px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
    <div class="legend">
      <div class="legend-item"><div class="legend-dot" style="background:var(--jack)"></div> Jack (Strava best efforts)</div>
      <div class="legend-item"><div class="legend-dot" style="background:var(--louis);opacity:.5;border:1px dashed var(--louis)"></div> Louis (~21:00)</div>
      <div class="legend-item"><div class="legend-dot" style="background:var(--target)"></div> Sub-20 target</div>
    </div>
    <div style="font-size:11px;color:var(--subtle)">{saved_str} saved &nbsp;&middot;&nbsp; {gap_str} to go</div>
  </div>
</div>

<p class="section-title">The Plan &mdash; 8-Week Programme <span style="font-weight:400;color:var(--muted)">{plan_source}</span></p>
<div class="card next-up-card" style="margin-bottom:16px">
  <div class="card-label" style="color:var(--jack)">Coming up this week</div>
  <div class="next-up-item">
    <div class="session-badge badge-intervals">Intervals</div>
    <div><div class="next-up-desc">10 &times; 400m @ 4:40/km &nbsp;&middot;&nbsp; 200m jog recoveries</div><div class="session-meta">Target: 1:52 per rep</div></div>
    <div class="next-up-date">Tue May 27</div>
  </div>
  <div class="next-up-item">
    <div class="session-badge badge-easy">Easy</div>
    <div><div class="next-up-desc">5km easy @ 6:05/km</div><div class="session-meta">Recovery effort</div></div>
    <div class="next-up-date">Thu May 29</div>
  </div>
  <div class="next-up-item">
    <div class="session-badge badge-parkrun">Parkrun</div>
    <div><div class="next-up-desc">Parkrun &mdash; target 24:12</div><div class="session-meta">4:50/km &nbsp;&middot;&nbsp; Week 1 test</div></div>
    <div class="next-up-date">Sat May 31</div>
  </div>
</div>

<div class="card" style="margin-bottom:16px">
  <div class="card-label">Parkrun target progression &mdash; when does Jack pass Louis?</div>
  <div class="chart-wrap" style="height:210px"><canvas id="planChart"></canvas></div>
  <div style="margin-top:10px;font-size:12px;color:var(--subtle);text-align:center">
    &#128205; Louis overtaken at <strong style="color:var(--jack)">Week 7 (July 11)</strong> &mdash; assuming he stays stuck at ~21:00
  </div>
</div>

<div class="grid col-2" style="margin-bottom:16px">
  <div class="card">
    <div class="card-label">Weeks 1&ndash;4</div>
    <div class="plan-week"><div class="plan-week-title">Week 1 &nbsp;&middot;&nbsp; May 26&ndash;31<span class="parkrun-target">Parkrun: 24:12</span></div>
      <div class="plan-session"><span class="session-badge badge-intervals">Int</span><div><div class="session-text">10 &times; 400m @ 4:40/km</div><div class="session-meta">Tue May 27</div></div></div>
      <div class="plan-session"><span class="session-badge badge-easy">Easy</span><div><div class="session-text">5km @ 6:05/km</div><div class="session-meta">Thu May 29</div></div></div>
      <div class="plan-session"><span class="session-badge badge-parkrun">PR</span><div><div class="session-text">Parkrun target 24:12</div><div class="session-meta">Sat May 31</div></div></div></div>
    <div class="plan-week"><div class="plan-week-title">Week 2 &nbsp;&middot;&nbsp; Jun 2&ndash;7<span class="parkrun-target">Parkrun: 23:29</span></div>
      <div class="plan-session"><span class="session-badge badge-intervals">Int</span><div><div class="session-text">8 &times; 400m @ 4:32/km</div><div class="session-meta">Tue Jun 3</div></div></div>
      <div class="plan-session"><span class="session-badge badge-easy">Easy</span><div><div class="session-text">5.5km @ 5:57/km</div><div class="session-meta">Thu Jun 5</div></div></div>
      <div class="plan-session"><span class="session-badge badge-parkrun">PR</span><div><div class="session-text">Parkrun target 23:29</div><div class="session-meta">Sat Jun 7</div></div></div></div>
    <div class="plan-week"><div class="plan-week-title">Week 3 &nbsp;&middot;&nbsp; Jun 9&ndash;14<span class="parkrun-target">Parkrun: 22:50</span></div>
      <div class="plan-session"><span class="session-badge badge-intervals">Int</span><div><div class="session-text">6 &times; 600m @ 4:24/km</div><div class="session-meta">Tue Jun 10</div></div></div>
      <div class="plan-session"><span class="session-badge badge-easy">Easy</span><div><div class="session-text">6km @ 5:49/km</div><div class="session-meta">Thu Jun 12</div></div></div>
      <div class="plan-session"><span class="session-badge badge-parkrun">PR</span><div><div class="session-text">Parkrun target 22:50</div><div class="session-meta">Sat Jun 14</div></div></div></div>
    <div class="plan-week" style="margin-bottom:0"><div class="plan-week-title">Week 4 &nbsp;&middot;&nbsp; Jun 16&ndash;21<span class="parkrun-target">Parkrun: 22:15</span></div>
      <div class="plan-session"><span class="session-badge badge-intervals">Int</span><div><div class="session-text">5 &times; 800m @ 4:18/km</div><div class="session-meta">Tue Jun 17</div></div></div>
      <div class="plan-session"><span class="session-badge badge-easy">Easy</span><div><div class="session-text">6.5km @ 5:42/km</div><div class="session-meta">Thu Jun 19</div></div></div>
      <div class="plan-session"><span class="session-badge badge-parkrun">PR</span><div><div class="session-text">Parkrun target 22:15</div><div class="session-meta">Sat Jun 21</div></div></div></div>
  </div>
  <div class="card">
    <div class="card-label">Weeks 5&ndash;8</div>
    <div class="plan-week"><div class="plan-week-title">Week 5 &nbsp;&middot;&nbsp; Jun 23&ndash;28<span class="parkrun-target">Parkrun: 21:45</span></div>
      <div class="plan-session"><span class="session-badge badge-intervals">Int</span><div><div class="session-text">Pyramid: 400/800/1200/800/400m @ 4:12/km</div><div class="session-meta">Tue Jun 24</div></div></div>
      <div class="plan-session"><span class="session-badge badge-easy">Easy</span><div><div class="session-text">7km @ 5:36/km</div><div class="session-meta">Thu Jun 26</div></div></div>
      <div class="plan-session"><span class="session-badge badge-parkrun">PR</span><div><div class="session-text">Parkrun target 21:45</div><div class="session-meta">Sat Jun 28</div></div></div></div>
    <div class="plan-week"><div class="plan-week-title">Week 6 &nbsp;&middot;&nbsp; Jun 30&ndash;Jul 5<span class="parkrun-target">Parkrun: 21:19</span></div>
      <div class="plan-session"><span class="session-badge badge-intervals">Int</span><div><div class="session-text">5 &times; 1km @ 4:08/km</div><div class="session-meta">Tue Jul 1</div></div></div>
      <div class="plan-session"><span class="session-badge badge-easy">Easy</span><div><div class="session-text">7km @ 5:31/km</div><div class="session-meta">Thu Jul 3</div></div></div>
      <div class="plan-session"><span class="session-badge badge-parkrun">PR</span><div><div class="session-text">Parkrun target 21:19</div><div class="session-meta">Sat Jul 5</div></div></div></div>
    <div class="plan-week"><div class="plan-week-title" style="color:var(--jack)">Week 7 &nbsp;&middot;&nbsp; Jul 7&ndash;12 &#127919;<span class="parkrun-target overtake">Parkrun: 20:57 &mdash; Louis crossed</span></div>
      <div class="plan-session"><span class="session-badge badge-intervals">Int</span><div><div class="session-text">12 &times; 400m @ 4:03/km</div><div class="session-meta">Tue Jul 8</div></div></div>
      <div class="plan-session"><span class="session-badge badge-easy">Easy</span><div><div class="session-text">7.5km @ 5:26/km</div><div class="session-meta">Thu Jul 10</div></div></div>
      <div class="plan-session"><span class="session-badge badge-parkrun">PR</span><div><div class="session-text">Parkrun target 20:57</div><div class="session-meta">Sat Jul 12</div></div></div></div>
    <div class="plan-week" style="margin-bottom:0"><div class="plan-week-title">Week 8 &nbsp;&middot;&nbsp; Jul 14&ndash;19<span class="parkrun-target overtake">Parkrun: 20:40</span></div>
      <div class="plan-session"><span class="session-badge badge-intervals">Int</span><div><div class="session-text">6 &times; 400m @ 4:00/km &mdash; goal pace</div><div class="session-meta">Tue Jul 15</div></div></div>
      <div class="plan-session"><span class="session-badge badge-easy">Easy</span><div><div class="session-text">6km @ 5:23/km</div><div class="session-meta">Thu Jul 17</div></div></div>
      <div class="plan-session"><span class="session-badge badge-parkrun">PR</span><div><div class="session-text">Parkrun target 20:40</div><div class="session-meta">Sat Jul 19</div></div></div></div>
  </div>
</div>

<p class="section-title">Activity Volume</p>
<div class="card" style="margin-bottom:16px">
  <div class="card-label">Weekly Distance (km) &mdash; last 5 weeks</div>
  <div class="chart-wrap" style="height:170px"><canvas id="weeklyDistChart"></canvas></div>
</div>

<p class="section-title">Last 7 Days &mdash; Health</p>
<div class="grid col-2" style="margin-bottom:16px">
  <div class="card">
    <div class="card-label">Daily Steps</div>
    <div class="chart-wrap" style="height:170px"><canvas id="stepsChart"></canvas></div>
    <div class="stats-row" style="margin-top:14px">
      <div class="stat-item"><div class="stat-val">{avg_steps:,}</div><div class="stat-lbl">{len(steps_data)}-day avg</div></div>
      <div class="stat-item"><div class="stat-val">{best_step_val:,}</div><div class="stat-lbl">Best ({best_step_date})</div></div>
      <div class="stat-item"><div class="stat-val" style="color:var(--warn)">{days_goal_met}/{len(steps_data)}</div><div class="stat-lbl">Goal days met</div></div>
    </div>
  </div>
  <div class="card">
    <div class="card-label">Sleep Quality &mdash; Last 5 Nights</div>
    {sleep_rows_html}
    <div class="legend" style="margin-top:12px">
      <div class="legend-item"><div class="legend-dot" style="background:#6366f1"></div> Deep</div>
      <div class="legend-item"><div class="legend-dot" style="background:#64748b"></div> Light</div>
      <div class="legend-item"><div class="legend-dot" style="background:#a78bfa"></div> REM</div>
    </div>
  </div>
</div>

<p class="section-title">Recent Runs</p>
<div class="card" style="margin-bottom:4px;padding:12px 0 0">
  <table class="run-table">
    <thead><tr>
      <th>Date</th><th>Run</th><th>Dist</th>
      <th>5k Split <span class="seg-badge">seg</span></th>
      <th>Pace /km</th><th>Avg HR</th><th>Elev</th>
    </tr></thead>
    <tbody>
{run_rows_html}
    </tbody>
  </table>
  <div class="table-note" style="padding-bottom:12px">5k Split = Strava best-effort segment recorded during that activity, not total activity time.</div>
</div>

<footer>
  Data: Strava (best-effort segments) &nbsp;&middot;&nbsp; Garmin Connect &nbsp;&middot;&nbsp; Notion (Running Plan)<br>
  Auto-synced daily at 04:00 &nbsp;&middot;&nbsp; Louis&rsquo;s data: manual entry until he enters the modern age
</footer>

<script>
Chart.defaults.color='#7a7f9a';
Chart.defaults.borderColor='#1e2030';
Chart.defaults.font.family='-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif';
function mmss(s){{const m=Math.floor(s/60);return m+':'+(s%60).toString().padStart(2,'0');}}

new Chart(document.getElementById('paceChart'),{{
  type:'line',
  data:{{
    labels:{trend_labels},
    datasets:[
      {{label:"Jack",data:{trend_data},borderColor:'#f97316',backgroundColor:'rgba(249,115,22,0.07)',borderWidth:2.5,pointBackgroundColor:'#f97316',pointRadius:5,tension:0.3,spanGaps:true,fill:true}},
      {louis_chart_dataset},
      {{label:'Target',data:{trend_labels}.map(()=>{GOAL_SECS}),borderColor:'#22c55e',borderDash:[4,3],borderWidth:1.5,pointRadius:0,fill:false}}
    ]
  }},
  options:{{responsive:true,maintainAspectRatio:false,
    scales:{{y:{{min:1150,max:1700,ticks:{{callback:v=>mmss(v),stepSize:60}},grid:{{color:'#1e2030'}}}},x:{{grid:{{display:false}}}}}},
    plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:c=>mmss(c.raw)}}}}}}
  }}
}});

new Chart(document.getElementById('planChart'),{{
  type:'line',
  data:{{
    labels:['W1 May 31','W2 Jun 7','W3 Jun 14','W4 Jun 21','W5 Jun 28','W6 Jul 5','W7 Jul 12','W8 Jul 19'],
    datasets:[
      {{label:'Target',data:[1452,1409,1370,1335,1305,1279,1257,1240],borderColor:'#f97316',backgroundColor:'rgba(249,115,22,0.06)',borderWidth:2.5,pointRadius:5,tension:0.3,fill:true}},
      {{label:'Louis',data:[1260,1260,1260,1260,1260,1260,1260,1260],borderColor:'rgba(59,130,246,0.5)',borderDash:[6,4],borderWidth:2,pointRadius:0,fill:false}},
      {{label:'Goal',data:[1200,1200,1200,1200,1200,1200,1200,1200],borderColor:'#22c55e',borderDash:[4,3],borderWidth:1.5,pointRadius:0,fill:false}}
    ]
  }},
  options:{{responsive:true,maintainAspectRatio:false,
    scales:{{y:{{min:1150,max:1500,ticks:{{callback:v=>mmss(v),stepSize:60}},grid:{{color:'#1e2030'}}}},x:{{grid:{{display:false}}}}}},
    plugins:{{legend:{{display:false}},tooltip:{{callbacks:{{label:c=>mmss(c.raw)}}}}}}
  }}
}});

new Chart(document.getElementById('weeklyDistChart'),{{
  type:'bar',
  data:{{
    labels:{week_labels},
    datasets:[{{
      data:{week_dist},
      backgroundColor:{week_dist}.map((v,i,a)=>i===a.length-1?'rgba(249,115,22,0.8)':'rgba(249,115,22,0.35)'),
      borderRadius:4
    }}]
  }},
  options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},
    scales:{{y:{{grid:{{color:'#1e2030'}},ticks:{{callback:v=>v+'km'}}}},x:{{grid:{{display:false}}}}}}
  }}
}});

new Chart(document.getElementById('stepsChart'),{{
  type:'bar',
  data:{{
    labels:{step_labels},
    datasets:[
      {{label:'Steps',data:{step_counts},backgroundColor:{step_colors},borderRadius:4}},
      {{label:'Goal',data:{step_goals},type:'line',borderColor:'rgba(234,179,8,0.5)',borderDash:[4,3],borderWidth:1.5,pointRadius:0,fill:false}}
    ]
  }},
  options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},
    scales:{{y:{{grid:{{color:'#1e2030'}},ticks:{{callback:v=>(v/1000).toFixed(0)+'k'}}}},x:{{grid:{{display:false}}}}}}
  }}
}});
</script>
{map_js}
</body>
</html>"""
    OUTPUT.write_text(html)
    print(f"Written {OUTPUT} ({OUTPUT.stat().st_size // 1024}KB)")


# ─── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Fetching Strava…")
    strava = fetch_strava()
    print(f"  PB: {mmss(strava['best_efforts'][0]['secs']) if strava['best_efforts'] else 'none'}, "
          f"{len(strava['activities'])} activities")

    print("Reading Garmin DB…")
    sleep_data = garmin_sleep(days=5)
    steps_data = garmin_steps(days=7)
    runs       = garmin_recent_runs(days=35)
    print(f"  {len(sleep_data)} sleep nights, {len(steps_data)} step days, {len(runs)} runs")

    print("Fetching Louis's Strava…")
    louis_strava = fetch_louis_strava()
    if louis_strava:
        louis_pb = louis_strava["best_efforts"][0]["secs"] if louis_strava["best_efforts"] else None
        print(f"  Louis PB: {mmss(louis_pb) if louis_pb else 'no best efforts yet'}, "
              f"{len(louis_strava['activities'])} activities")
    else:
        print("  Louis not yet connected (run exchange_louis_code.py once he sends his code)")

    print("Fetching Notion plan…")
    notion_lines = fetch_notion_plan()
    print(f"  {len(notion_lines) if notion_lines else 0} blocks")

    print("Generating HTML…")
    generate(strava, louis_strava, sleep_data, steps_data, runs, notion_lines)
    print("Done.")
