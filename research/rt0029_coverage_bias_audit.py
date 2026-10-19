"""CBB-EXP-0007 exploratory coverage/selection-bias audit. NOT certified OOS.

Source: GitHub ESPN/CSV/espn_team_game_logs.csv blob e9a9a8c89ad8d9eb7ff90c067f917b7372dcba3a
Run: python research/rt0029_coverage_bias_audit.py
No market data. All same-day games withheld until day ends. Retrospective data vintage, partial single season.
"""
import csv
from pathlib import Path
from collections import defaultdict

rows=list(csv.DictReader(Path("ESPN/CSV/espn_team_game_logs.csv").open(newline="",encoding="utf-8")))
events=defaultdict(list)
for row in rows:
    events[row["event_id"]].append(row)
games=[]
exclusions=defaultdict(int)
for event_id, pair in events.items():
    home=next((x for x in pair if x["home_away"]=="home"),None)
    away=next((x for x in pair if x["home_away"]=="away"),None)
    try:
        if len(pair)!=2 or home is None or away is None:
            exclusions["DUPLICATE_OR_PAIR"]+=1;continue
        if home["completed"]!="True" or away["completed"]!="True":
            exclusions["NOT_COMPLETED"]+=1;continue
        hs,aw=float(home["points_for"]),float(away["points_for"])
        if hs!=float(away["points_against"]) or aw!=float(home["points_against"]):
            exclusions["SCORE_MISMATCH"]+=1;continue
    except (TypeError,ValueError):
        exclusions["BAD_SCORE"]+=1;continue
    games.append((home["game_date"],home["team"],away["team"],hs,aw,home["pulled_at_utc"],home["data_ok"]=="True" and away["data_ok"]=="True"))
games.sort()
by_day=defaultdict(list)
for x in games:by_day[x[0]].append(x)
history=defaultdict(list)
evaluated,excluded=[],[]
def shrink(history,col):
    return (sum(x[col] for x in history)+5*72.)/(len(history)+5)
for day in sorted(by_day):
    for d,home,away,hs,aw,pulled,ok in by_day[day]:
        hh,aa=history[home],history[away]
        base=(d,hs,aw)
        if min(len(hh),len(aa))<2:
            excluded.append(base);continue
        prior=(shrink(hh,"pf"),shrink(aa,"pf"))
        od=((shrink(hh,"pf")+shrink(aa,"pa"))/2,(shrink(aa,"pf")+shrink(hh,"pa"))/2)
        evaluated.append((d,hs,aw,min(len(hh),len(aa)),prior,od))
    for d,home,away,hs,aw,pulled,ok in by_day[day]:
        history[home].append({"pf":hs,"pa":aw})
        history[away].append({"pf":aw,"pa":hs})
def avg(a,func): return sum(func(x) for x in a)/len(a) if a else None
def pop(a):
    return {"n":len(a),"home_win":avg(a,lambda x:float(x[1]>x[2])),
            "mean_home_margin":avg(a,lambda x:x[1]-x[2]),
            "absolute_margin_20plus":avg(a,lambda x:float(abs(x[1]-x[2])>=20))}
def diff(a,metric):
    if not a:return None
    def err(pred,z):
        hs,aw=z[1],z[2]
        return ((abs(pred[0]-hs)+abs(pred[1]-aw))/2 if metric=="team"
                else abs((pred[0]-pred[1])-(hs-aw)))
    return avg(a,lambda x:err(x[5],x)-err(x[4],x))
print("source",{"team_rows":len(rows),"events":len(events),"paired_valid":len(games),
                "excluded_events":dict(exclusions),"score_valid_both_data_ok":sum(x[6] for x in games),
                "retrospectively_pulled":sum(bool(x[5]) and x[5][:10]>x[0] for x in games)})
print("selection",{"all":pop([(x[0],x[3],x[4]) for x in games]),
                   "evaluated":pop(evaluated),"excluded":pop(excluded)})
print("by_month",[(month,pop([x for x in evaluated if x[0][:7]==month]),
                    diff([x for x in evaluated if x[0][:7]==month],"team"),
                    diff([x for x in evaluated if x[0][:7]==month],"margin"))
                   for month in ("2025-11","2025-12","2026-01")])
print("minimum_prior_strata",[(label,len(a),diff(a,"team"),diff(a,"margin"))
    for label,a in (("2_to_3",[x for x in evaluated if x[3]<=3]),
                    ("4plus",[x for x in evaluated if x[3]>=4]),
                    ("7plus",[x for x in evaluated if x[3]>=7]))])
print("status","EXPLORATORY_SELECTION_BIAS_DETECTED; CANONICAL/PIT/REPLICATION UNVERIFIED")
