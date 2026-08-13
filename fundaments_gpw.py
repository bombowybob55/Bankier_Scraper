#!/usr/bin/env python3
from __future__ import annotations
"""
fundaments_gpw.py — Fundamenty GPW dla Swetrowo Market Brief
=============================================================
Pobiera dane fundamentalne dla spółek WIG20 + mWIG40 z yfinance:
  - P/E (trailing & forward)
  - P/BV (Price/Book Value)
  - EV/EBITDA
  - ROE, ROA
  - Dywidenda % (TTM)
  - Kapitalizacja (Market Cap PLN)
  - Dług netto / EBITDA
  - Marża netto

Tickery GPW w Yahoo Finance mają suffix .WA (warszawa)
Przykład: PKO BP → PKO.WA, CD Projekt → CDR.WA

Zapis: Reports/Fundamental/gpw_fundamentals_YYYYMMDD_HHMMSS.csv
"""

import time
from datetime import datetime
from pathlib import Path

import pandas as pd

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False
    print("❌ Wymagany yfinance. Zainstaluj: pip install yfinance")

# ============================================================================
# KONFIGURACJA
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "Reports"
FUNDAMENTAL_DIR = REPORTS_DIR / "Fundamental"

# Mapowanie: Nazwa → Ticker Yahoo Finance (suffix .WA = Warszawa)
# WIG20 składniki (aktualne na 2026)
WIG20_TICKERS = {
    "Alior Bank":       "ALR.WA",
    "Allegro":          "ALE.WA",
    "Budimex":          "BDX.WA",
    "Modivo":           "MDV.WA",
    "CD Projekt":       "CDR.WA",
    "Dino Polska":      "DNP.WA",
    "Grupa Kęty":       "KTY.WA",
    "KGHM":             "KGH.WA",
    "Kruk":             "KRU.WA",
    "LPP":              "LPP.WA",
    "mBank":            "MBK.WA",
    "Orange Polska":    "OPL.WA",
    "Pekao":            "PEO.WA",
    "PGE":              "PGE.WA",
    "PKN Orlen":        "PKN.WA",
    "PKO BP":           "PKO.WA",
    "PZU":              "PZU.WA",
    "Erste Bank Polska": "EBP.WA",
    "Tauron":           "TPE.WA",
    "Żabka":            "ZAB.WA",
}

# mWIG40 — wybór najpłynniejszych (top 20 z mWIG40)
MWIG40_TICKERS = {
    "11 bit studios":   "11B.WA",
    "AmRest":           "EAT.WA",
    "Asseco Poland":    "ACP.WA",
    "Benefit Systems":  "BFT.WA",
    "BNP Paribas PL":   "BNP.WA",
    "Cyfrowy Polsat":   "CPS.WA",
    "Develia":          "DVL.WA",
    "Diagnostyka":      "DIA.WA",
    "Dom Development":  "DOM.WA",
    "GPW":              "GPW.WA",
    "Grupa Azoty":      "ATT.WA",
    "ING BSK":          "ING.WA",
    "Inter Cars":       "CAR.WA",
    "JSW":              "JSW.WA",
    "Millennium":       "MIL.WA",
    "Neuca":            "NEU.WA",
    "PlayWay":          "PLW.WA",
    "Vistula":          "VRG.WA",
    "Wirtualna Polska": "WPL.WA",
    "XTB":              "XTB.WA",
}

ALL_TICKERS = {**WIG20_TICKERS, **MWIG40_TICKERS}

# Opóźnienie między zapytaniami (API rate limiting)
REQUEST_DELAY = 0.6  # sekund


# ============================================================================
# POBIERANIE DANYCH
# ============================================================================

def get_val(info: dict, *keys, default=None):
    """Zwraca pierwszą niepustą wartość z listy kluczy info."""
    for key in keys:
        val = info.get(key)
        if val is not None and val != "" and str(val) != "nan":
            return val
    return default


def fetch_fundamental(name: str, yahoo_ticker: str, index_group: str) -> dict | None:
    """Pobiera dane fundamentalne dla jednej spółki."""
    print(f"  ⬇  {name} ({yahoo_ticker})...", end=" ", flush=True)
    try:
        t = yf.Ticker(yahoo_ticker)
        info = t.info

        if not info or info.get("regularMarketPrice") is None and info.get("previousClose") is None:
            # Próbuj pobrać przez history
            hist = t.history(period="5d")
            if hist.empty:
                print("❌ brak danych")
                return None

        market_cap    = get_val(info, "marketCap")
        total_debt    = get_val(info, "totalDebt", default=0) or 0
        total_cash    = get_val(info, "totalCash", default=0) or 0
        net_debt      = total_debt - total_cash
        ebitda        = get_val(info, "ebitda")
        ev            = (market_cap + net_debt) if market_cap is not None else None
        ev_ebitda     = round(ev / ebitda, 2) if (ev and ebitda and ebitda != 0) else None

        pe_trailing   = get_val(info, "trailingPE")
        pe_forward    = get_val(info, "forwardPE")
        pb_ratio      = get_val(info, "priceToBook")
        roe           = get_val(info, "returnOnEquity")
        roa           = get_val(info, "returnOnAssets")
        div_yield     = get_val(info, "dividendYield", "trailingAnnualDividendYield")
        profit_margin = get_val(info, "profitMargins")
        revenue       = get_val(info, "totalRevenue")
        price         = get_val(info, "regularMarketPrice", "previousClose", "currentPrice")
        currency      = get_val(info, "currency", default="PLN")
        sector        = get_val(info, "sector", default="-")
        employees     = get_val(info, "fullTimeEmployees")

        def pct(val):
            if val is None:
                return None
            return round(float(val) * 100, 2)

        def rnd(val, d=2):
            if val is None:
                return None
            try:
                return round(float(val), d)
            except Exception:
                return None

        record = {
            "ticker":        yahoo_ticker.replace(".WA", ""),
            "name":          name,
            "index":         index_group,
            "sector":        sector,
            "price":         rnd(price, 2),
            "currency":      currency,
            "market_cap_m":  rnd(market_cap / 1e6, 0) if market_cap else None,
            "pe_trailing":   rnd(pe_trailing),
            "pe_forward":    rnd(pe_forward),
            "ev_ebitda":     ev_ebitda,
            "p_bv":          rnd(pb_ratio),
            "roe_pct":       pct(roe),
            "roa_pct":       pct(roa),
            "div_yield_pct": pct(div_yield),
            "net_margin_pct": pct(profit_margin),
            "revenue_m":     rnd(revenue / 1e6, 0) if revenue else None,
            "net_debt_m":    rnd(net_debt / 1e6, 0),
            "employees":     employees,
        }

        # Krótki log
        pe_str  = f"P/E={pe_trailing:.1f}" if pe_trailing else "P/E=N/A"
        div_str = f"DIV={pct(div_yield):.1f}%" if div_yield else "DIV=0%"
        print(f"✅ {pe_str} {div_str}")
        return record

    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")
        return None


