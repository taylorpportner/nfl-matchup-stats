"""Build data/nfl_matchups.json from nflverse play-by-play.

Per team, offense and defense, blended across last season and this one:

  spread   EPA/play, success rate, dropback EPA, rush EPA
  total    neutral-situation pace (sec/play), pass rate over expected,
           explosive play rate (20+ yards)
  noisy    giveaways and takeaways per game, 3rd-down rate, red-zone TD
           rate, points for / against per game

Plus quarterback EPA per dropback and CPOE, and this week's schedule
(rest, roof, surface, division game, listed starting QBs).

Blending: last season counts as PRIOR_GAMES games of evidence, so this
season's numbers take over as games accumulate (50/50 after 4 games).

Data: nflverse (https://github.com/nflverse/nflverse-data).
"""

from __future__ import annotations

import datetime as dt
import io
import json
import math
import sys
from pathlib import Path

import pandas as pd
import requests

PBP_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
           "pbp/play_by_play_{season}.csv.gz")
GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
OUT = Path(__file__).parent / "data" / "nfl_matchups.json"

PRIOR_GAMES = 4          # last season's weight, in games
PRIOR_DROPBACKS = 150    # a QB's last season weight, in dropbacks
MIN_QB_DROPBACKS = 50
SCHEDULE_DAYS = 10

PBP_COLS = [
    "game_id", "play_id", "season_type", "posteam", "defteam", "pass", "rush",
    "qb_dropback", "qb_kneel", "qb_spike", "two_point_attempt", "epa",
    "success", "yards_gained", "cpoe", "passer_player_id", "passer_player_name",
    "xpass", "qtr", "score_differential", "game_seconds_remaining",
    "fixed_drive", "fixed_drive_result", "drive_inside20", "interception",
    "fumble_lost", "third_down_converted", "third_down_failed",
]

# metric -> (side, higher_is_better). Pace rank 1 = fastest; PROE rank
# 1 = most pass-heavy; both are descriptive rather than good/bad.
METRICS = {
    "epa_play":        ("offense", True),
    "success_rate":    ("offense", True),
    "dropback_epa":    ("offense", True),
    "rush_epa":        ("offense", True),
    "explosive_rate":  ("offense", True),
    "pace_sec":        ("offense", False),
    "proe":            ("offense", True),
    "giveaways_pg":    ("offense", False),
    "third_down_rate": ("offense", True),
    "red_zone_td_rate": ("offense", True),
    "points_pg":       ("offense", True),
    "def_epa_play":       ("defense", False),
    "def_success_rate":   ("defense", False),
    "def_dropback_epa":   ("defense", False),
    "def_rush_epa":       ("defense", False),
    "def_explosive_rate": ("defense", False),
    "takeaways_pg":       ("defense", True),
    "points_allowed_pg":  ("defense", False),
}


def current_season(today: dt.date) -> int:
    return today.year if today.month >= 8 else today.year - 1


def load_pbp(season: int) -> pd.DataFrame | None:
    resp = requests.get(PBP_URL.format(season=season), timeout=120)
    if resp.status_code != 200:
        print(f"  pbp {season}: HTTP {resp.status_code} (skipped)")
        return None
    df = pd.read_csv(io.BytesIO(resp.content), compression="gzip",
                     usecols=PBP_COLS, low_memory=False)
    df = df[df.season_type == "REG"]
    print(f"  pbp {season}: {len(df)} regular-season rows")
    return df if len(df) else None


