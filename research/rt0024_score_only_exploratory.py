"""Exploratory score-only audit; NOT champion certification.
Reads ESPN/CSV/espn_team_game_logs.csv (pinned blob e9a9a8c89ad8d9eb7ff90c067f917b7372dcba3a).
No market features. Excludes target date and later from prior means.
"""
import csv
import math
from collections import defaultdict
from pathlib import Path

SOURCE = Path("ESPN/CSV/espn_team_game_logs.csv")
PINNED_SOURCE_BLOB = "e9a9a8c89ad8d9eb7ff90c067f917b7372dcba3a"
BASELINE = 72.0
SHRINK_N = 5.0
MIN_PRIOR = 2

with SOURCE.open(newline="", encoding="utf-8") as fh:
    rows = list(csv.DictReader(fh))
by_id = defaultdict(list)
for row in rows:
    by_id[row["event_id"]].append(row)

games, exclusions = [], defaultdict(int)
for event_id, group in by_id.items():
    h = next((x for x in group if x["home_away"] == "home"), None)
    a = next((x for x in group if x["home_away"] == "away"), None)
    try:
        if len(group) != 2 or not h or not a:
            exclusions["PAIR_OR_DUPLICATE"] += 1
            continue
        if h["completed"] != "True" or a["completed"] != "True":
            exclusions["NOT_FINAL"] += 1
            continue
        if not h["game_datetime_utc"]:
            exclusions["MISSING_TIME"] += 1
            continue
        hs, as_ = float(h["points_for"]), float(a["points_for"])
        if hs != float(a["points_against"]) or as_ != float(h["points_against"]):
            exclusions["INCOHERENT_SCORE"] += 1
            continue
    except (ValueError, TypeError):
        exclusions["INVALID_SCORE"] += 1
        continue
    games.append((h["game_date"], h["team"], a["team"], hs, as_))
games.sort()
by_day = defaultdict(list)
for g in games:
    by_day[g[0]].append(g)

history, observations = defaultdict(list), []
for day in sorted(by_day):
    for _, ht, at, hs, as_ in by_day[day]:
        hprev, aprev = history[ht], history[at]
        if len(hprev) < MIN_PRIOR or len(aprev) < MIN_PRIOR:
            continue
        hp = (sum(hprev) + SHRINK_N * BASELINE) / (len(hprev) + SHRINK_N)
        ap = (sum(aprev) + SHRINK_N * BASELINE) / (len(aprev) + SHRINK_N)
        observations.append((hs, as_, BASELINE, BASELINE, hp, ap, day))
    # Update AFTER all same-date predictions: conservative PIT rule.
    for _, ht, at, hs, as_ in by_day[day]:
        history[ht].append(hs)
        history[at].append(as_)

def scores(i):
    n = len(observations)
    if not n:
        return None
    team = sum(abs(o[0] - o[i]) + abs(o[1] - o[i+1]) for o in observations) / (2*n)
    margin = sum(abs((o[0]-o[1]) - (o[i]-o[i+1])) for o in observations) / n
    total = sum(abs((o[0]+o[1]) - (o[i]+o[i+1])) for o in observations) / n
    return {"team_mae":team,"margin_mae":margin,"total_mae":total}

print({"input_team_rows": len(rows),"unique_events":len(by_id),"valid_games":len(games),
       "evaluated_games":len(observations),"exclusions":dict(exclusions),
       "fixed_72":scores(2),"shrunken_prior_scores":scores(4),
       "status":"EXPLORATORY_ONLY_NOT_CANONICAL_NOT_CERTIFIED"})
