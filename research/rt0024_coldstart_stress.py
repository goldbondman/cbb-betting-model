"""EXPLORATORY RT0024/RT0029 minimum-history challenger stress test.

NOT confirmatory. Partial retrospective team-game log; strict prior-date updates;
72-point fixed fallback and 5-game shrink fixed previously (not newly tuned).
No market input. No DATA001 or season replication.
Run: python research/rt0024_coldstart_stress.py
"""
import csv
from pathlib import Path
from collections import defaultdict

F = Path("ESPN/CSV/espn_team_game_logs.csv")
SOURCE_GITHUB_BLOB = "e9a9a8c89ad8d9eb7ff90c067f917b7372dcba3a"
rows=list(csv.DictReader(F.open(newline="",encoding="utf-8")))
events=defaultdict(list)
for row in rows:events[row["event_id"]].append(row)
games=[]
fail=defaultdict(int)
for game_id, recs in events.items():
    h=next((x for x in recs if x["home_away"]=="home"),None)
    a=next((x for x in recs if x["home_away"]=="away"),None)
    try:
        if len(recs)!=2 or h is None or a is None: fail["PAIR"]+=1;continue
        if h["completed"]!="True" or a["completed"]!="True": fail["NOT_FINAL"]+=1;continue
        hs,aw=float(h["points_for"]),float(a["points_for"])
        if hs!=float(a["points_against"]) or aw!=float(h["points_against"]): fail["SCORE"]+=1;continue
        games.append((h["game_date"],h["team"],a["team"],hs,aw))
    except (TypeError,ValueError):fail["INVALID"]+=1
games.sort()
days=defaultdict(list)
for x in games:days[x[0]].append(x)
history=defaultdict(list)
records=[]
def shrunk(h,k):
    return (sum(z[k] for z in h)+5*72.)/(len(h)+5)
for day in sorted(days):
    for dt,ht,at,hs,aw in days[day]:
        hh,aa=history[ht],history[at]
        prior=(shrunk(hh,"pf"),shrunk(aa,"pf"))
        od=((shrunk(hh,"pf")+shrunk(aa,"pa"))/2,
            (shrunk(aa,"pf")+shrunk(hh,"pa"))/2)
        def loss(p):
            return ((abs(p[0]-hs)+abs(p[1]-aw))/2,
                    abs((p[0]-p[1])-(hs-aw)),
                    abs((p[0]+p[1])-(hs+aw)))
        records.append((dt,min(len(hh),len(aa)),loss(prior),loss(od)))
    # Do not use any realized same-date result.
    for dt,ht,at,hs,aw in days[day]:
        history[ht].append({"pf":hs,"pa":aw})
        history[at].append({"pf":aw,"pa":hs})
def av(x):return sum(x)/len(x)
for name,subset in (
    ("all",records),("min0",[z for z in records if z[1]==0]),
    ("min1",[z for z in records if z[1]==1]),
    ("min2plus",[z for z in records if z[1]>=2]),
    ("min0to1",[z for z in records if z[1]<=1]),
    ("min4plus",[z for z in records if z[1]>=4])):
    print(name,{"n":len(subset),"dates":len({z[0] for z in subset}),
        "prior":tuple(round(av([z[2][i] for z in subset]),5) for i in range(3)),
        "offdef":tuple(round(av([z[3][i] for z in subset]),5) for i in range(3)),
        "od_minus_prior":tuple(round(av([z[3][i]-z[2][i] for z in subset]),5) for i in range(3))})
print("audit",{"team_rows":len(rows),"unique_events":len(events),"valid_games":len(games),"excluded_events":dict(fail),
               "status":"EXPLORATORY_NONCANONICAL_NOT_CERTIFIED"})
