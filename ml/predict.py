"""
predict.py

Loads the trained per-blood-group models (from ml/model/) and produces
7-day and 14-day ahead forecasts, saved to ml/predictions/.

Forecasting is done recursively (a.k.a. iterative multi-step forecasting):
to predict day T+2 we need lag_1 = the (predicted) value for day T+1, since
the true value doesn't exist yet. This is standard practice for lag-feature
tree models but means forecast error compounds with horizon -- day 14's
forecast is built partly on day 1-13's own forecasts, not ground truth.
This is disclosed explicitly in the output and in the limitations section.

Usage:
    python predict.py --data supply_data.csv --horizon 14
    python predict.py --horizon 7
"""

import argparse
import os

import joblib
import numpy as np
import pandas as pd

from feature_engineering import (
    load_raw, get_series_for_group, add_calendar_features,
    add_lag_and_rolling_features, FEATURE_COLUMNS, BLOOD_GROUPS,
    LAGS, ROLLING_WINDOWS,
)

MODEL_DIR = os.path.join(os.path.dirname(__file__), "model")
PRED_DIR = os.path.join(os.path.dirname(__file__), "predictions")


def forecast_group(blood_group: str, data_path: str, horizon: int) -> pd.DataFrame:
    bundle = joblib.load(os.path.join(MODEL_DIR, f"model_{blood_group}.joblib"))
    model = bundle["model"]
    model_name = bundle["model_name"]

    raw = load_raw(data_path)
    series = get_series_for_group(raw, blood_group)[["date", "units_collected"]].copy()

    history = series.copy()  # will grow with each recursive step's prediction
    forecasts = []

    for step in range(1, horizon + 1):
        next_date = history["date"].max() + pd.Timedelta(days=1)
        working = pd.concat(
            [history, pd.DataFrame({"date": [next_date], "units_collected": [np.nan]})],
            ignore_index=True,
        )
        working = add_calendar_features(working)
        working = add_lag_and_rolling_features(working)
        row = working.iloc[[-1]]

        missing = row[FEATURE_COLUMNS].isna().any(axis=1).iloc[0]
        if missing:
            raise RuntimeError(
                f"Feature row for {next_date.date()} has NaNs; need more history."
            )

        pred = float(model.predict(row[FEATURE_COLUMNS])[0])
        pred = max(0.0, pred)  # units collected can't be negative

        forecasts.append({"date": next_date, "blood_group": blood_group,
                           "forecast_units": round(pred, 1), "horizon_day": step,
                           "model_used": model_name})

        # feed the prediction back in as if it were observed, so lag/rolling
        # features for the *next* step can be computed
        history = pd.concat(
            [history, pd.DataFrame({"date": [next_date], "units_collected": [pred]})],
            ignore_index=True,
        )

    return pd.DataFrame(forecasts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
    "--data",
    default=os.path.join(os.path.dirname(__file__), "..", "data", "real", "supply_data.csv")
)
    parser.add_argument("--horizon", type=int, default=14, choices=range(1, 31))
    args = parser.parse_args()

    os.makedirs(PRED_DIR, exist_ok=True)
    all_forecasts = []
    for bg in BLOOD_GROUPS:
        fc = forecast_group(bg, args.data, args.horizon)
        all_forecasts.append(fc)
        print(f"\n{bg} - next {args.horizon} days (model: {fc['model_used'].iloc[0]}):")
        print(fc[["date", "forecast_units"]].to_string(index=False))

    combined = pd.concat(all_forecasts, ignore_index=True)

    out_path = os.path.join(PRED_DIR, f"forecast_{args.horizon}day.csv")
    combined.to_csv(out_path, index=False)
    print(f"\nSaved combined forecast to {out_path}")

    # also emit the standard 7-day and 14-day files explicitly if this run
    # covers them, so downstream systems can rely on fixed filenames
    if args.horizon >= 7:
        combined[combined["horizon_day"] <= 7].to_csv(
            os.path.join(PRED_DIR, "forecast_7day.csv"), index=False)
    if args.horizon >= 14:
        combined[combined["horizon_day"] <= 14].to_csv(
            os.path.join(PRED_DIR, "forecast_14day.csv"), index=False)


if __name__ == "__main__":
    main()
