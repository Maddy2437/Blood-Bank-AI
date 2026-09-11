"""
feature_engineering.py

Loads the raw supply_data.csv (READ-ONLY, never modified) and turns it into
a per-blood-group daily time series with leakage-safe temporal features.

Dataset shape confirmed by inspection:
    - Columns: date, blood_group, units_collected
    - One row per (date, blood_group)
    - 4 blood groups: A, AB, B, O
    - Daily granularity, 2006-01-01 to 2026-09-04, NO missing calendar days
      for any group (verified: every group has exactly one row per date in
      its range -> no imputation of missing dates is needed).
    - 46 rows out of 30,208 have units_collected == 0 (real zero-collection
      days, not missing data -> left as-is, not imputed).
"""

import pandas as pd
import numpy as np


RAW_COLUMNS = ["date", "blood_group", "units_collected"]
BLOOD_GROUPS = ["A", "AB", "B", "O"]

LAGS = [1, 7, 14]
ROLLING_WINDOWS = [7, 14, 30]


def load_raw(csv_path: str) -> pd.DataFrame:
    """Load the raw CSV without ever writing back to it."""
    df = pd.read_csv(csv_path, parse_dates=["date"])
    missing_cols = set(RAW_COLUMNS) - set(df.columns)
    if missing_cols:
        raise ValueError(f"Unexpected schema, missing columns: {missing_cols}")
    df = df.sort_values(["blood_group", "date"]).reset_index(drop=True)
    return df


def get_series_for_group(df: pd.DataFrame, blood_group: str) -> pd.DataFrame:
    """
    Return a single blood group's daily series.

    The dataset is expected to be a complete daily calendar with no gaps
    (verified during EDA: every group has exactly one row per date across
    2006-01-01 to 2026-09-04). Rather than silently filling any missing
    date with 0 -- which would fabricate a "no collections" data point that
    never actually happened and quietly corrupt lag/rolling features and
    every downstream forecast -- this function checks for gaps explicitly
    and raises if any are found. A missing date is a data-quality problem
    that needs a human decision (drop, interpolate, investigate the
    source), not a silent default.
    """
    sub = df[df["blood_group"] == blood_group].sort_values("date").reset_index(drop=True)

    dup_dates = sub["date"][sub["date"].duplicated()]
    if len(dup_dates) > 0:
        raise ValueError(
            f"Blood group '{blood_group}' has {len(dup_dates)} duplicate date(s) "
            f"in the raw data, e.g. {sorted(dup_dates.dt.date.unique())[:5]}. "
            "Resolve duplicates before building features."
        )

    full_idx = pd.date_range(sub["date"].min(), sub["date"].max(), freq="D")
    missing_dates = full_idx.difference(sub["date"])
    if len(missing_dates) > 0:
        preview = [str(d.date()) for d in missing_dates[:10]]
        raise ValueError(
            f"Blood group '{blood_group}' is missing {len(missing_dates)} calendar "
            f"day(s) between {sub['date'].min().date()} and {sub['date'].max().date()}. "
            f"First missing date(s): {preview}. Refusing to auto-fill these with 0, "
            "since that would fabricate 'no collections' data points. Investigate the "
            "source data and decide explicitly how to handle the gap (drop the range, "
            "interpolate, or re-pull the missing days) before forecasting."
        )

    return sub[["date", "units_collected"]].assign(blood_group=blood_group)


def add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["day_of_week"] = df["date"].dt.dayofweek  # 0=Mon ... 6=Sun
    df["is_weekend"] = df["day_of_week"].isin([5, 6]).astype(int)
    df["day_of_month"] = df["date"].dt.day
    df["month"] = df["date"].dt.month
    df["year"] = df["date"].dt.year
    df["day_of_year"] = df["date"].dt.dayofyear
    df["week_of_year"] = df["date"].dt.isocalendar().week.astype(int)
    # cyclical encodings so the model sees Dec 31 -> Jan 1 as adjacent
    df["dow_sin"] = np.sin(2 * np.pi * df["day_of_week"] / 7)
    df["dow_cos"] = np.cos(2 * np.pi * df["day_of_week"] / 7)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)
    return df


def add_lag_and_rolling_features(df: pd.DataFrame, target_col: str = "units_collected") -> pd.DataFrame:
    """
    All lag/rolling features are computed using .shift() BEFORE any rolling
    aggregation, so that the feature for date T only ever uses information
    from date T-1 or earlier. This is the anti-leakage guarantee:
    rolling stats are computed on the *already-shifted* series, so a value
    at row T can never see units_collected[T].
    """
    df = df.sort_values("date").copy()

    for lag in LAGS:
        df[f"lag_{lag}"] = df[target_col].shift(lag)

    shifted = df[target_col].shift(1)  # exclude current day from all rolling windows
    for window in ROLLING_WINDOWS:
        df[f"rolling_mean_{window}"] = shifted.rolling(window=window, min_periods=window).mean()
        df[f"rolling_std_{window}"] = shifted.rolling(window=window, min_periods=window).std()
        df[f"rolling_min_{window}"] = shifted.rolling(window=window, min_periods=window).min()
        df[f"rolling_max_{window}"] = shifted.rolling(window=window, min_periods=window).max()

    # trend features: difference vs same day last week / momentum
    df["diff_lag1_lag7"] = df["lag_1"] - df["lag_7"]
    pct = df[target_col].shift(1).pct_change(periods=1)
    # pct_change is undefined (+/-inf) when the prior day's value was 0
    # (division by zero). Cap rather than drop, so a single zero-collection
    # day doesn't destroy an otherwise valid row.
    pct = pct.replace([np.inf, -np.inf], np.nan).clip(lower=-5, upper=5)
    df["pct_change_lag1"] = pct.fillna(0.0)

    return df


def build_feature_table(csv_path: str, blood_group: str) -> pd.DataFrame:
    """Full pipeline for one blood group: raw -> calendar-complete -> features."""
    raw = load_raw(csv_path)
    series = get_series_for_group(raw, blood_group)
    series = add_calendar_features(series)
    series = add_lag_and_rolling_features(series)
    return series


FEATURE_COLUMNS = (
    ["day_of_week", "is_weekend", "day_of_month", "month", "year",
     "day_of_year", "week_of_year", "dow_sin", "dow_cos", "month_sin", "month_cos"]
    + [f"lag_{l}" for l in LAGS]
    + [f"rolling_mean_{w}" for w in ROLLING_WINDOWS]
    + [f"rolling_std_{w}" for w in ROLLING_WINDOWS]
    + [f"rolling_min_{w}" for w in ROLLING_WINDOWS]
    + [f"rolling_max_{w}" for w in ROLLING_WINDOWS]
    + ["diff_lag1_lag7", "pct_change_lag1"]
)

TARGET_COLUMN = "units_collected"


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "supply_data.csv"
    for bg in BLOOD_GROUPS:
        feat = build_feature_table(path, bg)
        print(bg, feat.shape, "NaNs after feature build (expected, from lag warm-up):",
              feat[FEATURE_COLUMNS].isna().any(axis=1).sum())
