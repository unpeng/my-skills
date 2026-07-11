# -*- coding: utf-8 -*-
"""
ML prediction module - trains and uses Random Forest + XGBoost models
for stock direction prediction.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.metrics import accuracy_score, classification_report
import warnings
warnings.filterwarnings("ignore")

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import RANDOM_STATE, TRAIN_RATIO
from model.technical import compute_all_indicators
from data.processor import clean_kline, compute_returns, compute_volatility, \
    compute_volume_features, generate_labels, train_test_split

# Feature columns used for prediction
FEATURE_COLS = [
    # Returns
    "ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d",
    # Volatility
    "volatility", "atr", "price_range",
    # Volume
    "vol_ratio", "vol_change",
    # MA
    "ma5_slope", "ma20_slope", "ma60_slope",
    # MACD
    "macd_dif", "macd_dea", "macd_hist",
    # RSI
    "rsi",
    # KDJ
    "kdj_k", "kdj_d", "kdj_j",
    # Bollinger
    "boll_width", "boll_pct",
]


class StockPredictor:
    """
    Stock direction predictor using ensemble of classifiers.

    Predicts whether a stock will go UP or DOWN in the next N trading days.
    """

    def __init__(self, predict_days: int = 5):
        self.predict_days = predict_days
        self.model = None
        self.is_trained = False
        self.feature_cols = FEATURE_COLS
        self.train_accuracy = 0.0
        self.test_accuracy = 0.0

    def _prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Full pipeline: clean → features → indicators → labels → dropna.

        Args:
            df: Raw K-line DataFrame

        Returns:
            DataFrame ready for model training/prediction.
        """
        df = clean_kline(df)
        if df.empty:
            return df

        df = compute_returns(df)
        df = compute_volatility(df)
        df = compute_volume_features(df)
        df = compute_all_indicators(df)
        df = generate_labels(df, self.predict_days)

        # Ensure all feature columns exist
        available = [c for c in self.feature_cols if c in df.columns]
        self.feature_cols = available

        # Replace infinity values with NaN, then fill
        for col in self.feature_cols:
            if col in df.columns:
                df[col] = df[col].replace([np.inf, -np.inf], np.nan)
                df[col] = df[col].ffill().fillna(0)

        # Drop rows where label/forward_ret are NaN (end of series)
        df = df.dropna(subset=["label", "forward_ret"])

        return df

    def train(self, df: pd.DataFrame, ratio: float = None) -> dict:
        """
        Train the prediction model on historical data.

        Args:
            df: Raw K-line DataFrame
            ratio: Train/test split ratio (default from config)

        Returns:
            Dict with training results (accuracy, feature importance).
        """
        if ratio is None:
            ratio = TRAIN_RATIO

        processed = self._prepare_data(df)
        if len(processed) < 100:
            return {"error": "数据不足，至少需要100条有效记录"}

        train_df, test_df = train_test_split(processed, ratio)

        X_train = train_df[self.feature_cols]
        y_train = train_df["label"]
        X_test = test_df[self.feature_cols]
        y_test = test_df["label"]

        # Ensemble: Random Forest + Gradient Boosting
        rf = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=10,
            random_state=RANDOM_STATE,
            n_jobs=-1,
        )
        gb = GradientBoostingClassifier(
            n_estimators=100,
            max_depth=5,
            learning_rate=0.1,
            random_state=RANDOM_STATE,
        )

        rf.fit(X_train, y_train)
        gb.fit(X_train, y_train)

        # Soft voting ensemble
        self.rf_model = rf
        self.gb_model = gb
        self.model = "ensemble"
        self.is_trained = True

        # Evaluate
        rf_pred = rf.predict(X_test)
        gb_pred = gb.predict(X_test)

        # Ensemble prediction (majority vote)
        ensemble_pred = ((rf_pred + gb_pred) >= 1).astype(int)

        self.train_accuracy = accuracy_score(y_train, rf.predict(X_train))
        self.test_accuracy = accuracy_score(y_test, ensemble_pred)

        # Feature importance (from Random Forest)
        importance = pd.Series(
            rf.feature_importances_, index=self.feature_cols
        ).sort_values(ascending=False)

        return {
            "train_accuracy": round(self.train_accuracy, 4),
            "test_accuracy": round(self.test_accuracy, 4),
            "train_samples": len(train_df),
            "test_samples": len(test_df),
            "feature_importance": importance.head(10).to_dict(),
        }

    def predict(self, df: pd.DataFrame) -> dict:
        """
        Predict stock direction for the latest data point.

        Args:
            df: Raw K-line DataFrame (same format as training data)

        Returns:
            Dict with prediction results (direction, probability, confidence).
        """
        if not self.is_trained:
            return {"error": "模型尚未训练，请先调用 train() 方法"}

        processed = self._prepare_data(df)
        if processed.empty:
            return {"error": "数据为空"}

        latest = processed.iloc[-1:]
        X = latest[self.feature_cols]

        # Get probabilities from both models
        rf_proba = self.rf_model.predict_proba(X)[0]
        gb_proba = self.gb_model.predict_proba(X)[0]

        # Average probabilities
        avg_proba = (rf_proba + gb_proba) / 2

        direction = "上涨" if avg_proba[1] > 0.5 else "下跌"
        confidence = max(avg_proba) * 100
        up_prob = avg_proba[1] * 100
        down_prob = avg_proba[0] * 100

        # Current technical state
        row = latest.iloc[0]
        rsi = row.get("rsi", None)
        macd_hist = row.get("macd_hist", None)

        result = {
            "direction": direction,
            "up_probability": round(up_prob, 2),
            "down_probability": round(down_prob, 2),
            "confidence": round(confidence, 2),
            "rsi": round(rsi, 2) if rsi is not None else None,
            "macd_hist": round(macd_hist, 4) if macd_hist is not None else None,
            "close": round(row["close"], 2),
            "date": str(row.name.date()) if hasattr(row.name, "date") else str(row.name),
        }

        return result

    def train_and_predict(self, df: pd.DataFrame) -> dict:
        """
        Convenience method: train on historical data, then predict latest.

        Args:
            df: Raw K-line DataFrame

        Returns:
        """
        train_result = self.train(df)
        if "error" in train_result:
            return train_result

        predict_result = self.predict(df)

        return {
            "train": train_result,
            "prediction": predict_result,
        }
