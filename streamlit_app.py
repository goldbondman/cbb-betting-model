
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime

# Page config
st.set_page_config(page_title="CBB Betting Model", page_icon="🏀", layout="wide")

# === LOAD DATA ===
@st.cache_data
def load_data():
    bart = pd.read_csv("barttorvik.csv")
    games = pd.read_csv("espn_games.csv")
    teams = pd.read_csv("espn_teams.csv")
    
    # Preprocess
    bart.columns = [str(col).strip().lower().replace(" ", "_").replace(".", "") for col in bart.columns]
    if "adjoe" in bart.columns and "adjde" in bart.columns:
        bart["adjem"] = bart["adjoe"] - bart["adjde"]
    
    return bart, games, teams

bart_clean, espn_games, espn_teams = load_data()

# === HELPER FUNCTIONS ===
def safe_get(data_dict, key, default=0):
    """Safely get value from dict"""
    if key in data_dict:
        return float(data_dict[key])
    if key.lower() in data_dict:
        return float(data_dict[key.lower()])
    key_under = key.replace(" ", "_").lower()
    if key_under in data_dict:
        return float(data_dict[key_under])
    return default

def calculate_rolling_stats(games_df, team_name, n=7):
    """Calculate last N games stats"""
    team_games = games_df[
        (games_df["home_team"] == team_name) | 
        (games_df["away_team"] == team_name)
    ].sort_values("date", ascending=False).head(n)
    
    if len(team_games) == 0:
        return {"games_played": 0, "margin_last_n": 0, "wins_last_n": 0}
    
    total_pts = total_opp = wins = 0
    for _, g in team_games.iterrows():
        if g["home_team"] == team_name:
            total_pts += g["home_score"]
            total_opp += g["away_score"]
            if g["home_win"]:
                wins += 1
        else:
            total_pts += g["away_score"]
            total_opp += g["home_score"]
            if not g["home_win"]:
                wins += 1
    
    n = len(team_games)
    return {
        "games_played": n,
        "margin_last_n": (total_pts - total_opp) / n,
        "wins_last_n": wins
    }

# === MODELS ===
def model1_schedule_adjusted(A, B):
    adjem_a = safe_get(A, "adjem", 0)
    adjem_b = safe_get(B, "adjem", 0)
    sos_a = safe_get(A, "sos", 100) / 100
    sos_b = safe_get(B, "sos", 100) / 100
    sched_adj = (sos_a ** 0.4) - (sos_b ** 0.4)
    return (adjem_a - adjem_b) + sched_adj * 2

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
    
    return sum(factors.values()), factors

def model4_situational(A, B):
    tempo_a = safe_get(A, "adjt", 70)
    tempo_b = safe_get(B, "adjt", 70)
    return (tempo_a - tempo_b) * 0.15

def home_court_advantage(venue):
    venues = {
        "Cameron Indoor Stadium": 4.2,
        "Allen Fieldhouse": 4.0,
        "Rupp Arena": 3.8,
    }
    return venues.get(venue, 2.7)

def calculate_confidence(models, A, B):
    values = [v for v in models.values() if isinstance(v, (int, float))]
    variance = max(values) - min(values)
    agreement = max(0.70, min(0.99, 1.0 - variance / 20.0))
    return round(agreement, 3)

def detect_alpha(pred, conf, models, vegas=None):
    is_alpha = False
    reasons = []
    
    if abs(pred) > 2.5 and conf > 0.90:
        is_alpha = True
        reasons.append(f"Strong confidence ({conf:.0%})")
    
    if vegas and abs(pred - vegas) > 3.0:
        is_alpha = True
        reasons.append(f"Vegas edge: {abs(pred - vegas):.1f}pts")
    
    signs = [1 if v > 0 else -1 for v in models.values() if isinstance(v, (int, float))]
    if len(set(signs)) == 1 and abs(pred) > 2.0:
        is_alpha = True
        reasons.append("Unanimous direction")
    
    return {"is_alpha": is_alpha, "reasons": reasons}

