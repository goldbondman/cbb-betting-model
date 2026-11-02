"""CBB experimental history-gated score-only mixture.
NOT confirmatory: thresholds inspired by same discovery sample; external PIT and multiseason certification missing.
Run from repository root; no sportsbook inputs. Source pinned blob
e9a9a8c89ad8d9eb7ff90c067f917b7372dcba3a.
"""
import csv
from collections import defaultdict
from pathlib import Path

rows=list(csv.DictReader(Path("ESPN/CSV/espn_team_game_logs.csv").open(newline="",encoding="utf-8")))
events=defaultdict(list)
for x in rows: events[x["event_id"]].append(x)
games=[]
for event_id, grp in events.items():
    h=next((x for x in grp if x["home_away"]=="home"),None)
    a=next((x for x in grp if x["home_away"]=="away"),None)
    if len(grp)!=2 or h is None or a is None: continue
    if h["completed"]!="True" or a["completed"]!="True":continue
    try:
        hs,aw=float(h["points_for"]),float(a["points_for"])
        if hs != float(a["points_against"]) or aw != float(h["points_against"]):continue
    except (TypeError,ValueError):continue
    games.append((h["game_date"],h["team"],a["team"],hs,aw))
games.sort()
bydate=defaultdict(list)
for g in games:bydate[g[0]].append(g)
history=defaultdict(list)
forecasts=[]
def smooth(hist,key):
    return (sum(r[key] for r in hist)+5*72)/(len(hist)+5)
for day in sorted(bydate):
    for _,ht,at,hs,aw in bydate[day]:
        h,a=history[ht],history[at]
        prior=(smooth(h,"f"),smooth(a,"f"))
        od=((smooth(h,"f")+smooth(a,"a"))/2,
            (smooth(a,"f")+smooth(h,"a"))/2)
        n=min(len(h),len(a))
        preds={"prior":prior,"offdef":od,
               "gate2":od if n>=2 else prior,
               "soft2":tuple((1-min(1,n/2))*p+min(1,n/2)*o for p,o in zip(prior,od)),
               "soft5":tuple((1-min(1,n/5))*p+min(1,n/5)*o for p,o in zip(prior,od))}
        forecasts.append((hs,aw,n,preds))
    # Same-date data withheld until all predictions have been generated.
    for _,ht,at,hs,aw in bydate[day]:
        history[ht].append({"f":hs,"a":aw})
        history[at].append({"f":aw,"a":hs})
for name in ("prior","offdef","gate2","soft2","soft5"):
    n=len(forecasts)
    score=margin=total=0
    for hs,aw,_,p in forecasts:
        ph,pa=p[name]
        score+=(abs(ph-hs)+abs(pa-aw))/2
        margin+=abs(ph-pa-hs+aw)
        total+=abs(ph+pa-hs-aw)
    print(name, {"n":n,"team_score_mae":round(score/n,5),
                 "margin_mae":round(margin/n,5),
                 "total_mae":round(total/n,5)})
print("STATUS: EXPLORATORY_POSTHOC_NO_CHAMPION_PROMOTION")
