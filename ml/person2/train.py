"""
train.py

Trains and compares forecasting models per blood group:
    - Baseline 1: naive lag-1 (tomorrow = today)
    - Baseline 2: 7-day moving average
    - RandomForestRegressor
    - GradientBoostingRegressor
    - XGBRegressor

Splitting is strictly chronological:
    train:      everything before the validation window
    validation: last VAL_DAYS days before the test window (used for model selection)
    test:       last TEST_DAYS days (final, untouched-until-evaluation holdout)

No shuffling anywhere. Lag/rolling features for the val/test windows are
computed by feature_engineering.py directly from the full series with
shift() applied first, so no row in val/test uses same-day or future
information from itself or later dates.

Usage:
    python train.py --data supply_data.csv
    python train.py --data supply_data.csv --test-days 60 --val-days 60
"""

import argparse
import json
import os
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error

try:
    from xgboost import XGBRegressor
    HAS_XGB = True
except ImportError:
    HAS_XGB = False

from feature_engineering import (
    build_feature_table, FEATURE_COLUMNS, TARGET_COLUMN, BLOOD_GROUPS,
)

warnings.filterwarnings("ignore")

MODEL_DIR = os.path.join(os.path.dirname(__file__), "model")
PRED_DIR = os.path.join(os.path.dirname(__file__), "predictions")


