import os
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import requests
import json

from supabase import create_client, Client

st.set_page_config(page_title="CBB Betting Model", page_icon="🏀", layout="wide")

# ============================================================
# SUPABASE STORAGE (PERSISTENT)
# ============================================================

def _get_supabase_client() -> Client:
    """
    Creates a Supabase client using Streamlit secrets (preferred) or env vars.
    Required:
      - SUPABASE_URL
      - SUPABASE_ANON_KEY
    """
    url = None
    key = None

    # Streamlit Cloud secrets
    try:
        url = st.secrets.get("SUPABASE_URL", None)
        key = st.secrets.get("SUPABASE_ANON_KEY", None)
    except Exception:
        pass

    # Env vars fallback
    if not url:
        url = os.environ.get("SUPABASE_URL")
    if not key:
        key = os.environ.get("SUPABASE_ANON_KEY")

    if not url or not key:
        st.error("Missing Supabase credentials. Set SUPABASE_URL and SUPABASE_ANON_KEY in Streamlit Secrets or env vars.")
        st.stop()

    return create_client(url, key)

@st.cache_resource
def supabase_client() -> Client:
    return _get_supabase_client()

def make_game_id(team_a: str, team_b: str, game_date: str) -> str:
    return f"{team_a}_vs_{team_b}_{game_date}".replace(" ", "_")

def sb_upsert_prediction(prediction_data: dict) -> str:
    """
    Upserts a prediction row into public.predictions.
    """
    sb = supabase_client()

    game_id = make_game_id(
        prediction_data["team_a"],
        prediction_data["team_b"],
        prediction_data["game_date"],
    )

    record = {
        "id": game_id,
        "game_date": prediction_data["game_date"],
        "team_a": prediction_data["team_a"],
        "team_b": prediction_data["team_b"],
        "ensemble_prediction": float(prediction_data["ensemble"]["prediction"]),
        "confidence": float(prediction_data["ensemble"]["confidence"]),
        "is_alpha": bool(prediction_data["ensemble"]["is_alpha"]),
        "kelly_bet": float(prediction_data["ensemble"]["kelly"]["kelly_dollars"]),
        "models": prediction_data["models"],

        # Result fields remain null until updated
        "actual_result": None,
        "actual_spread": None,
        "won": None,
        "ensemble_error": None,
        "model_accuracy": None,
    }

    sb.table("predictions").upsert(record).execute()
    return game_id

