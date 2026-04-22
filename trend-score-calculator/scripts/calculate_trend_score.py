#!/usr/bin/env python3
"""
计算指定标的过去 N 个交易日的 Trend Score。
默认参数: ma_short=5, ma_mid=10, ma_long=20, 其他采用项目默认值。
数据源优先级: iFinD > efinance > akshare > local parquet
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


def safe_float(value, default=0.0):
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return default
        return float(value)
    except Exception:
        return default


def atr(df: pd.DataFrame, period: int = 20) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


def efficiency_ratio(series: pd.Series, period: int = 10) -> pd.Series:
    if series.empty:
        return pd.Series(dtype=float)
    change = (series - series.shift(period)).abs()
    volatility = series.diff().abs().rolling(period, min_periods=1).sum()
    er = change / volatility.replace(0, np.nan)
    return er.fillna(0.0)


def calculate_trend_score(bars: pd.DataFrame, cfg: dict) -> dict:
    n_short = int(cfg.get("n_short", 5))
    n_mid = int(cfg.get("n_mid", 10))
    n_long = int(cfg.get("n_long", 20))
    atr_period = int(cfg.get("atr_period", 20))
    min_bars = max(n_long, atr_period) + 2

    if bars.empty or len(bars) < min_bars:
        return {
            "ok": False,
            "reason": "insufficient_bars",
            "trend_score": 0.0,
            "price_direction": 0.0,
            "confidence": 0.0,
            "atr": 0.0,
            "price": 0.0,
            "ma_mid": 0.0,
        }

    price = pd.to_numeric(bars["close"], errors="coerce")
    high = pd.to_numeric(bars["high"], errors="coerce")
    low = pd.to_numeric(bars["low"], errors="coerce")
    volume = pd.to_numeric(bars["volume"], errors="coerce").fillna(0.0)

    calc_df = pd.DataFrame(
        {"close": price, "high": high, "low": low, "volume": volume}
    ).dropna(subset=["close", "high", "low"])

    if len(calc_df) < min_bars:
        return {
            "ok": False,
            "reason": "invalid_bars_after_cleanup",
            "trend_score": 0.0,
            "price_direction": 0.0,
            "confidence": 0.0,
            "atr": 0.0,
            "price": 0.0,
            "ma_mid": 0.0,
        }

    atr_series = atr(calc_df, period=atr_period)
    atr_now = safe_float(atr_series.iloc[-1], default=0.0)
    if atr_now <= 0:
        return {
            "ok": False,
            "reason": "invalid_atr",
            "trend_score": 0.0,
            "price_direction": 0.0,
            "confidence": 0.0,
            "atr": 0.0,
            "price": safe_float(calc_df["close"].iloc[-1], 0.0),
            "ma_mid": 0.0,
        }

    weights_bias = np.array(
        [
            safe_float(cfg.get("w_bias_short", 0.4), 0.4),
            safe_float(cfg.get("w_bias_mid", 0.4), 0.4),
            safe_float(cfg.get("w_bias_long", 0.2), 0.2),
        ]
    )
    weights_slope = np.array(
        [
            safe_float(cfg.get("w_slope_short", 0.4), 0.4),
            safe_float(cfg.get("w_slope_mid", 0.4), 0.4),
            safe_float(cfg.get("w_slope_long", 0.2), 0.2),
        ]
    )

    bias_parts: list[float] = []
    slope_parts: list[float] = []
    close_series = calc_df["close"]

    for n in (n_short, n_mid, n_long):
        ma_n = close_series.rolling(n, min_periods=n).mean().iloc[-1]
        bias_n = (
            (close_series.iloc[-1] - ma_n) / atr_now if pd.notna(ma_n) else 0.0
        )
        ema_n = close_series.ewm(span=n, adjust=False).mean()
        slope_n = 0.0
        if len(ema_n) >= 2:
            slope_n = (ema_n.iloc[-1] - ema_n.iloc[-2]) / (atr_now * n)
        bias_parts.append(safe_float(bias_n))
        slope_parts.append(safe_float(slope_n))

    bias_mix = float(np.dot(weights_bias, np.array(bias_parts)))
    slope_mix = float(np.dot(weights_slope, np.array(slope_parts)))

    norm_bias = float(np.tanh(bias_mix / 2.0) * 100.0)
    norm_slope = float(np.tanh(slope_mix) * 100.0)

    w_bias_norm = safe_float(cfg.get("w_bias_norm", 0.5), 0.5)
    w_slope_norm = safe_float(cfg.get("w_slope_norm", 0.5), 0.5)
    price_direction = w_bias_norm * norm_bias + w_slope_norm * norm_slope

    vol_ma_period = int(cfg.get("vol_ma_period", 20))
    er_period = int(cfg.get("er_period", 10))

    vol_ma = safe_float(
        calc_df["volume"].rolling(vol_ma_period, min_periods=1).mean().iloc[-1], 0.0
    )
    current_volume = safe_float(calc_df["volume"].iloc[-1], 0.0)
    vol_ratio = (current_volume / vol_ma) if vol_ma > 0 else 0.0
    volume_factor = 1.0 if vol_ratio >= 3.0 else max(vol_ratio / 3.0, 0.0)

    er_series = efficiency_ratio(close_series, period=er_period)
    er_now = float(np.clip(safe_float(er_series.iloc[-1], 0.0), 0.0, 1.0))

    w_vol = safe_float(cfg.get("w_vol", 0.3), 0.3)
    w_er = safe_float(cfg.get("w_er", 0.7), 0.7)
    confidence = float((volume_factor**w_vol) * (er_now**w_er))
    trend_score = float(np.clip(price_direction * confidence, -100.0, 100.0))

    current_price = safe_float(close_series.iloc[-1], 0.0)
    ma_mid = safe_float(close_series.rolling(n_mid, min_periods=1).mean().iloc[-1], 0.0)

    return {
        "ok": True,
        "reason": "ok",
        "trend_score": trend_score,
        "price_direction": price_direction,
        "confidence": confidence,
        "atr": atr_now,
        "price": current_price,
        "ma_mid": ma_mid,
    }


_IFIND_LOGGED_IN = False


def _ensure_ifind_path() -> None:
    """确保 iFinD DLL 路径在 PATH 中"""
    try:
        import iFinDAPI
    except ImportError:
        return
    base = Path(iFinDAPI.__file__).parent
    # Windows x64
    bin_x64 = base / "Windows" / "bin" / "x64"
    if bin_x64.exists():
        path_env = os.environ.get("PATH", "")
        if str(bin_x64) not in path_env:
            os.environ["PATH"] = str(bin_x64) + os.pathsep + path_env


def _ifind_login(username: str = "", password: str = "") -> bool:
    """登录 iFinD，返回是否成功"""
    global _IFIND_LOGGED_IN
    if _IFIND_LOGGED_IN:
        return True

    _ensure_ifind_path()
    try:
        from iFinDPy import THS_iFinDLogin
    except ImportError:
        return False

    # 优先使用环境变量中的账号
    user = username or os.environ.get("IFIND_USERNAME", "")
    pwd = password or os.environ.get("IFIND_PASSWORD", "")
    if not user or not pwd:
        return False

    try:
        result = THS_iFinDLogin(user, pwd)
        if result == 0:
            _IFIND_LOGGED_IN = True
            return True
    except Exception:
        pass
    return False


def fetch_data_ifind(symbol: str, days: int = 60) -> pd.DataFrame:
    """用 iFinD (同花顺) 获取数据"""
    _ensure_ifind_path()
    try:
        from iFinDPy import THS_HQ, THS_iFinDLogout
    except ImportError:
        return pd.DataFrame()

    if not _ifind_login():
        return pd.DataFrame()

    raw_symbol = symbol.replace(".SS", ".SH").replace(".SZ", ".SZ")
    start = (date.today() - timedelta(days=days + 10)).strftime("%Y-%m-%d")
    end = date.today().strftime("%Y-%m-%d")

    try:
        # fields: close, open, high, low, volume
        data = THS_HQ(raw_symbol, "close;open;high;low;volume", start, end)
        if not data or not isinstance(data, str):
            return pd.DataFrame()

        # iFinD returns semicolon-separated string
        lines = data.strip().split("\n")
        if len(lines) < 2:
            return pd.DataFrame()

        rows = []
        for line in lines[1:]:
            parts = line.split(";")
            if len(parts) >= 5:
                try:
                    rows.append({
                        "time": pd.to_datetime(parts[0]),
                        "close": float(parts[1]),
                        "open": float(parts[2]),
                        "high": float(parts[3]),
                        "low": float(parts[4]),
                        "volume": float(parts[5]) if len(parts) > 5 else 0.0,
                    })
                except (ValueError, IndexError):
                    continue

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame()


def fetch_data_efinance(symbol: str, days: int = 60) -> pd.DataFrame:
    """用 efinance (东方财富) 获取数据"""
    try:
        import efinance as ef
    except ImportError:
        return pd.DataFrame()

    raw_symbol = symbol.replace(".SS", "").replace(".SZ", "")
    try:
        df = ef.stock.get_quote_history(raw_symbol)
        if df is None or df.empty:
            return pd.DataFrame()

        df = df.rename(
            columns={
                "日期": "time",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
            }
        )
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").reset_index(drop=True)
        # 只保留最近 days+20 天
        cutoff = date.today() - timedelta(days=days + 20)
        df = df[df["time"] >= pd.Timestamp(cutoff)]
        return df
    except Exception:
        return pd.DataFrame()


def fetch_data_akshare(symbol: str, days: int = 60) -> pd.DataFrame:
    """用 akshare 获取数据"""
    try:
        import akshare as ak
    except ImportError:
        return pd.DataFrame()

    raw_symbol = symbol.replace(".SS", "").replace(".SZ", "")
    try:
        df = ak.fund_etf_hist_em(
            symbol=raw_symbol,
            period="daily",
            start_date=(date.today() - timedelta(days=days + 10)).strftime("%Y%m%d"),
            end_date=date.today().strftime("%Y%m%d"),
            adjust="qfq",
        )
        if df.empty:
            return pd.DataFrame()

        df = df.rename(
            columns={
                "日期": "time",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
            }
        )
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").reset_index(drop=True)
        return df
    except Exception:
        return pd.DataFrame()


def fetch_data(symbol: str, days: int = 60) -> tuple[pd.DataFrame, str]:
    """
    按优先级实时获取数据（不读取本地缓存）。
    返回 (DataFrame, source_name)
    """
    # 1. iFinD (同花顺)
    df = fetch_data_ifind(symbol, days)
    if not df.empty:
        return df, "ifind"

    # 2. efinance (东方财富)
    df = fetch_data_efinance(symbol, days)
    if not df.empty:
        return df, "efinance"

    # 3. akshare
    df = fetch_data_akshare(symbol, days)
    if not df.empty:
        return df, "akshare"

    return pd.DataFrame(), "none"


def main():
    if len(sys.argv) < 2:
        print("Usage: python calculate_trend_score.py <symbol> [days]")
        sys.exit(1)

    symbol = sys.argv[1]
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    cfg = {
        "n_short": 5,
        "n_mid": 10,
        "n_long": 20,
        "atr_period": 20,
        "w_bias_short": 0.4,
        "w_bias_mid": 0.4,
        "w_bias_long": 0.2,
        "w_slope_short": 0.4,
        "w_slope_mid": 0.4,
        "w_slope_long": 0.2,
        "w_bias_norm": 0.5,
        "w_slope_norm": 0.5,
        "vol_ma_period": 20,
        "er_period": 10,
        "w_vol": 0.3,
        "w_er": 0.7,
    }

    df, source = fetch_data(symbol, days=60)

    if df.empty:
        print(f"无法获取标的 {symbol} 的数据")
        sys.exit(1)

    min_bars = max(cfg["n_long"], cfg["atr_period"]) + 2
    if len(df) < min_bars + days:
        print(f"历史数据不足，需要至少 {min_bars + days} 天，实际只有 {len(df)} 天")
        sys.exit(1)

    results = []
    total_rows = len(df)
    for i in range(days):
        idx = total_rows - days + i
        bars = df.iloc[: idx + 1].copy()
        result = calculate_trend_score(bars, cfg)

        trade_date = pd.to_datetime(bars.iloc[-1]["time"]).strftime("%Y-%m-%d")
        results.append(
            {
                "date": trade_date,
                "close": round(result["price"], 3) if result["ok"] else None,
                "trend_score": round(result["trend_score"], 2) if result["ok"] else None,
                "price_direction": round(result["price_direction"], 2) if result["ok"] else None,
                "confidence": round(result["confidence"], 4) if result["ok"] else None,
                "atr": round(result["atr"], 4) if result["ok"] else None,
                "ma10": round(result["ma_mid"], 3) if result["ok"] else None,
            }
        )

    print(f"SYMBOL={symbol}")
    print(f"DAYS={days}")
    print(f"SOURCE={source}")
    print(f"ROWS={len(df)}")
    for r in results:
        print(
            f"{r['date']}\t"
            f"{r['close']}\t"
            f"{r['trend_score']}\t"
            f"{r['price_direction']}\t"
            f"{r['confidence']}\t"
            f"{r['atr']}\t"
            f"{r['ma10']}"
        )


if __name__ == "__main__":
    main()