def team_season(pbp: pd.DataFrame, games: pd.DataFrame, season: int) -> pd.DataFrame:
    plays = pbp[((pbp["pass"] == 1) | (pbp["rush"] == 1))
                & pbp.epa.notna() & pbp.posteam.notna()
                & (pbp.qb_kneel != 1) & (pbp.qb_spike != 1)
                & (pbp.two_point_attempt != 1)].copy()
    plays["explosive"] = (plays.yards_gained >= 20).astype(float)
    dropbacks = plays[plays.qb_dropback == 1]
    rushes = plays[(plays.rush == 1) & (plays.qb_dropback != 1)]

    out = pd.DataFrame(index=sorted(plays.posteam.unique()))
    off, dfn = plays.groupby("posteam"), plays.groupby("defteam")
    out["games"] = off.game_id.nunique()
    out["epa_play"] = off.epa.mean()
    out["success_rate"] = off.success.mean()
    out["explosive_rate"] = off.explosive.mean()
    out["dropback_epa"] = dropbacks.groupby("posteam").epa.mean()
    out["rush_epa"] = rushes.groupby("posteam").epa.mean()
    out["def_epa_play"] = dfn.epa.mean()
    out["def_success_rate"] = dfn.success.mean()
    out["def_explosive_rate"] = dfn.explosive.mean()
    out["def_dropback_epa"] = dropbacks.groupby("defteam").epa.mean()
    out["def_rush_epa"] = rushes.groupby("defteam").epa.mean()

    # Neutral situations: first three quarters, within one score.
    neutral = plays[(plays.qtr <= 3) & (plays.score_differential.abs() <= 7)]
    out["proe"] = (neutral[neutral.xpass.notna()]
                   .assign(oe=lambda d: d["pass"] - d.xpass)
                   .groupby("posteam").oe.mean() * 100)
    ordered = plays.sort_values(["game_id", "play_id"])
    gap = ordered.groupby(["game_id", "posteam", "fixed_drive"]) \
        .game_seconds_remaining.shift(1) - ordered.game_seconds_remaining
    ordered = ordered.assign(gap=gap)
    ordered = ordered[(ordered.qtr <= 3) & (ordered.score_differential.abs() <= 7)
                      & (ordered.gap > 0) & (ordered.gap <= 60)]
    out["pace_sec"] = ordered.groupby("posteam").gap.mean()

    all_rows = pbp[pbp.posteam.notna()]
    giveaways = all_rows.assign(t=all_rows.interception.fillna(0) + all_rows.fumble_lost.fillna(0))
    out["giveaways_pg"] = giveaways.groupby("posteam").t.sum() / out.games
    out["takeaways_pg"] = giveaways.groupby("defteam").t.sum() / out.games
    third = all_rows.groupby("posteam")[["third_down_converted", "third_down_failed"]].sum()
    out["third_down_rate"] = third.third_down_converted / (
        third.third_down_converted + third.third_down_failed)
    drives = all_rows[all_rows.drive_inside20 == 1].drop_duplicates(["game_id", "posteam", "fixed_drive"])
    out["red_zone_td_rate"] = drives.assign(td=(drives.fixed_drive_result == "Touchdown").astype(float)) \
        .groupby("posteam").td.mean()

    g = games[(games.season == season) & (games.game_type == "REG") & games.result.notna()]
    home = g.rename(columns={"home_team": "team", "home_score": "pf", "away_score": "pa"})[["team", "pf", "pa"]]
    away = g.rename(columns={"away_team": "team", "away_score": "pf", "home_score": "pa"})[["team", "pf", "pa"]]
    scores = pd.concat([home, away]).groupby("team").mean()
    out["points_pg"] = scores.pf
    out["points_allowed_pg"] = scores.pa
    return out


def qb_season(pbp: pd.DataFrame) -> pd.DataFrame:
    db = pbp[(pbp.qb_dropback == 1) & pbp.epa.notna() & pbp.passer_player_id.notna()]
    grp = db.groupby("passer_player_id")
    return pd.DataFrame({
        "name": grp.passer_player_name.last(),
        "team": grp.posteam.last(),
        "dropbacks": grp.size(),
        "epa_dropback": grp.epa.mean(),
        "cpoe": grp.cpoe.mean(),
    })


def blend(prior, current, prior_weight, current_weight):
    vals = [(v, w) for v, w in ((prior, prior_weight), (current, current_weight))
            if v is not None and not (isinstance(v, float) and math.isnan(v)) and w > 0]
    if not vals:
        return None
    return sum(v * w for v, w in vals) / sum(w for _, w in vals)


def clean(v, digits=4):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), digits)