def predict_game(team_a_name, team_b_name, home_a=True, venue="default", vegas=None):
    """Main prediction function"""
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
    
    m1 = model1_schedule_adjusted(A_data, B_data)
    m2 = model2_four_factors(A_data, B_data)
    m3, m3_f = model3_bidirectional(A_data, B_data)
    m4 = model4_situational(A_data, B_data)
    
    pred = 0.45*m3 + 0.25*m1 + 0.20*m2 + 0.10*m4
    
    hc = home_court_advantage(venue)
    pred += hc if home_a else -hc
    
    models = {"M1": m1, "M2": m2, "M3": m3, "M4": m4}
    conf = calculate_confidence(models, A_data, B_data)
    alpha = detect_alpha(pred, conf, models, vegas)
    
    return {
        "team_a": team_a_name,
        "team_b": team_b_name,
        "prediction": round(pred, 1),
        "confidence": conf,
        "is_alpha": alpha["is_alpha"],
        "alpha_reasons": alpha["reasons"],
        "models": {k: round(v,1) for k,v in models.items()},
        "vegas_edge": round(pred - vegas, 1) if vegas else None,
        "home_court": hc,
        "team_a_last_7": A_roll,
        "team_b_last_7": B_roll
    }

# === UI ===
st.title("🏀 College Basketball Betting Model")

# Sidebar
st.sidebar.header("💰 Bankroll")
bankroll = st.sidebar.number_input("Total ($)", value=1000, step=100)
unit = st.sidebar.number_input("Unit ($)", value=10, step=5)
st.sidebar.info(f"1 Unit = ${unit} ({unit/bankroll*100:.1f}%)")

st.sidebar.markdown("---")
st.sidebar.caption(f"Updated: {datetime.now().strftime('%Y-%m-%d')}")
st.sidebar.caption(f"Teams: {len(bart_clean)} | Games: {len(espn_games)}")

# Main app
teams = sorted(bart_clean["team"].unique().tolist())

col1, col2 = st.columns(2)
with col1:
    team_a = st.selectbox("Home Team", teams)
with col2:
    team_b = st.selectbox("Away Team", teams, index=1)

col3, col4 = st.columns(2)
with col3:
    venue = st.selectbox("Venue", ["default", "Cameron Indoor Stadium", "Allen Fieldhouse", "Rupp Arena"])
with col4:
    vegas_line = st.number_input("Vegas Line (optional)", value=0.0, step=0.5)
    has_vegas = st.checkbox("Use Vegas line")

if st.button("🚀 Predict", type="primary"):
    if team_a == team_b:
        st.error("Select different teams!")
    else:
        with st.spinner("Running models..."):
            result = predict_game(team_a, team_b, True, venue, vegas_line if has_vegas else None)
            
            if result:
                st.markdown("---")
                st.markdown(f"## {team_a} vs {team_b}")
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Prediction", f"{team_a} {result['prediction']:+.1f}")
                with col2:
                    st.metric("Confidence", f"{result['confidence']:.0%}")
                with col3:
                    if result["is_alpha"]:
                        st.success("🚨 ALPHA")
                    else:
                        st.info("❌ No edge")
                
                st.markdown("### Models")
                model_df = pd.DataFrame({
                    "Model": ["Schedule", "Four Factors", "Bidirectional", "Situational"],
                    "Prediction": [result["models"]["M1"], result["models"]["M2"], 
                                  result["models"]["M3"], result["models"]["M4"]],
                    "Weight": ["25%", "20%", "45%", "10%"]
                })
                st.dataframe(model_df, hide_index=True)
                
                if result["is_alpha"]:
                    st.success(f"""
                    **BET RECOMMENDATION:** {team_a} {result['prediction']:+.1f}
                    
                    Reasons: {", ".join(result["alpha_reasons"])}
                    """)
                
                if has_vegas:
                    st.info(f"Vegas edge: {result['vegas_edge']:+.1f} points")
