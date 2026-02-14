"""Tests for app.py error handling and validation logic."""

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

# Import the main function to test
import app


class TestAppValidation(unittest.TestCase):
    """Test validation and error handling in app.py."""

    @patch("app.st")
    @patch("app.DataLoader")
    @patch("app.PredictionUI")
    @patch("app.PredictionEngine")
    @patch("app.BettingEngine")
    def test_prediction_source_failure_falls_back_to_live_model(
        self, mock_bet_engine, mock_pred_engine, mock_ui, mock_data_loader, mock_st
    ):
        """Test that prediction source failures do not crash the app."""
        data_instance = mock_data_loader.return_value
        data_instance.load_vegas_lines.return_value = pd.DataFrame(
            [
                {
                    "game_id": "12345",
                    "home_team": "Duke",
                    "away_team": "UNC",
                    "market_spread": -5.5,
                }
            ]
        )
        data_instance.load_todays_predictions.side_effect = RuntimeError("supabase unavailable")
        data_instance.get_team_snapshot.side_effect = [{"team": "Duke"}, {"team": "UNC"}]

        pred_engine_instance = mock_pred_engine.return_value
        pred_engine_instance.active_model = {"model_id": "test"}
        pred_engine_instance.predict_spread.return_value = {
            "predicted_spread": -6.0,
            "confidence": 0.7,
            "breakdown": {},
        }

        bet_engine_instance = mock_bet_engine.return_value
        bet_engine_instance.recommend_spread.return_value = {"action": "pass"}

        ui_instance = mock_ui.return_value
        ui_instance.render_prediction_card = MagicMock()
        ui_instance.render_bet_recommendation = MagicMock()

        mock_st.sidebar.selectbox.return_value = "Conservative"

        app.main()

        ui_instance.render_prediction_card.assert_called_once()
        mock_st.sidebar.info.assert_called_once_with(
            "Precomputed predictions unavailable; using live model predictions."
        )

    @patch("app.st")
    @patch("app.DataLoader")
    @patch("app.PredictionUI")
    @patch("app.PredictionEngine")
    @patch("app.BettingEngine")
    def test_dataframe_type_validation(self, mock_bet_engine, mock_pred_engine, mock_ui, mock_data_loader, mock_st):
        """Test that non-DataFrame return values are handled correctly."""
        # Setup mock that returns None instead of DataFrame
        data_instance = mock_data_loader.return_value
        data_instance.load_vegas_lines.return_value = None
        data_instance.load_todays_predictions.return_value = pd.DataFrame()
        
        # Configure streamlit mocks
        mock_st.sidebar.selectbox.return_value = "Conservative"
        mock_st.info = MagicMock()
        
        # Call main function
        app.main()
        
        # Verify that st.info was called with "No games today"
        mock_st.info.assert_called_once_with("No games today")

    @patch("app.st")
    @patch("app.DataLoader")
    @patch("app.PredictionUI")
    @patch("app.PredictionEngine")
    @patch("app.BettingEngine")
    def test_empty_dataframe_handling(self, mock_bet_engine, mock_pred_engine, mock_ui, mock_data_loader, mock_st):
        """Test that empty DataFrames are handled correctly."""
        # Setup mock with empty DataFrame
        data_instance = mock_data_loader.return_value
        data_instance.load_vegas_lines.return_value = pd.DataFrame()
        data_instance.load_todays_predictions.return_value = pd.DataFrame()
        
        # Configure streamlit mocks
        mock_st.sidebar.selectbox.return_value = "Conservative"
        mock_st.info = MagicMock()
        
        # Call main function
        app.main()
        
        # Verify that st.info was called with "No games today"
        mock_st.info.assert_called_once_with("No games today")

    @patch("app.st")
    @patch("app.DataLoader")
    @patch("app.PredictionUI")
    @patch("app.PredictionEngine")
    @patch("app.BettingEngine")
    @patch("app.logger")
    def test_missing_team_snapshot_logging(self, mock_logger, mock_bet_engine, mock_pred_engine, mock_ui, mock_data_loader, mock_st):
        """Test that missing team snapshots are logged as warnings."""
        # Setup mock data
        data_instance = mock_data_loader.return_value
        games_df = pd.DataFrame([{
            "game_id": "12345",
            "home_team": "Duke",
            "away_team": "UNC",
            "market_spread": -5.5,
        }])
        data_instance.load_vegas_lines.return_value = games_df
        data_instance.load_todays_predictions.return_value = pd.DataFrame()
        data_instance.get_team_snapshot.side_effect = [{}, {}]  # Both return empty dicts
        
        # Configure streamlit mocks
        mock_st.sidebar.selectbox.return_value = "Conservative"
        
        # Call main function
        app.main()
        
        # Verify that logger.warning was called with the specific message
        mock_logger.warning.assert_any_call(
            "Missing team snapshot for game: home=%s, away=%s",
            "Duke",
            "UNC",
        )

    @patch("app.st")
    @patch("app.DataLoader")
    @patch("app.PredictionUI")
    @patch("app.PredictionEngine")
    @patch("app.BettingEngine")
    @patch("app.logger")
    def test_prediction_key_validation(self, mock_logger, mock_bet_engine, mock_pred_engine, mock_ui, mock_data_loader, mock_st):
        """Test that missing prediction keys are validated and logged."""
        # Setup mock data
        data_instance = mock_data_loader.return_value
        games_df = pd.DataFrame([{
            "game_id": "12345",
            "home_team": "Duke",
            "away_team": "UNC",
            "market_spread": -5.5,
        }])
        data_instance.load_vegas_lines.return_value = games_df
        data_instance.load_todays_predictions.return_value = pd.DataFrame()
        data_instance.get_team_snapshot.side_effect = [{"team": "Duke"}, {"team": "UNC"}]
        
        # Mock prediction engine to return prediction without required keys
        pred_engine_instance = mock_pred_engine.return_value
        pred_engine_instance.active_model = {"model_id": "test"}
        pred_engine_instance.predict_spread.return_value = {}  # Missing keys
        
        # Mock UI
        ui_instance = mock_ui.return_value
        ui_instance.render_prediction_card = MagicMock()
        
        # Configure streamlit mocks
        mock_st.sidebar.selectbox.return_value = "Conservative"
        
        # Call main function
        app.main()
        
        # Verify that logger.error was called for missing predicted_spread
        mock_logger.error.assert_called_once()
        args = mock_logger.error.call_args[0]
        self.assertIn("predicted_spread", args[0])
        
        # Verify that render_prediction_card was NOT called (game was skipped)
        ui_instance.render_prediction_card.assert_not_called()

    @patch("app.st")
    @patch("app.DataLoader")
    @patch("app.PredictionUI")
    @patch("app.PredictionEngine")
    @patch("app.BettingEngine")
    def test_event_id_game_id_mismatch_handling(self, mock_bet_engine, mock_pred_engine, mock_ui, mock_data_loader, mock_st):
        """Test that event_id vs game_id column mismatch is handled correctly."""
        # Setup mock data
        data_instance = mock_data_loader.return_value
        games_df = pd.DataFrame([{
            "game_id": "12345",
            "home_team": "Duke",
            "away_team": "UNC",
            "market_spread": -5.5,
        }])
        data_instance.load_vegas_lines.return_value = games_df
        
        # daily_preds has event_id column (not game_id)
        daily_preds_df = pd.DataFrame([{
            "event_id": "12345",
            "predicted_spread": -6.0,
            "confidence": 0.7,
            "breakdown": {},
        }])
        data_instance.load_todays_predictions.return_value = daily_preds_df
        
        # Mock engines
        pred_engine_instance = mock_pred_engine.return_value
        pred_engine_instance.active_model = {"model_id": "test"}
        bet_engine_instance = mock_bet_engine.return_value
        bet_engine_instance.recommend_spread.return_value = {"action": "pass"}
        
        # Mock UI
        ui_instance = mock_ui.return_value
        ui_instance.render_prediction_card = MagicMock()
        ui_instance.render_bet_recommendation = MagicMock()
        
        # Configure streamlit mocks
        mock_st.sidebar.selectbox.return_value = "Conservative"
        
        # Call main function
        app.main()
        
        # Verify that the prediction card was rendered (prediction was found via event_id)
        ui_instance.render_prediction_card.assert_called_once()


if __name__ == "__main__":
    unittest.main()
