#!/usr/bin/env python3
from __future__ import annotations
"""
alerts.py — Alerty Techniczne dla Swetrowo Market Brief
========================================================
Generuje alerty dla spółek GPW (WIG20/mWIG40) i zagranicznych (DJIA):
  - RSI < 30  → Oversold (sygnał kupna)
  - RSI > 70  → Overbought (sygnał sprzedaży)
  - Golden Cross: MA50 przecina MA200 od dołu
  - Death Cross: MA50 przecina MA200 od góry
  - Wolumen > 2× średniej 20-dniowej → Spike wolumenu
  - Cena ≥ 52-tygodniowe maksimum (new high)
  - Cena ≤ 52-tygodniowe minimum (new low)

Źródła danych:
  - historical_data.db     → GPW (WIG20 + mWIG40)
  - historical_data_zagr.db → DJIA i inne zagraniczne

Zapis: Reports/Alerts/alerts_YYYYMMDD_HHMMSS.json
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ============================================================================
# KONFIGURACJA
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "Reports"
ALERTS_DIR = REPORTS_DIR / "Alerts"

DB_GPW  = BASE_DIR / "historical_data.db"
DB_ZAGR = BASE_DIR / "historical_data_zagr.db"

# Progi alertów
RSI_OVERSOLD    = 30
RSI_OVERBOUGHT  = 70
RSI_PERIOD      = 14
MA_SHORT        = 50
MA_LONG         = 200
VOLUME_MULT     = 2.0   # Wolumen > 2× średniej 20-dniowej
VOLUME_AVG_DAYS = 20
HIGH52_WINDOW   = 252   # ~1 rok sesji giełdowych (252 dni)

MIN_ROWS = 210  # Potrzebujemy co najmniej MA200 + paru dni buforu


# ============================================================================
# WSKAŹNIKI
# ============================================================================

def calc_rsi(close: pd.Series, period: int = RSI_PERIOD) -> float | None:
    """Zwraca ostatnią wartość RSI(14)."""
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain  = delta.where(delta > 0, 0.0)
    loss  = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs  = avg_gain / avg_loss.replace(0, 0.001)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def calc_ma(close: pd.Series, window: int) -> pd.Series:
    return close.rolling(window=window).mean()


def check_golden_death_cross(close: pd.Series) -> str | None:
    """
    Golden Cross: MA50 > MA200 dzisiaj, ale MA50 <= MA200 wczoraj.
    Death Cross:  MA50 < MA200 dzisiaj, ale MA50 >= MA200 wczoraj.
    Zwraca 'golden', 'death' lub None.
    """
    if len(close) < MA_LONG + 2:
        return None
    ma50  = calc_ma(close, MA_SHORT)
    ma200 = calc_ma(close, MA_LONG)
    if ma50.iloc[-2] is None or ma200.iloc[-2] is None:
        return None
    today_golden   = ma50.iloc[-1] > ma200.iloc[-1]
    yesterday_gold = ma50.iloc[-2] > ma200.iloc[-2]
    if today_golden and not yesterday_gold:
        return "golden"
    if not today_golden and yesterday_gold:
        return "death"
    return None


def check_volume_spike(volume: pd.Series) -> tuple[bool, float]:
    """
    Sprawdza czy ostatni wolumen > VOLUME_MULT × średniej 20-dniowej.
    Zwraca (bool, ratio).
    """
    if len(volume) < VOLUME_AVG_DAYS + 1:
        return False, 0.0
    avg_vol = volume.iloc[-(VOLUME_AVG_DAYS + 1):-1].mean()
    last_vol = float(volume.iloc[-1])
    if avg_vol == 0:
        return False, 0.0
    ratio = round(last_vol / avg_vol, 2)
    return ratio >= VOLUME_MULT, ratio


def check_52w_high_low(close: pd.Series) -> str | None:
    """
    Sprawdza czy ostatnia cena jest nowym max/min 52-tygodniowym.
    Zwraca '52w_high', '52w_low' lub None.
    """
    window = min(HIGH52_WINDOW, len(close))
    if window < 20:
        return None
    period = close.iloc[-window:]
    last = float(close.iloc[-1])
    if last >= float(period.max()):
        return "52w_high"
    if last <= float(period.min()):
        return "52w_low"
    return None


# ============================================================================
# ANALIZA JEDNEJ SPÓŁKI
# ============================================================================

def analyze_ticker(conn: sqlite3.Connection, ticker: str, market: str) -> list[dict]:
    """Analizuje jedną spółkę i zwraca listę wygenerowanych alertów."""
    try:
        df = pd.read_sql_query(
            "SELECT date, close, volume FROM prices WHERE ticker = ? ORDER BY date ASC",
            conn,
            params=(ticker,),
        )
    except Exception:
        try:
            df = pd.read_sql_query(
                "SELECT date, close, volume FROM stock_prices WHERE ticker = ? ORDER BY date ASC",
                conn,
                params=(ticker,),
            )
        except Exception as e:
            return []

    if df.empty or len(df) < 30:
        return []

    df["close"]  = pd.to_numeric(df["close"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    df = df.dropna(subset=["close"])

    close  = df["close"]
    volume = df["volume"]
    last_price = float(close.iloc[-1])
    last_date  = str(df["date"].iloc[-1])

    alerts = []

    # RSI
    rsi = calc_rsi(close)
    if rsi is not None:
        if rsi < RSI_OVERSOLD:
            alerts.append({
                "ticker":   ticker,
                "market":   market,
                "type":     "RSI_OVERSOLD",
                "label":    f"RSI = {rsi:.1f} (< {RSI_OVERSOLD})",
                "signal":   "buy",
                "severity": "high",
                "rsi":      round(rsi, 2),
                "price":    last_price,
                "date":     last_date,
            })
        elif rsi > RSI_OVERBOUGHT:
            alerts.append({
                "ticker":   ticker,
                "market":   market,
                "type":     "RSI_OVERBOUGHT",
                "label":    f"RSI = {rsi:.1f} (> {RSI_OVERBOUGHT})",
                "signal":   "sell",
                "severity": "high",
                "rsi":      round(rsi, 2),
                "price":    last_price,
                "date":     last_date,
            })

    # Golden / Death Cross (tylko jeśli wystarczająco danych)
    if len(close) >= MIN_ROWS:
        cross = check_golden_death_cross(close)
        if cross == "golden":
            alerts.append({
                "ticker":   ticker,
                "market":   market,
                "type":     "GOLDEN_CROSS",
                "label":    "Golden Cross (MA50 ↑ MA200)",
                "signal":   "buy",
                "severity": "medium",
                "rsi":      round(rsi, 2) if rsi else None,
                "price":    last_price,
                "date":     last_date,
            })
        elif cross == "death":
            alerts.append({
                "ticker":   ticker,
                "market":   market,
                "type":     "DEATH_CROSS",
                "label":    "Death Cross (MA50 ↓ MA200)",
                "signal":   "sell",
                "severity": "medium",
                "rsi":      round(rsi, 2) if rsi else None,
                "price":    last_price,
                "date":     last_date,
            })

    # Volume Spike
    spike, ratio = check_volume_spike(volume)
    if spike:
        alerts.append({
            "ticker":       ticker,
            "market":       market,
            "type":         "VOLUME_SPIKE",
            "label":        f"Wolumen {ratio:.1f}× powyżej średniej",
            "signal":       "watch",
            "severity":     "medium",
            "volume_ratio": ratio,
            "price":        last_price,
            "date":         last_date,
        })

    # 52-week high/low
    hl = check_52w_high_low(close)
    if hl == "52w_high":
        alerts.append({
            "ticker":   ticker,
            "market":   market,
            "type":     "52W_HIGH",
            "label":    "Nowe 52-tygodniowe maksimum",
            "signal":   "watch",
            "severity": "low",
            "rsi":      round(rsi, 2) if rsi else None,
            "price":    last_price,
            "date":     last_date,
        })
    elif hl == "52w_low":
        alerts.append({
            "ticker":   ticker,
            "market":   market,
            "type":     "52W_LOW",
            "label":    "Nowe 52-tygodniowe minimum",
            "signal":   "watch",
            "severity": "low",
            "rsi":      round(rsi, 2) if rsi else None,
            "price":    last_price,
            "date":     last_date,
        })

    return alerts


# ============================================================================
# ANALIZA BAZY DANYCH
# ============================================================================

def get_tickers(conn: sqlite3.Connection) -> list[str]:
    for table in ("prices", "stock_prices"):
        try:
            df = pd.read_sql_query(f"SELECT DISTINCT ticker FROM {table}", conn)
            return df["ticker"].tolist()
        except Exception:
            continue
    return []


def analyze_database(db_path: Path, market: str) -> list[dict]:
    """Analizuje wszystkie spółki w danej bazie i zwraca łączną listę alertów."""
    if not db_path.exists():
        print(f"  ⚠  Baza danych nie istnieje: {db_path}")
        return []

    all_alerts = []
    try:
        conn = sqlite3.connect(str(db_path))
        tickers = get_tickers(conn)
        if not tickers:
            print(f"  ⚠  Brak tickerów w bazie: {db_path.name}")
            conn.close()
            return []

        print(f"  → Analizuję {len(tickers)} spółek ({market}) ...")
        for ticker in tickers:
            alerts = analyze_ticker(conn, ticker, market)
            all_alerts.extend(alerts)

        conn.close()
    except Exception as e:
        print(f"  ❌ Błąd bazy {db_path.name}: {e}")

    return all_alerts


# ============================================================================
# ZAPIS I WYŚWIETLANIE
# ============================================================================

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

def save_alerts(all_alerts: list[dict]) -> Path:
    ALERTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = ALERTS_DIR / f"alerts_{ts}.json"

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_alerts": len(all_alerts),
        "alerts": sorted(all_alerts, key=lambda a: (SEVERITY_ORDER.get(a.get("severity", "low"), 2), a["ticker"])),
    }
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path


SIGNAL_EMOJI = {"buy": "🟢", "sell": "🔴", "watch": "🟡"}


def print_alerts(all_alerts: list[dict]):
    print(f"\n{'='*65}")
    print(f"  ALERTY TECHNICZNE — łącznie: {len(all_alerts)}")
    print(f"{'='*65}")

    by_severity = {"high": [], "medium": [], "low": []}
    for a in all_alerts:
        by_severity.setdefault(a.get("severity", "low"), []).append(a)

    for severity, label in [("high", "🔥 WYSOKIE"), ("medium", "⚠️  ŚREDNIE"), ("low", "ℹ️  NISKIE")]:
        group = by_severity.get(severity, [])
        if not group:
            continue
        print(f"\n{label} ({len(group)}):")
        for a in group:
            emoji = SIGNAL_EMOJI.get(a["signal"], "•")
            print(f"  {emoji} {a['ticker']:<8} [{a['market']:<5}] {a['label']}")

    print(f"{'='*65}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "=" * 65)
    print("  ALERTS.PY — Swetrowo Market Brief")
    print("=" * 65)

    all_alerts = []

    print("\n📊 GPW (WIG20/mWIG40):")
    gpw_alerts = analyze_database(DB_GPW, "GPW")
    all_alerts.extend(gpw_alerts)

    print("\n🌍 Zagraniczne (DJIA/zagr):")
    zagr_alerts = analyze_database(DB_ZAGR, "ZAGR")
    all_alerts.extend(zagr_alerts)

    print_alerts(all_alerts)

    out_path = save_alerts(all_alerts)
    print(f"\n✅ Alerty zapisane: {out_path}")
    print(f"   Łącznie alertów: {len(all_alerts)}")


if __name__ == "__main__":
    main()
