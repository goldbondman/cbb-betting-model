"""CBB EXP-0006: noncertifying, single-partial-season score-only sensitivity audit.

RESEARCH ONLY. Not DATA001, no verified source-vintage PIT, no champion comparison.
Source: ESPN/CSV/espn_team_game_logs.csv blob e9a9a8c89ad8d9eb7ff90c067f917b7372dcba3a.
Frozen exploratory parameters (not nested tuned): baseline=72, prior shrink=5 games,
minimum previous scored games=2, home-offset shrink=20.
This is a post-hoc challenger family, NOT a confirmatory validation.
Run from repo root: python research/rt0024_offdef_sensitivity.py
"""
import csv
from collections import defaultdict
from pathlib import Path

F = Path("ESPN/CSV/espn_team_game_logs.csv")
rows = list(csv.DictReader(F.open(newline="", encoding="utf-8")))
by_id = defaultdict(list)
for x in rows:
    by_id[x["event_id"]].append(x)
valid, excluded = [], defaultdict(int)
for eid, group in by_id.items():
    h = next((x for x in group if x["home_away"] == "home"), None)
    a = next((x for x in group if x["home_away"] == "away"), None)
    try:
        if len(group) != 2 or not h or not a:
            excluded["DUPLICATE_OR_PAIR"] += 1; continue
        if h["completed"] != "True" or a["completed"] != "True":
            excluded["NOT_COMPLETED"] += 1; continue
        hs, aw = float(h["points_for"]), float(a["points_for"])
        if hs != float(a["points_against"]) or aw != float(h["points_against"]):
            excluded["SCORE_MISMATCH"] += 1; continue
        if not h["game_date"]:
            excluded["NO_DATE"] += 1; continue
        valid.append((h["game_date"], h["team"], a["team"], hs, aw))
    except (TypeError, ValueError):
        excluded["BAD_SCORE"] += 1
valid.sort()
by_day = defaultdict(list)
for g in valid:
    by_day[g[0]].append(g)
history = defaultdict(list)
n_prior, home_margin_sum = 0, 0.
observed = []
def smooth(hist, col):
    return (sum(x[col] for x in hist) + 5*72.)/(len(hist)+5)
for date in sorted(by_day):
    for _, ht, at, hs, aw in by_day[date]:
        h, a = history[ht], history[at]
        if len(h)<2 or len(a)<2: continue
        p=(smooth(h,"for"),smooth(a,"for"))
        od=((smooth(h,"for")+smooth(a,"against"))/2,
            (smooth(a,"for")+smooth(h,"against"))/2)
        h_offset = home_margin_sum / (n_prior + 20)
        preds={"fixed":(72.,72.),"prior":p,"offdef":od,
               "recent3":(smooth(h[-3:],"for"),smooth(a[-3:],"for")),
               "offdef3":((smooth(h[-3:],"for")+smooth(a[-3:],"against"))/2,
                          (smooth(a[-3:],"for")+smooth(h[-3:],"against"))/2),
               "offset":(od[0]+h_offset/2,od[1]-h_offset/2)}
        observed.append((date,hs,aw,preds))
    # All same-date scores become known only AFTER same-date forecasts.
    for _, ht, at, hs, aw in by_day[date]:
        history[ht].append({"for":hs,"against":aw})
        history[at].append({"for":aw,"against":hs})
        home_margin_sum += hs-aw
        n_prior += 1
def loss(pred,hs,aw):
    ph,pa=pred
    return ((abs(ph-hs)+abs(pa-aw))/2,
            abs((ph-pa)-(hs-aw)),abs((ph+pa)-(hs+aw)),
            ph-pa-hs+aw)
def avg(xs):return sum(xs)/len(xs)
for name in ["fixed","prior","offdef","recent3","offdef3","offset"]:
    l=[loss(preds[name],hs,aw) for _,hs,aw,preds in observed]
    print(name,len(l),*[round(avg([z[i] for z in l]),6) for i in range(4)])
print("metadata",{"team_rows":len(rows),"unique_ids":len(by_id),
                  "score_pair_valid":len(valid),"eligible":len(observed),
                  "distinct_eval_dates":len({x[0] for x in observed}),
                  "excluded":dict(excluded),"status":"EXPLORATORY_NONCERTIFYING"})