def main() -> int:
    today = dt.date.today()
    season = current_season(today)
    print(f"Building for {season} (blend with {season - 1})")
    games = pd.read_csv(GAMES_URL, low_memory=False)

    prior_pbp, cur_pbp = load_pbp(season - 1), load_pbp(season)
    if prior_pbp is None and cur_pbp is None:
        print("No play-by-play available.")
        return 1
    prior = team_season(prior_pbp, games, season - 1) if prior_pbp is not None else pd.DataFrame()
    cur = team_season(cur_pbp, games, season) if cur_pbp is not None else pd.DataFrame()

    teams = sorted(set(prior.index) | set(cur.index))
    blended = {}
    for team in teams:
        g_cur = int(cur.games.get(team, 0)) if len(cur) else 0
        row = {"games_current": g_cur}
        for m in METRICS:
            p = prior[m].get(team) if m in prior else None
            c = cur[m].get(team) if m in cur else None
            row[m] = blend(p, c, PRIOR_GAMES if p is not None else 0, g_cur)
        blended[team] = row

    ranks = {}
    for m, (_, higher_better) in METRICS.items():
        series = pd.Series({t: blended[t][m] for t in teams}, dtype="float64").dropna()
        ranks[m] = series.rank(ascending=not higher_better, method="min").astype(int).to_dict()

    team_json = {}
    for team in teams:
        entry = {"games_current": blended[team]["games_current"], "offense": {}, "defense": {}}
        for m, (side, _) in METRICS.items():
            key = m.removeprefix("def_") if side == "defense" else m
            entry[side][key] = {"value": clean(blended[team][m]), "rank": ranks[m].get(team)}
        team_json[team] = entry

    qb_prior = qb_season(prior_pbp) if prior_pbp is not None else pd.DataFrame()
    qb_cur = qb_season(cur_pbp) if cur_pbp is not None else pd.DataFrame()
    qbs = {}
    for pid in sorted(set(qb_prior.index) | set(qb_cur.index)):
        p = qb_prior.loc[pid] if pid in qb_prior.index else None
        c = qb_cur.loc[pid] if pid in qb_cur.index else None
        db_p = int(p.dropbacks) if p is not None else 0
        db_c = int(c.dropbacks) if c is not None else 0
        if db_p + db_c < MIN_QB_DROPBACKS:
            continue
        w_p = min(db_p, PRIOR_DROPBACKS)
        latest = c if c is not None else p
        qbs[pid] = {
            "name": latest["name"], "team": latest.team,
            "dropbacks_current": db_c, "dropbacks_prior": db_p,
            "epa_dropback": clean(blend(p.epa_dropback if p is not None else None,
                                        c.epa_dropback if c is not None else None, w_p, db_c)),
            "cpoe": clean(blend(p.cpoe if p is not None else None,
                                c.cpoe if c is not None else None, w_p, db_c), 2),
        }

    # Roof isn't filled in until close to kickoff; fall back to the
    # stadium's most recent known roof.
    known = games[games.roof.notna()].sort_values("gameday")
    stadium_roof = known.groupby("stadium_id").roof.last().to_dict()
    window = games[(games.season == season) & games.result.isna()
                   & (pd.to_datetime(games.gameday).dt.date >= today - dt.timedelta(days=1))
                   & (pd.to_datetime(games.gameday).dt.date <= today + dt.timedelta(days=SCHEDULE_DAYS))]
    schedule = []
    for _, g in window.iterrows():
        roof = g.roof if isinstance(g.roof, str) else stadium_roof.get(g.stadium_id)
        schedule.append({
            "game_id": g.game_id, "week": int(g.week), "gameday": g.gameday,
            "gametime": g.gametime, "away": g.away_team, "home": g.home_team,
            "away_rest": int(g.away_rest) if pd.notna(g.away_rest) else None,
            "home_rest": int(g.home_rest) if pd.notna(g.home_rest) else None,
            "div_game": bool(g.div_game) if pd.notna(g.div_game) else None,
            "roof": roof if isinstance(roof, str) else None,
            "surface": g.surface if isinstance(g.surface, str) else None,
            "stadium": g.stadium if isinstance(g.stadium, str) else None,
            "away_qb_id": g.away_qb_id if isinstance(g.away_qb_id, str) else None,
            "home_qb_id": g.home_qb_id if isinstance(g.home_qb_id, str) else None,
            "away_qb_name": g.away_qb_name if isinstance(g.away_qb_name, str) else None,
            "home_qb_name": g.home_qb_name if isinstance(g.home_qb_name, str) else None,
        })

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "season": season,
        "prior_season": season - 1,
        "prior_weight_games": PRIOR_GAMES,
        "teams": team_json,
        "qbs": qbs,
        "schedule": schedule,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Leave the file untouched when only the timestamp would change, so
    # the daily job commits real changes only.
    if OUT.exists():
        previous = json.loads(OUT.read_text())
        if {k: v for k, v in previous.items() if k != "generated_at"} == \
                {k: v for k, v in json.loads(json.dumps(payload)).items() if k != "generated_at"}:
            print("No changes since last build.")
            return 0
    OUT.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB): "
          f"{len(team_json)} teams, {len(qbs)} QBs, {len(schedule)} games")
    return 0


if __name__ == "__main__":
    sys.exit(main())