def mape(y_true, y_pred):
    """MAPE is only meaningful where y_true != 0. Days with 0 actual units
    are excluded from this specific metric (division by zero is undefined),
    and we report what fraction of rows were excluded so the number isn't
    silently misleading."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mask = y_true != 0
    excluded = (~mask).sum()
    if mask.sum() == 0:
        return np.nan, excluded
    val = np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100
    return val, excluded


def chronological_split(df: pd.DataFrame, test_days: int, val_days: int):
    df = df.sort_values("date").reset_index(drop=True)
    n = len(df)
    test_start = n - test_days
    val_start = test_start - val_days
    train = df.iloc[:val_start].copy()
    val = df.iloc[val_start:test_start].copy()
    test = df.iloc[test_start:].copy()
    return train, val, test


def evaluate(y_true, y_pred):
    mae = mean_absolute_error(y_true, y_pred)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    mape_val, excluded = mape(y_true, y_pred)
    return {
        "MAE": round(float(mae), 3),
        "RMSE": round(float(rmse), 3),
        "MAPE_%": None if np.isnan(mape_val) else round(float(mape_val), 3),
        "MAPE_excluded_zero_actuals": int(excluded),
    }


def baseline_naive_lag1(test_df: pd.DataFrame):
    """Prediction for day T = actual value on day T-1 (already in lag_1 col)."""
    return test_df["lag_1"].values


def baseline_moving_average_7(test_df: pd.DataFrame):
    """Prediction for day T = mean of the preceding 7 days (rolling_mean_7,
    which is already computed with shift(1) so it excludes day T)."""
    return test_df["rolling_mean_7"].values


def get_models():
    models = {
        "RandomForest": RandomForestRegressor(
            n_estimators=300, max_depth=10, min_samples_leaf=3,
            random_state=42, n_jobs=-1,
        ),
        "GradientBoosting": GradientBoostingRegressor(
            n_estimators=300, max_depth=3, learning_rate=0.05,
            subsample=0.8, random_state=42,
        ),
    }
    if HAS_XGB:
        models["XGBoost"] = XGBRegressor(
            n_estimators=400, max_depth=4, learning_rate=0.03,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42, n_jobs=-1, verbosity=0,
        )
    return models


def train_for_group(blood_group: str, data_path: str, test_days: int, val_days: int):
    print(f"\n{'=' * 60}\nBlood group: {blood_group}\n{'=' * 60}")
    feat = build_feature_table(data_path, blood_group)
    feat = feat.dropna(subset=FEATURE_COLUMNS + [TARGET_COLUMN]).reset_index(drop=True)

    train_df, val_df, test_df = chronological_split(feat, test_days, val_days)
    print(f"Train: {train_df['date'].min().date()} -> {train_df['date'].max().date()} ({len(train_df)} rows)")
    print(f"Val:   {val_df['date'].min().date()} -> {val_df['date'].max().date()} ({len(val_df)} rows)")
    print(f"Test:  {test_df['date'].min().date()} -> {test_df['date'].max().date()} ({len(test_df)} rows)")

    X_train, y_train = train_df[FEATURE_COLUMNS], train_df[TARGET_COLUMN]
    X_val, y_val = val_df[FEATURE_COLUMNS], val_df[TARGET_COLUMN]
    X_test, y_test = test_df[FEATURE_COLUMNS], test_df[TARGET_COLUMN]

    # train+val combined is used for the final fit of the chosen model,
    # but model SELECTION happens only on the val set, and test is touched
    # exactly once at the end for the reported final numbers.
    results = {}

    results["Baseline_NaiveLag1"] = {"val": evaluate(y_val, baseline_naive_lag1(val_df))}
    results["Baseline_MovingAvg7"] = {"val": evaluate(y_val, baseline_moving_average_7(val_df))}

    # --- Step 1: model selection, using ONLY train-fitted models scored on val ---
    # This is the sole basis for picking a winner. Test data is not touched here.
    fitted_models = {}
    for name, model in get_models().items():
        model.fit(X_train, y_train)
        val_pred = model.predict(X_val)
        results[name] = {"val": evaluate(y_val, val_pred)}
        fitted_models[name] = model

    ml_names = list(fitted_models.keys())
    best_name = min(ml_names, key=lambda n: results[n]["val"]["MAE"])

    # --- Step 2: final scoring, reported for the SELECTED model only ---
    # The selected model is refit on train+val (more data, same hyperparameters)
    # and scored once on the held-out test set. This retrain-on-train+val score
    # is the number that should be treated as "the model's expected accuracy" --
    # it is what actually gets saved and deployed below.
    X_trainval = pd.concat([X_train, X_val])
    y_trainval = pd.concat([y_train, y_val])
    best_model = get_models()[best_name]
    best_model.fit(X_trainval, y_trainval)
    test_pred = best_model.predict(X_test)
    results[best_name]["test_final_after_trainval_refit"] = evaluate(y_test, test_pred)

    # --- Step 3: diagnostic-only test scores for the non-selected ML models ---
    # These use the train-only fit from Step 1 (not refit on train+val) and are
    # reported purely so the comparison table isn't missing columns. They are
    # NOT comparable apples-to-apples with the selected model's score above
    # (different training data), and they play no role in model selection --
    # selection already happened in Step 1, using validation data only.
    for name in ml_names:
        if name == best_name:
            continue
        pred = fitted_models[name].predict(X_test)
        results[name]["test_trainonly_fit_diagnostic"] = evaluate(y_test, pred)

    # Baselines need no fitting; their "test" score is on the same footing
    # (no training data at all), so it's left as a plain "test" key.
    results["Baseline_NaiveLag1"]["test"] = evaluate(y_test, baseline_naive_lag1(test_df))
    results["Baseline_MovingAvg7"]["test"] = evaluate(y_test, baseline_moving_average_7(test_df))

    print("\nValidation MAE by model (this is what determines selection):")
    for name in ["Baseline_NaiveLag1", "Baseline_MovingAvg7"] + ml_names:
        print(f"  {name:20s}: MAE={results[name]['val']['MAE']:.2f}")
    print(f"\nSelected model (lowest val MAE): {best_name}")
    print(f"Final test score (selected model, refit on train+val): "
          f"{results[best_name]['test_final_after_trainval_refit']}")

    # Persist
    os.makedirs(MODEL_DIR, exist_ok=True)
    import joblib
    model_path = os.path.join(MODEL_DIR, f"model_{blood_group}.joblib")
    joblib.dump({"model": best_model, "features": FEATURE_COLUMNS, "blood_group": blood_group,
                 "model_name": best_name}, model_path)

    return {
        "blood_group": blood_group,
        "best_model": best_name,
        "results": results,
        "train_range": [str(train_df["date"].min().date()), str(train_df["date"].max().date())],
        "val_range": [str(val_df["date"].min().date()), str(val_df["date"].max().date())],
        "test_range": [str(test_df["date"].min().date()), str(test_df["date"].max().date())],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=os.path.join(os.path.dirname(__file__), "supply_data.csv"))
    parser.add_argument("--test-days", type=int, default=60)
    parser.add_argument("--val-days", type=int, default=60)
    args = parser.parse_args()

    os.makedirs(PRED_DIR, exist_ok=True)
    summary = {}
    for bg in BLOOD_GROUPS:
        summary[bg] = train_for_group(bg, args.data, args.test_days, args.val_days)

    summary_path = os.path.join(PRED_DIR, "model_comparison_summary.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSaved comparison summary to {summary_path}")


if __name__ == "__main__":
    main()