# ============================================================================
# GENEROWANIE RAPORTU
# ============================================================================

def generate_report(records: list) -> pd.DataFrame:
    """Buduje DataFrame, sortuje i formatuje."""
    df = pd.DataFrame(records)

    # Sortuj po kapitalizacji malejąco
    df = df.sort_values("market_cap_m", ascending=False, na_position="last")

    # Atrakcyjność fundamentalna: P/E niskie + dywidenda + ROE wysokie
    def fundamental_score(row):
        score = 0
        pe = row.get("pe_trailing")
        div = row.get("div_yield_pct")
        roe = row.get("roe_pct")
        pb  = row.get("p_bv")

        if pe is not None:
            if pe < 10:     score += 30
            elif pe < 15:   score += 20
            elif pe < 25:   score += 10
            elif pe > 40:   score -= 20

        if div is not None:
            if div > 5:     score += 25
            elif div > 3:   score += 15
            elif div > 1:   score += 5

        if roe is not None:
            if roe > 20:    score += 20
            elif roe > 12:  score += 10
            elif roe < 0:   score -= 15

        if pb is not None:
            if pb < 1:      score += 15
            elif pb < 2:    score += 5
            elif pb > 5:    score -= 10

        return score

    df["fundamental_score"] = df.apply(fundamental_score, axis=1)
    df["fundamental_label"] = df["fundamental_score"].apply(lambda s:
        "💎 Niedowartościowana" if s >= 50
        else "✅ Atrakcyjna"    if s >= 25
        else "⏸ Neutralna"     if s >= 0
        else "⚠️ Droga"
    )

    return df


def save_report(df: pd.DataFrame) -> Path:
    FUNDAMENTAL_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = FUNDAMENTAL_DIR / f"gpw_fundamentals_{ts}.csv"
    df.to_csv(out_path, index=False)
    return out_path


def print_summary(df: pd.DataFrame):
    print(f"\n{'='*70}")
    print("  TOP 10 — FUNDAMENTY GPW (wg P/E rosnąco)")
    print(f"{'='*70}")

    display = df[df["pe_trailing"].notna()].sort_values("pe_trailing").head(10)
    cols = ["ticker", "name", "pe_trailing", "div_yield_pct", "roe_pct", "p_bv", "fundamental_label"]
    cols = [c for c in cols if c in display.columns]
    print(display[cols].to_string(index=False))

    print(f"\n{'='*70}")
    print("  TOP 10 DYWIDENDOWE (wg stopy dywidendy malejąco)")
    print(f"{'='*70}")
    div_top = df[df["div_yield_pct"].notna()].sort_values("div_yield_pct", ascending=False).head(10)
    print(div_top[["ticker", "name", "div_yield_pct", "pe_trailing", "roe_pct"]].to_string(index=False))
    print(f"{'='*70}")


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "=" * 70)
    print("  FUNDAMENTS GPW — Swetrowo Market Brief")
    print(f"  Spółek do pobrania: {len(ALL_TICKERS)} (WIG20 + mWIG40)")
    print("=" * 70 + "\n")

    if not HAS_YFINANCE:
        raise SystemExit("Zainstaluj: pip install yfinance")

    records = []

    print("📊 WIG20:")
    for name, ticker in WIG20_TICKERS.items():
        rec = fetch_fundamental(name, ticker, "WIG20")
        if rec:
            records.append(rec)
        time.sleep(REQUEST_DELAY)

    print("\n📊 mWIG40 (top 20):")
    for name, ticker in MWIG40_TICKERS.items():
        rec = fetch_fundamental(name, ticker, "mWIG40")
        if rec:
            records.append(rec)
        time.sleep(REQUEST_DELAY)

    if not records:
        print("❌ Brak danych — sprawdź połączenie internetowe")
        return

    print(f"\n✅ Zebrano dane dla {len(records)}/{len(ALL_TICKERS)} spółek")

    df = generate_report(records)
    print_summary(df)

    out_path = save_report(df)
    print(f"\n💾 Raport zapisany: {out_path}")


if __name__ == "__main__":
    main()
