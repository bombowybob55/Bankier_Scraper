#!/usr/bin/env python3
"""
macro_data.py — Makro Snapshot dla Swetrowo Market Brief
=========================================================
Pobiera dane makroekonomiczne:
  - Kursy FX (EUR/PLN, USD/PLN, CHF/PLN, GBP/PLN)
  - Indeksy globalne (WIG20, WIG, DAX, S&P500, NASDAQ, DJIA)
  - Surowce (Złoto, Ropa WTI, Miedź)
  - Stopy procentowe NBP (API NBP)
  - Rentowność obligacji 10Y (PL, US, DE)

Zapis: Reports/Macro/macro_snapshot_YYYYMMDD_HHMMSS.json
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False
    print("⚠  yfinance nie zainstalowane. Uruchom: pip install yfinance")

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("⚠  requests nie zainstalowane.")

# ============================================================================
# KONFIGURACJA
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "Reports"
MACRO_DIR = REPORTS_DIR / "Macro"

# Symbole yfinance
SYMBOLS = {
    # Indeksy
    "WIG20":   "^WIG20",
    "WIG":     "^WIG",
    "DAX":     "^GDAXI",
    "SP500":   "^GSPC",
    "NASDAQ":  "^IXIC",
    "DJIA":    "^DJI",
    # FX (vs PLN)
    "EURPLN":  "EURPLN=X",
    "USDPLN":  "PLN=X",
    "CHFPLN":  "CHFPLN=X",
    "GBPPLN":  "GBPPLN=X",
    # Surowce (USD)
    "GOLD":    "GC=F",
    "OIL_WTI": "CL=F",
    "COPPER":  "HG=F",
    # Obligacje 10Y
    "US10Y":   "^TNX",
    "DE10Y":   "^TMBMKDE-10Y",
}

NBP_API_URL = "https://api.nbp.pl/api/stopy/2024/?format=json"
NBP_RATES_URL = "https://api.nbp.pl/api/ceniony/a/usd/?format=json"

# ============================================================================
# POBIERANIE DANYCH
# ============================================================================

def fetch_yfinance_snapshot(symbols: dict) -> dict:
    """Pobiera ostatnią cenę i zmianę % dla listy symboli przez yfinance."""
    if not HAS_YFINANCE:
        return {}

    result = {}
    tickers_list = list(symbols.values())

    try:
        # Pobierz dane dla wszystkich symboli w jednym zapytaniu (szybciej)
        data = yf.download(
            tickers_list,
            period="5d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )

        close = data.get("Close", data) if "Close" in data.columns.get_level_values(0) else data

        for label, symbol in symbols.items():
            try:
                if len(tickers_list) == 1:
                    series = close[symbol] if symbol in close.columns else close
                else:
                    series = close[symbol]

                series = series.dropna()
                if len(series) < 2:
                    result[label] = {"price": None, "change_pct": None, "symbol": symbol}
                    continue

                price = float(series.iloc[-1])
                prev  = float(series.iloc[-2])
                change_pct = round((price - prev) / prev * 100, 3) if prev != 0 else 0.0

                result[label] = {
                    "price": round(price, 4),
                    "change_pct": change_pct,
                    "symbol": symbol,
                    "date": series.index[-1].strftime("%Y-%m-%d"),
                }
            except Exception as e:
                result[label] = {"price": None, "change_pct": None, "symbol": symbol, "error": str(e)}

    except Exception as e:
        print(f"  ❌ Błąd yfinance batch download: {e}")
        # Fallback: indywidualne zapytania
        for label, symbol in symbols.items():
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                if hist.empty or len(hist) < 2:
                    result[label] = {"price": None, "change_pct": None, "symbol": symbol}
                    continue
                price = float(hist["Close"].iloc[-1])
                prev  = float(hist["Close"].iloc[-2])
                change_pct = round((price - prev) / prev * 100, 3) if prev != 0 else 0.0
                result[label] = {
                    "price": round(price, 4),
                    "change_pct": change_pct,
                    "symbol": symbol,
                    "date": hist.index[-1].strftime("%Y-%m-%d"),
                }
            except Exception as e2:
                result[label] = {"price": None, "change_pct": None, "symbol": symbol, "error": str(e2)}

    return result


def fetch_nbp_rate() -> dict:
    """Pobiera referencyjną stopę procentową NBP."""
    if not HAS_REQUESTS:
        return {}
    try:
        # Stopa referencyjna NBP
        url = "https://api.nbp.pl/api/stopy/2024/?format=json"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            # Znajdź stopę referencyjną (ref)
            for entry in reversed(data):
                rates = entry.get("stopy", [])
                for r in rates:
                    if r.get("symbol", "").lower() in ("ref", "reference"):
                        return {
                            "rate": r.get("oprocentowanie"),
                            "date": entry.get("data"),
                            "name": "Stopa referencyjna NBP",
                        }
    except Exception:
        pass

    # Fallback: scraping z publicznego API NBP (prostszy endpoint)
    try:
        url2 = "https://api.nbp.pl/api/stopy/?format=json"
        resp2 = requests.get(url2, timeout=10)
        if resp2.status_code == 200:
            data2 = resp2.json()
            if isinstance(data2, list) and data2:
                last = data2[-1]
                stopy = last.get("stopy", [])
                for s in stopy:
                    if "ref" in s.get("symbol", "").lower():
                        return {
                            "rate": s.get("oprocentowanie"),
                            "date": last.get("data"),
                            "name": "Stopa referencyjna NBP",
                        }
    except Exception:
        pass

    return {"rate": None, "date": None, "name": "Stopa referencyjna NBP", "error": "Brak danych"}


def fetch_nbp_usd_rate() -> dict:
    """Pobiera kurs USD/PLN z tabeli kursów NBP (jako cross-check)."""
    if not HAS_REQUESTS:
        return {}
    try:
        url = "https://api.nbp.pl/api/exchangerates/rates/A/usd/?format=json"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            rates = data.get("rates", [])
            if rates:
                last = rates[-1]
                return {
                    "rate": last.get("mid"),
                    "date": last.get("effectiveDate"),
                    "currency": "USD/PLN (NBP mid)",
                }
    except Exception as e:
        pass
    return {"rate": None, "currency": "USD/PLN (NBP mid)"}


# ============================================================================
# GENEROWANIE RAPORTU
# ============================================================================

def build_snapshot(market_data: dict, nbp_rate: dict, nbp_usd: dict) -> dict:
    """Buduje ustrukturyzowany snapshot makro."""
    ts = datetime.now()

    snapshot = {
        "generated_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp_iso": ts.isoformat(),
        "indices": {},
        "fx": {},
        "commodities": {},
        "bonds": {},
        "nbp": {},
    }

    # Mapowanie label → kategoria
    categories = {
        "WIG20":   "indices",
        "WIG":     "indices",
        "DAX":     "indices",
        "SP500":   "indices",
        "NASDAQ":  "indices",
        "DJIA":    "indices",
        "EURPLN":  "fx",
        "USDPLN":  "fx",
        "CHFPLN":  "fx",
        "GBPPLN":  "fx",
        "GOLD":    "commodities",
        "OIL_WTI": "commodities",
        "COPPER":  "commodities",
        "US10Y":   "bonds",
        "DE10Y":   "bonds",
    }

    for label, cat in categories.items():
        snapshot[cat][label] = market_data.get(label, {"price": None, "change_pct": None})

    # NBP
    snapshot["nbp"]["reference_rate"] = nbp_rate
    snapshot["nbp"]["usd_mid"] = nbp_usd

    return snapshot


def save_snapshot(snapshot: dict) -> Path:
    """Zapisuje snapshot do pliku JSON."""
    MACRO_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = MACRO_DIR / f"macro_snapshot_{ts}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return out_path


def print_summary(snapshot: dict):
    """Wyświetla skrócone podsumowanie w terminalu."""
    print("\n" + "=" * 60)
    print("  MACRO SNAPSHOT")
    print("=" * 60)

    def fmt(label, data, unit=""):
        price = data.get("price")
        chg   = data.get("change_pct")
        if price is None:
            return f"  {label:<12}: N/A"
        arrow = "▲" if (chg or 0) >= 0 else "▼"
        chg_str = f"{arrow}{abs(chg or 0):.2f}%" if chg is not None else ""
        return f"  {label:<12}: {price:>12.4f} {unit}  {chg_str}"

    print("\n📈 Indeksy:")
    for k, v in snapshot["indices"].items():
        print(fmt(k, v))

    print("\n💱 FX (PLN):")
    for k, v in snapshot["fx"].items():
        print(fmt(k, v))

    print("\n🛢  Surowce:")
    for k, v in snapshot["commodities"].items():
        unit = "USD/oz" if k == "GOLD" else "USD/bbl" if k == "OIL_WTI" else "USD/lb"
        print(fmt(k, v, unit))

    print("\n📊 Obligacje 10Y:")
    for k, v in snapshot["bonds"].items():
        print(fmt(k, v, "%"))

    nbp = snapshot["nbp"].get("reference_rate", {})
    rate = nbp.get("rate")
    date = nbp.get("date", "")
    if rate is not None:
        print(f"\n🏦 NBP Stopa Ref.: {rate}%  ({date})")

    print("=" * 60)


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "=" * 60)
    print("  MACRO DATA — Swetrowo Market Brief")
    print("=" * 60)

    if not HAS_YFINANCE:
        print("❌ Wymagany yfinance. Zainstaluj: pip install yfinance")
        sys.exit(1)

    print("\n⬇  Pobieranie danych rynkowych (yfinance)...")
    market_data = fetch_yfinance_snapshot(SYMBOLS)

    print("⬇  Pobieranie stóp procentowych NBP...")
    nbp_rate = fetch_nbp_rate()
    nbp_usd  = fetch_nbp_usd_rate()

    snapshot = build_snapshot(market_data, nbp_rate, nbp_usd)
    print_summary(snapshot)

    out_path = save_snapshot(snapshot)
    print(f"\n✅ Snapshot zapisany: {out_path}")


if __name__ == "__main__":
    main()
