# CBB Betting Model

College basketball prediction system using 4 ensemble models.

## Features
- Real-time predictions
- 4 model ensemble (Schedule, Four Factors, Bidirectional, Situational)
- Alpha signal detection
- Home court adjustments

## Documentation
- **[DATA_FLOW.md](DATA_FLOW.md)** - Complete documentation of data ingestion, feature engineering, predictions, and backtesting

## Data Sources
- Barttorvik (efficiency metrics)
- ESPN API (game results)

## Run Locally
```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Deploy to Streamlit Cloud
1. Push to GitHub
2. Go to streamlit.io
3. Deploy from repo

Review/testing app URL (allowlisted): https://cbb-betting-model-ddjun7mby42wjaxbjltebj.streamlit.app/
