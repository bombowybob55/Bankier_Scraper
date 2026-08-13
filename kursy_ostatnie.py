
import yfinance as yf
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import time
import os

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, 'historical_data.db')
DEFAULT_DAYS_BACK = 30  # For tickers with no data in DB
YAHOO_SUFFIX = '.WA'  # Warsaw Stock Exchange suffix on Yahoo Finance

# Ticker Mapping (Name -> GPW ticker). Used as the Yahoo Finance symbol
# (with YAHOO_SUFFIX appended) and as the ticker stored in the database.
# Note: stooq.pl (the previous source) started blocking scripted requests
# behind a JS proof-of-work challenge, so this now uses yfinance like
# kursy_zagr_upd.py and fundaments_gpw.py do.
TICKERS = {
    # WIG20
    'ALIOR': 'ALR',
    'ALLEGRO': 'ALE',
    'BUDIMEX': 'BDX',
    'MODIVO': 'MDV',
    'CD PROJEKT': 'CDR',
    'DINO': 'DNP',
    'GRUPA KĘTY': 'KTY',
    'KGHM': 'KGH',
    'KRUK': 'KRU',
    'LPP': 'LPP',
    'MBANK': 'MBK',
    'ORANGE POLSKA': 'OPL',
    'PEKAO': 'PEO',
    'PEPCO': 'PCO',
    'PGE': 'PGE',
    'PKN ORLEN': 'PKN',
    'PKO BP': 'PKO',
    'PZU': 'PZU',
    'ERSTE BANK POLSKA': 'EBP',
    'TAURON': 'TPE',
    'ENEA': 'ENA',
    'ŻABKA': 'ZAB',

    # mWIG40
    '11 BIT STUDIOS': '11B',
    'ABPL': 'ABE',
    'AMREST': 'EAT',
    'ASBIS': 'ASB',
    'ASSECO POLAND': 'ACP',
    'ASSECOSEE': 'ASE',
    'AUTOPARTN': 'APR',
    'BENEFIT': 'BFT',
    'BNP PARIBAS BANK POLSKA': 'BNP',
    'CYBERFLKS': 'CBF',
    'CYFROWY POLSAT': 'CPS',
    'DEVELIA': 'DVL',
    'DIAG': 'DIA',
    'DOMDEV': 'DOM',
    'EUROCASH': 'EUR',
    'GPW': 'GPW',
    'GREENX': 'GRX',
    'GRUPAAZOTY': 'ATT',
    'GRUPRACUJ': 'GPP',
    'HANDLOWY': 'BHW',
    'HUUUGE': 'HUG',
    'ING BANK ŚLĄSKI': 'ING',
    'INTERCARS': 'CAR',
    'JSW': 'JSW',
    'LUBAWA': 'LBW',
    'MILLENNIUM': 'MIL',
    'MIRBUD': 'MRB',
    'MOBRUK': 'MBR',
    'NEUCA': 'NEU',
    'NEWAG': 'NWG',
    'PEP': 'PEP',
    'PLAYWAY': 'PLW',
    'RAINBOW': 'RBW',
    'SYNEKTIK': 'SNT',
    'TEXT': 'TXT',
    'TSGAMES': 'TEN',
    'VERCOM': 'VRC',
    'VISTULA GROUP': 'VRG',
    'VOXEL': 'VOX',
    'WIRTUALNA': 'WPL',
    'XTB': 'XTB'
}

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS prices (
            ticker TEXT,
            date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            PRIMARY KEY (ticker, date)
        )
    ''')
    conn.commit()
    conn.close()

def get_latest_date_for_ticker(ticker):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT MAX(date) FROM prices WHERE ticker = ?", (ticker,))
    res = c.fetchone()
    conn.close()
    if res and res[0]:
        return res[0]
    return None

def save_to_db(ticker, df):
    """Save dataframe to database"""
    conn = sqlite3.connect(DB_NAME)

    # yfinance returns columns: Open, High, Low, Close, Volume
    df = df.reset_index()  # Date becomes a column
    df = df.rename(columns={
        'Date': 'date',
        'Open': 'open',
        'High': 'high',
        'Low': 'low',
        'Close': 'close',
        'Volume': 'volume'
    })

    # Convert date to string format and keep only YYYY-MM-DD
    df['date'] = df['date'].astype(str).str[:10]

    # Add ticker column (short GPW code, not the Yahoo .WA symbol)
    df['ticker'] = ticker

    # Ensure correct types
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0).astype(int)

    records = 0
    for _, row in df.iterrows():
        try:
            conn.execute('''
                INSERT OR REPLACE INTO prices (ticker, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (row['ticker'], row['date'], row['open'], row['high'], row['low'], row['close'], row['volume']))
            records += 1
        except Exception as e:
            print(f"[{ticker}] Error inserting row for {row['date']}: {e}")

    conn.commit()
    conn.close()
    return records

def fetch_data(ticker, start_date, end_date):
    """
    Fetch data using yfinance for the specified date range.
    start_date and end_date should be YYYY-MM-DD strings.
    """
    yahoo_symbol = ticker + YAHOO_SUFFIX
    print(f"Fetching {ticker} ({yahoo_symbol}) from {start_date} to {end_date}...")
    try:
        stock = yf.Ticker(yahoo_symbol)
        df = stock.history(start=start_date, end=end_date, interval='1d')

        if df.empty:
            print(f"No data for {ticker}")
            return None

        return df

    except Exception as e:
        print(f"Error fetching {ticker}: {e}")
        return None

def main():
    init_db()

    unique_tickers = sorted(list(set(TICKERS.values())))
    print(f"Checking updates for {len(unique_tickers)} tickers...")

    end_date = datetime.now().strftime('%Y-%m-%d')

    success_count = 0
    up_to_date_count = 0
    fail_count = 0

    for ticker in unique_tickers:
        latest_date_str = get_latest_date_for_ticker(ticker)

        if latest_date_str:
            # latest_date_str might be YYYY-MM-DD or YYYY-MM-DD HH:MM:SS+ZZ:ZZ
            # Start from the day after the latest date
            latest_date = datetime.strptime(latest_date_str[:10], '%Y-%m-%d')
            start_date = (latest_date + timedelta(days=1)).strftime('%Y-%m-%d')

            # Check if we're already up to date
            if start_date >= end_date:
                print(f"{ticker} is already up to date (latest: {latest_date_str})")
                up_to_date_count += 1
                continue
        else:
            # Fallback for new tickers
            start_date = (datetime.now() - timedelta(days=DEFAULT_DAYS_BACK)).strftime('%Y-%m-%d')
            print(f"No existing data for {ticker}, starting from {start_date}")

        df = fetch_data(ticker, start_date, end_date)

        if df is not None and not df.empty:
            count = save_to_db(ticker, df)
            if count > 0:
                print(f"Saved/Updated {count} records for {ticker}")
                success_count += 1
            else:
                print(f"{ticker} is already up to date.")
                up_to_date_count += 1
        elif df is not None and df.empty:
            print(f"{ticker} returned empty data.")
            up_to_date_count += 1
        else:
            print(f"Failed to fetch data for {ticker}")
            fail_count += 1

        # Polite delay to avoid rate limiting
        time.sleep(0.5)

    print(f"\nDone.")
    print(f"Success (New/Updated): {success_count}")
    print(f"Up to date: {up_to_date_count}")
    print(f"Failed: {fail_count}")

if __name__ == "__main__":
    main()