def sb_fetch_predictions(limit: int = 5000) -> list:
    sb = supabase_client()
    resp = (
        sb.table("predictions")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data or []

def sb_fetch_pending_predictions(limit: int = 5000) -> list:
    sb = supabase_client()
    resp = (
        sb.table("predictions")
        .select("*")
        .is_("actual_result", "null")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data or []

def sb_update_prediction_result(game_id: str, team_a_score: int, team_b_score: int) -> dict:
    """
    Updates a row with final score and computed accuracy fields.
    """
    sb = supabase_client()

    existing = sb.table("predictions").select("*").eq("id", game_id).limit(1).execute().data
    if not existing:
        raise ValueError("Prediction not found in Supabase.")

    pred = existing[0]

    actual_spread = float(team_a_score - team_b_score)
    ensemble_pred = float(pred.get("ensemble_prediction") or 0.0)

    ensemble_correct = (
        (ensemble_pred > 0 and actual_spread > 0)
        or (ensemble_pred < 0 and actual_spread < 0)
    )
    ensemble_error = float(abs(ensemble_pred - actual_spread))

    models = pred.get("models") or {}
    model_accuracy = {}
    for model_name, model_data in models.items():
        try:
            mp = float(model_data.get("prediction", 0))
        except Exception:
            mp = 0.0
        correct = (
            (mp > 0 and actual_spread > 0)
            or (mp < 0 and actual_spread < 0)
        )
        model_accuracy[model_name] = {
            "correct": bool(correct),
            "error": float(abs(mp - actual_spread)),
        }

    patch = {
        "actual_result": f"{team_a_score}-{team_b_score}",
        "actual_spread": actual_spread,
        "won": bool(ensemble_correct),
        "ensemble_error": ensemble_error,
        "model_accuracy": model_accuracy,
    }

    sb.table("predictions").update(patch).eq("id", game_id).execute()
    pred.update(patch)
    return pred

def sb_get_performance_stats() -> dict:
    """
    Calculates aggregate performance from Supabase rows.
    """
    rows = sb_fetch_predictions(limit=5000)
    completed = [r for r in rows if r.get("actual_result") is not None]

    if len(completed) == 0:
        return None

    ensemble_correct = sum(1 for r in completed if r.get("won") is True)
    ensemble_accuracy = ensemble_correct / len(completed)

    errors = [float(r.get("ensemble_error") or 0.0) for r in completed if r.get("ensemble_error") is not None]
    avg_error = float(np.mean(errors)) if errors else 0.0

    total_kelly = sum(float(r.get("kelly_bet") or 0.0) for r in completed)

    model_names = ["M1_Schedule", "M2_FourFactors", "M3_Bidirectional", "M4_Situational"]
    model_stats = {}
    for mn in model_names:
        correct = 0
        errs = []
        denom_rows = 0
        for r in completed:
            ma = (r.get("model_accuracy") or {}).get(mn)
            if not ma:
                continue
            denom_rows += 1
            if ma.get("correct") is True:
                correct += 1
            if ma.get("error") is not None:
                errs.append(float(ma["error"]))
        denom = max(1, denom_rows)
        model_stats[mn] = {
            "accuracy": correct / denom,
            "correct": correct,
            "total": denom,
            "avg_error": float(np.mean(errs)) if errs else None,
        }

    alpha_predictions = sum(1 for r in rows if r.get("is_alpha") is True)

    return {
        "total_predictions": len(completed),
        "ensemble_accuracy": ensemble_accuracy,
        "ensemble_correct": ensemble_correct,
        "avg_error": avg_error,
        "total_kelly_bet": total_kelly,
        "model_stats": model_stats,
        "alpha_predictions": alpha_predictions,
        "recent": completed[:10],  # newest-first already
    }

# ============================================================
# LOAD DATA
# ============================================================

@st.cache_data
def load_data():
    """Load all data sources"""
    bart = pd.read_csv("barttorvik.csv")
    games = pd.read_csv("espn_games.csv")
    teams_esp = pd.read_csv("espn_teams.csv")

    try:
        hasla = pd.read_csv("haslametrics.csv")
    except Exception:
        hasla = pd.DataFrame()

    bart.columns = [str(col).strip().lower().replace(" ", "_").replace(".", "") for col in bart.columns]
    if "adjoe" in bart.columns and "adjde" in bart.columns:
        bart["adjem"] = bart["adjoe"] - bart["adjde"]

    if len(hasla) > 0:
        hasla.columns = [str(col).strip().lower().replace(" ", "_").replace(".", "") for col in hasla.columns]

    return bart, hasla, games, teams_esp

bart_clean, hasla_clean, espn_games, espn_teams = load_data()

# ============================================================
# FETCH GAMES (TODAY + TOMORROW)
# ============================================================

@st.cache_data(ttl=1800)
def get_upcoming_games(days_ahead=2):
    """Fetch games for today and next N days"""
    all_games = []

    for day_offset in range(days_ahead):
        date = (datetime.now() + timedelta(days=day_offset)).strftime("%Y%m%d")
        url = f"http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={date}"

        try:
            response = requests.get(url, timeout=20)
            data = response.json()

            for event in data.get("events", []):
                comp = event.get("competitions", [{}])[0]
                competitors = comp.get("competitors", [])

                if len(competitors) >= 2:
                    home = competitors[0]["team"]["displayName"]
                    away = competitors[1]["team"]["displayName"]

                    all_games.append({
                        "game_id": event["id"],
                        "game_date": date,
                        "home_team": home,
                        "away_team": away,
                        "time": event.get("date", ""),
                        "status": event.get("status", {}).get("type", {}).get("description", "Scheduled"),
                        "day_label": "TODAY" if day_offset == 0 else "TOMORROW" if day_offset == 1 else f"+{day_offset} days",
                    })
        except Exception as e:
            st.error(f"Error fetching {date}: {e}")

    return pd.DataFrame(all_games)

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def safe_get(data_dict, key, default=0):
    if key in data_dict:
        val = data_dict[key]
        return float(val) if pd.notna(val) else default
    if key.lower() in data_dict:
        val = data_dict[key.lower()]
        return float(val) if pd.notna(val) else default
    key_under = key.replace(" ", "_").lower()
    if key_under in data_dict:
        val = data_dict[key_under]
        return float(val) if pd.notna(val) else default
    return default

def calculate_rolling_stats(games_df, team_name, n=7):
    team_games = games_df[
        (games_df["home_team"] == team_name) |
        (games_df["away_team"] == team_name)
    ].sort_values("date", ascending=False).head(n)

    if len(team_games) == 0:
        return {"games_played": 0, "margin_last_n": 0, "wins_last_n": 0}

    total_pts = 0
    total_opp = 0
    wins = 0

    for _, g in team_games.iterrows():
        if g["home_team"] == team_name:
            total_pts += g["home_score"]
            total_opp += g["away_score"]
            if g.get("home_win", False):
                wins += 1
        else:
            total_pts += g["away_score"]
            total_opp += g["home_score"]
            if not g.get("home_win", False):
                wins += 1

    n = len(team_games)
    return {
        "games_played": n,
        "margin_last_n": (total_pts - total_opp) / n,
        "wins_last_n": wins,
    }

# ============================================================
# MODELS (unchanged in this file)
# Note: we will update Model 1 and Model 3 in the next pass, per your request.
# ============================================================

def model1_schedule_adjusted(A, B):
    adjem_a = safe_get(A, "adjem", 0)
    adjem_b = safe_get(B, "adjem", 0)
    sos_a = safe_get(A, "sos", 100) / 100
    sos_b = safe_get(B, "sos", 100) / 100
    return (adjem_a - adjem_b) + ((sos_a ** 0.4) - (sos_b ** 0.4)) * 2

def model2_four_factors(A, B):
    efg_a = safe_get(A, "efg", 0.50)
    efg_b = safe_get(B, "efg", 0.50)
    tov_a = safe_get(A, "tov", 18)
    tov_b = safe_get(B, "tov", 18)
    orb_a = safe_get(A, "orb", 28)
    drb_b = safe_get(B, "drb", 72)
    ftr_a = safe_get(A, "ftr", 0.30)
    ftr_b = safe_get(B, "ftr", 0.30)

    efg_impact = (efg_a - efg_b) * 100
    tov_impact = (tov_b - tov_a) * 0.5
    reb_impact = (orb_a - drb_b) * 0.3
    ft_impact = (ftr_a - ftr_b) * 10

    return efg_impact * 0.4 + tov_impact * 0.3 + reb_impact * 0.2 + ft_impact * 0.1

def model3_bidirectional(A, B):
    factors = {}
    factors["efficiency"] = safe_get(A, "adjem", 0) - safe_get(B, "adjem", 0)
    factors["momentum"] = safe_get(A, "margin_last_7", 0) - safe_get(B, "margin_last_7", 0)

    tempo_a = safe_get(A, "adjt", 70)
    tempo_b = safe_get(B, "adjt", 70)
    tempo_diff = abs(tempo_a - tempo_b)
    factors["tempo"] = (tempo_a - tempo_b) * (1 - tempo_diff / 20) * 0.1

    win_a = safe_get(A, "barthag", 0.5)
    win_b = safe_get(B, "barthag", 0.5)
    factors["win_pct"] = (win_a - win_b) * 10

    # Haslametrics validation
    if len(hasla_clean) > 0:
        hasla_a = hasla_clean[hasla_clean.get("team", "") == A.get("team", "")]
        hasla_b = hasla_clean[hasla_clean.get("team", "") == B.get("team", "")]
        if len(hasla_a) > 0 and len(hasla_b) > 0:
            hasla_em_a = safe_get(hasla_a.iloc[0].to_dict(), "em", 0)
            hasla_em_b = safe_get(hasla_b.iloc[0].to_dict(), "em", 0)
            factors["hasla_validation"] = (hasla_em_a - hasla_em_b) * 0.1

    return sum(factors.values()), factors

def model4_situational(A, B):
    tempo_a = safe_get(A, "adjt", 70)
    tempo_b = safe_get(B, "adjt", 70)
    return (tempo_a - tempo_b) * 0.15

def home_court_advantage(venue):
    venues = {"Cameron Indoor Stadium": 4.2, "Allen Fieldhouse": 4.0, "Rupp Arena": 3.8}
    return venues.get(venue, 2.7)

def calculate_confidence(models, A, B):
    values = [v for v in models.values() if isinstance(v, (int, float))]
    if len(values) == 0:
        return 0.70
    variance = max(values) - min(values)
    return max(0.70, min(0.99, 1.0 - variance / 20.0))

def detect_alpha(pred, conf, models, vegas=None):
    is_alpha = False
    reasons = []

    if abs(pred) > 2.5 and conf > 0.90:
        is_alpha = True
        reasons.append(f"High conf ({conf:.0%})")

    if vegas is not None and abs(pred - vegas) > 3.0:
        is_alpha = True
        reasons.append(f"Vegas edge {abs(pred - vegas):.1f}pts")

    signs = [1 if v > 0 else -1 for v in models.values() if isinstance(v, (int, float))]
    if len(signs) > 0 and len(set(signs)) == 1 and abs(pred) > 2.0:
        is_alpha = True
        reasons.append("Unanimous")

    return {"is_alpha": is_alpha, "reasons": reasons}

def calculate_kelly_bet(win_prob, odds=-110, bankroll=1000, kelly_fraction=0.25):
    if odds > 0:
        decimal_odds = (odds / 100) + 1
    else:
        decimal_odds = (100 / abs(odds)) + 1

    b = decimal_odds - 1
    p = win_prob
    q = 1 - p

    kelly_pct = (b * p - q) / b
    kelly_pct = kelly_pct * kelly_fraction
    kelly_pct = max(0, min(kelly_pct, 0.05))

    kelly_dollars = bankroll * kelly_pct

    return {
        "kelly_pct": round(kelly_pct, 4),
        "kelly_dollars": round(kelly_dollars, 2),
        "recommended": "BET" if kelly_pct > 0.01 else "PASS",
    }

def predict_game(team_a_name, team_b_name, home_a=True, venue="default", vegas=None, bankroll=1000, game_date=None):
    """Complete prediction"""
    A = bart_clean[bart_clean["team"] == team_a_name]
    B = bart_clean[bart_clean["team"] == team_b_name]

    if len(A) == 0 or len(B) == 0:
        return None

    A_data = A.iloc[0].to_dict()
    B_data = B.iloc[0].to_dict()

    A_roll = calculate_rolling_stats(espn_games, team_a_name)
    B_roll = calculate_rolling_stats(espn_games, team_b_name)

    A_data["margin_last_7"] = A_roll["margin_last_n"]
    B_data["margin_last_7"] = B_roll["margin_last_n"]
    A_data["team"] = team_a_name
    B_data["team"] = team_b_name

    # Run models
    m1 = model1_schedule_adjusted(A_data, B_data)
    m2 = model2_four_factors(A_data, B_data)
    m3, m3_factors = model3_bidirectional(A_data, B_data)
    m4 = model4_situational(A_data, B_data)

    # Individual model predictions
    model_predictions = {}
    for model_name, model_pred in [
        ("M1_Schedule", m1),
        ("M2_FourFactors", m2),
        ("M3_Bidirectional", m3),
        ("M4_Situational", m4),
    ]:
        hc = home_court_advantage(venue)
        final_pred = model_pred + (hc if home_a else -hc)
        win_prob = 1 / (1 + 10 ** (-final_pred / 15))
        kelly = calculate_kelly_bet(win_prob, -110, bankroll, 0.25)

        model_predictions[model_name] = {
            "prediction": round(final_pred, 1),
            "win_prob": round(win_prob, 3),
            "kelly": kelly,
        }

    # Ensemble
    ensemble_pred = 0.45 * m3 + 0.25 * m1 + 0.20 * m2 + 0.10 * m4
    hc = home_court_advantage(venue)
    ensemble_pred += hc if home_a else -hc

    ensemble_win_prob = 1 / (1 + 10 ** (-ensemble_pred / 15))
    ensemble_kelly = calculate_kelly_bet(ensemble_win_prob, -110, bankroll, 0.25)

    models = {"M1_Schedule": m1, "M2_FourFactors": m2, "M3_Bidirectional": m3, "M4_Situational": m4}
    conf = calculate_confidence(models, A_data, B_data)
    alpha = detect_alpha(ensemble_pred, conf, models, vegas)

    result = {
        "game_date": game_date or datetime.now().strftime("%Y%m%d"),
        "team_a": team_a_name,
        "team_b": team_b_name,
        "ensemble": {
            "prediction": round(ensemble_pred, 1),
            "confidence": conf,
            "win_prob": round(ensemble_win_prob, 3),
            "kelly": ensemble_kelly,
            "is_alpha": alpha["is_alpha"],
            "alpha_reasons": alpha["reasons"],
        },
        "models": model_predictions,
        "vegas_edge": round(ensemble_pred - vegas, 1) if vegas is not None else None,
        "home_court": hc,
        "timestamp": datetime.now().isoformat(),
    }

    # Save prediction to Supabase
    try:
        sb_upsert_prediction(result)
    except Exception as e:
        st.error(f"Supabase save failed: {e}")

    return result

# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title("🏀 CBB Model")
st.sidebar.markdown("---")

st.sidebar.header("💰 Settings")
bankroll = st.sidebar.number_input("Bankroll ($)", value=1000, step=100)
unit = st.sidebar.number_input("Unit ($)", value=10, step=5)

st.sidebar.info(f"""
**Your Setup:**
- Bankroll: ${bankroll:,.0f}
- 1 Unit = ${unit}
- Using 25% Kelly
""")

st.sidebar.markdown("---")

page = st.sidebar.radio("Navigate", [
    "📅 Upcoming Games",
    "🎯 Single Prediction",
    "📊 Model Performance",
    "👥 Team Performance",
    "🧠 ML Insights",
    "📝 Update Results",
])

st.sidebar.markdown("---")
st.sidebar.caption(f"Updated: {datetime.now().strftime('%m/%d %I:%M%p')}")
st.sidebar.caption(f"{len(bart_clean)} teams | {len(espn_games)} games")
if len(hasla_clean) > 0:
    st.sidebar.caption(f"✅ Haslametrics: {len(hasla_clean)} teams")

# Optional Supabase debug
st.sidebar.markdown("---")
st.sidebar.subheader("Supabase Debug")
try:
    test_rows = sb_fetch_predictions(limit=3)
    st.sidebar.write(f"Rows found: {len(test_rows)}")
    if len(test_rows) > 0:
        st.sidebar.caption("Latest row id:")
        st.sidebar.code(test_rows[0].get("id", ""))
except Exception as e:
    st.sidebar.error(f"Supabase error: {e}")

# ============================================================
# PAGES
# ============================================================

if page == "📅 Upcoming Games":
    st.title("📅 Today & Tomorrow - Auto Predictions")

    upcoming = get_upcoming_games(days_ahead=2)

    if len(upcoming) == 0:
        st.warning("No games found")
    else:
        today_games = upcoming[upcoming["day_label"] == "TODAY"]
        tomorrow_games = upcoming[upcoming["day_label"] == "TOMORROW"]

        col1, col2 = st.columns(2)
        with col1:
            st.metric("Today", len(today_games))
        with col2:
            st.metric("Tomorrow", len(tomorrow_games))

        # Auto-predict all
        with st.spinner("Running predictions..."):
            all_predictions = []

            for _, game in upcoming.iterrows():
                result = predict_game(
                    game["home_team"],
                    game["away_team"],
                    True,
                    "default",
                    None,
                    bankroll,
                    game["game_date"],
                )

                if result:
                    result["game_id"] = game["game_id"]
                    result["day_label"] = game["day_label"]
                    all_predictions.append(result)

        # Summary
        alpha_count = sum(1 for p in all_predictions if p["ensemble"]["is_alpha"])
        bet_count = sum(1 for p in all_predictions if p["ensemble"]["kelly"]["recommended"] == "BET")
        total_kelly = sum(
            p["ensemble"]["kelly"]["kelly_dollars"]
            for p in all_predictions
            if p["ensemble"]["kelly"]["recommended"] == "BET"
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Alpha Signals", alpha_count)
        with col2:
            st.metric("Bet Recommendations", bet_count)
        with col3:
            st.metric("Total Kelly $", f"${total_kelly:.0f}")

        tab1, tab2 = st.tabs(["📅 TODAY", "📅 TOMORROW"])

        with tab1:
            today_preds = [p for p in all_predictions if p["day_label"] == "TODAY"]
            if len(today_preds) == 0:
                st.info("No games today")
            else:
                for pred in sorted(today_preds, key=lambda x: x["ensemble"]["confidence"], reverse=True):
                    ens = pred["ensemble"]
                    title = f"{'🚨' if ens['is_alpha'] else '📊'} {pred['team_a']} vs {pred['team_b']} | {ens['confidence']:.0%}"
                    if ens["kelly"]["recommended"] == "BET":
                        title += f" | 💰${ens['kelly']['kelly_dollars']:.0f}"

                    with st.expander(title, expanded=ens["is_alpha"]):
                        col1, col2, col3, col4 = st.columns(4)
                        with col1:
                            st.metric("Ensemble", f"{pred['team_a']} {ens['prediction']:+.1f}")
                        with col2:
                            st.metric("Confidence", f"{ens['confidence']:.0%}")
                        with col3:
                            st.metric("Win %", f"{ens['win_prob']:.1%}")
                        with col4:
                            if ens["kelly"]["recommended"] == "BET":
                                st.metric("Kelly", f"${ens['kelly']['kelly_dollars']:.0f}")
                            else:
                                st.info("PASS")

                        if ens["is_alpha"]:
                            st.success(f"ALPHA: {', '.join(ens['alpha_reasons'])}")

        with tab2:
            tomorrow_preds = [p for p in all_predictions if p["day_label"] == "TOMORROW"]
            if len(tomorrow_preds) == 0:
                st.info("No games tomorrow")
            else:
                for pred in sorted(tomorrow_preds, key=lambda x: x["ensemble"]["confidence"], reverse=True):
                    ens = pred["ensemble"]
                    title = f"{'🚨' if ens['is_alpha'] else '📊'} {pred['team_a']} vs {pred['team_b']} | {ens['confidence']:.0%}"

                    with st.expander(title):
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("Prediction", f"{pred['team_a']} {ens['prediction']:+.1f}")
                        with col2:
                            st.metric("Confidence", f"{ens['confidence']:.0%}")
                        with col3:
                            if ens["kelly"]["recommended"] == "BET":
                                st.metric("Kelly", f"${ens['kelly']['kelly_dollars']:.0f}")
                            else:
                                st.info("PASS")

elif page == "📊 Model Performance":
    st.title("📊 Model Performance")

    try:
        stats = sb_get_performance_stats()
    except Exception as e:
        stats = None
        st.error(f"Failed to load performance stats: {e}")

    if stats is None:
        st.info("No completed predictions yet. Once you update results, performance stats will show here.")
    else:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Games", stats["total_predictions"])
        with col2:
            st.metric("Ensemble Accuracy", f"{stats['ensemble_accuracy']:.1%}")
        with col3:
            st.metric("Alpha Signals", stats["alpha_predictions"])
        with col4:
            st.metric("Total Kelly Bet", f"${stats['total_kelly_bet']:.0f}")

        st.markdown("### Model Breakdown")
        model_df = pd.DataFrame([
            {
                "Model": name.replace("_", " "),
                "Accuracy": f"{data['accuracy']:.1%}",
                "Correct": f"{data['correct']}/{data['total']}",
                "Avg Error": (f"{data['avg_error']:.1f} pts" if data["avg_error"] is not None else "n/a"),
            }
            for name, data in stats["model_stats"].items()
        ])
        st.dataframe(model_df, use_container_width=True, hide_index=True)

elif page == "📝 Update Results":
    st.title("📝 Update Game Results")
    st.info("Enter final scores to update prediction accuracy (saved in Supabase).")

    try:
        pending = sb_fetch_pending_predictions()
    except Exception as e:
        pending = []
        st.error(f"Failed to load pending predictions: {e}")

    if len(pending) == 0:
        st.success("No pending predictions found (or none saved yet).")
    else:
        options = [f"{p['team_a']} vs {p['team_b']} ({p.get('game_date', '')})" for p in pending]
        game_select = st.selectbox("Select Game", options)

        if game_select:
            selected = pending[options.index(game_select)]

            col1, col2 = st.columns(2)
            with col1:
                score_a = st.number_input(f"{selected['team_a']} Score", min_value=0, value=0, step=1)
            with col2:
                score_b = st.number_input(f"{selected['team_b']} Score", min_value=0, value=0, step=1)

            if st.button("Update Result"):
                try:
                    updated = sb_update_prediction_result(selected["id"], int(score_a), int(score_b))
                    if updated:
                        st.success(f"✅ Updated! Prediction was {'✅ CORRECT' if updated.get('won') else '❌ WRONG'}")
                        st.write(f"Error: {float(updated.get('ensemble_error') or 0.0):.1f} points")
                except Exception as e:
                    st.error(f"Update failed: {e}")

else:
    st.title(page)
    st.info("Feature in development")
