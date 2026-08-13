
import os
import glob
import pandas as pd
import re
from datetime import datetime

# ============================================================================
# CONFIGURATION
# ============================================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(BASE_DIR, "Reports")
SENTIMENT_DIR = os.path.join(REPORTS_DIR, "Sentiment")
TECHNICAL_DIR = os.path.join(REPORTS_DIR, "Technical")
OUTPUT_DIR = os.path.join(REPORTS_DIR, "Combined")

# Name to Ticker Mapping (from kursy.py)
NAME_TO_TICKER = {
    # WIG20
    'ALIOR': 'ALR',
    'ALLEGRO': 'ALE',
    'BUDIMEX': 'BDX',
    'MDV': 'MDV',
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
    'PGNIG': 'PGN', # Added manually just in case, though might be delisted
    'PZU': 'PZU',
    'EBP': 'EBP',
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

# ============================================================================
# HELPERS
# ============================================================================

def get_latest_file(directory, pattern):
    """Finds the latest file in a directory matching a glob pattern."""
    full_pattern = os.path.join(directory, pattern)
    files = glob.glob(full_pattern)
    if not files:
        return None
    return max(files, key=os.path.getctime)

def parse_sentiment_report(filepath):
    """
    Parses the text-based sentiment report.
    Returns a DataFrame with columns: ['Ticker', 'Sentiment_Score', 'Sentiment_Label', 'Threads']
    """
    print(f"Reading sentiment report: {os.path.basename(filepath)}")
    
    data = []
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
            
        # Regex to match table rows
        # Example: | 31 | JSW | 33 | 5 | 7 | 21 | 15.2% | 21.2% | 😐 -NEGAT |
        # We need Company Name (col 2), Positive % (col 7), Negative % (col 8), Sentiment (col 9)
        
        for line in lines:
            if not line.strip().startswith('|'):
                continue
            if 'Spółka' in line or '---' in line:
                continue
                
            parts = [p.strip() for p in line.split('|')]
            if len(parts) < 10:
                continue
                
            # Parts indices (0-based, empty string at 0 and -1 because of leading/trailing |)
            # 0: ''
            # 1: ID
            # 2: Name
            # 3: Threads
            # 4: Pos Count
            # 5: Neg Count
            # 6: Neut Count
            # 7: Pos %
            # 8: Neg %
            # 9: Sentiment Label
            
            name = parts[2]
            threads = int(parts[3])
            pos_pct_str = parts[7].replace('%', '')
            neg_pct_str = parts[8].replace('%', '')
            sentiment_label = parts[9]
            
            try:
                pos_pct = float(pos_pct_str)
                neg_pct = float(neg_pct_str)
            except ValueError:
                pos_pct = 0.0
                neg_pct = 0.0
                
            # Calculate Sentiment Score (-100 to 100)
            # If no threads, score is 0
            if threads == 0:
                score = 0
            else:
                score = pos_pct - neg_pct
            
            # Map Name to Ticker
            ticker = NAME_TO_TICKER.get(name)
            
            if ticker:
                data.append({
                    'Ticker': ticker,
                    'Sentiment_Score': score,
                    'Sentiment_Label': sentiment_label,
                    'Threads': threads,
                    'Sentiment_Name': name
                })
            else:
                # Optional: Print unmatched names for debugging
                # print(f"Warning: No ticker mapping for '{name}'")
                pass
                
        return pd.DataFrame(data)
        
    except Exception as e:
        print(f"Error parsing sentiment report: {e}")
        return pd.DataFrame()

def load_technical_report(filepath):
    """
    Loads the CSV technical report.
    Returns a DataFrame.
    """
    print(f"Reading technical report: {os.path.basename(filepath)}")
    try:
        df = pd.read_csv(filepath)
        # Rename 'ticker' to 'Ticker' for consistency if needed, but it's likely lowercase in file
        if 'ticker' in df.columns:
            df = df.rename(columns={'ticker': 'Ticker'})
        return df
    except Exception as e:
        print(f"Error loading technical report: {e}")
        return pd.DataFrame()

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("🚀 GENERATING COMBINED STOCK ATTRACTIVENESS REPORT")
    print("=" * 60)
    
    # 1. Load Latest Sentiment Data
    sentiment_file = get_latest_file(SENTIMENT_DIR, "sentiment_report_*.txt")
    if not sentiment_file:
        print("❌ No sentiment report found in", SENTIMENT_DIR)
        return
        
    df_sent = parse_sentiment_report(sentiment_file)
    if df_sent.empty:
        print("❌ Failed to extract data from sentiment report.")
        return
    print(f"✅ Loaded sentiment data for {len(df_sent)} companies.")
        
    # 2. Load Latest Technical Data
    technical_file = get_latest_file(TECHNICAL_DIR, "techincals_report_*.csv")
    if not technical_file:
        print("❌ No technical report found in", TECHNICAL_DIR)
        return
        
    df_tech = load_technical_report(technical_file)
    if df_tech.empty:
        print("❌ Failed to load technical report.")
        return
    print(f"✅ Loaded technical data for {len(df_tech)} companies.")
    
    # 3. Merge Data
    # Inner join to only include companies present in both (and successfully mapped)
    # merged_df = pd.merge(df_tech, df_sent, on='Ticker', how='inner')
    # Using 'left' join on technical data to keep all technicals, filling sentiment with 0/Neutral if missing
    merged_df = pd.merge(df_tech, df_sent, on='Ticker', how='left')
    
    # Fill NaN values for sentiment columns (for companies not in sentiment report or mapping failed)
    merged_df['Sentiment_Score'] = merged_df['Sentiment_Score'].fillna(0)
    merged_df['Threads'] = merged_df['Threads'].fillna(0)
    merged_df['Sentiment_Label'] = merged_df['Sentiment_Label'].fillna('N/A')
    
    # 4. Calculate Final Score
    # Weight: 50% Technical, 50% Sentiment
    # Note: Technical 'combined_score' is likely -100 to 100
    # Sentiment 'Sentiment_Score' is -100 to 100
    
    merged_df['Final_Score'] = (merged_df['combined_score'] + merged_df['Sentiment_Score']) / 2
    
    # 5. Determine Overall Attractiveness Label
    def get_attractiveness_label(score):
        if score >= 50: return "💎 STRONG BUY"
        if score >= 20: return "✅ BUY"
        if score >= -20: return "⏸ HOLD"
        if score >= -50: return "❌ SELL"
        return "💀 STRONG SELL"
        
    merged_df['Attractiveness'] = merged_df['Final_Score'].apply(get_attractiveness_label)
    
    # 6. Select and Reorder Columns
    # Available columns from stock_analysis.py: 
    # ticker, combined_score, sentiment (tech), macd_line, macd_signal, macd_histogram, macd_score, 
    # rsi_value, rsi_score, bb_upper, bb_middle, bb_lower, bb_current, bb_score, mfi_value, mfi_score, last_close, last_date
    
    output_columns = [
        'Ticker', 
        'Final_Score', 
        'Attractiveness',
        'combined_score',  # Technical Score
        'Sentiment_Score', 
        'Sentiment_Label', 
        'Threads',
        'last_close',
        'rsi_value',
        'mfi_value',
        'macd_histogram'
    ]
    
    # Filter only columns that exist (in case technical report format changes)
    output_columns = [c for c in output_columns if c in merged_df.columns]
    
    final_df = merged_df[output_columns].copy()
    
    # Sort by Final Score Descending
    final_df = final_df.sort_values('Final_Score', ascending=False)
    
    # 7. Generate Output
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_file = os.path.join(OUTPUT_DIR, f"combined_{timestamp}.csv")
    
    final_df.to_csv(output_file, index=False)
    
    print("\n🏆 TOP 5 MOST ATTRACTIVE COMPANIES:")
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 1000)
    print(final_df[['Ticker', 'Final_Score', 'Attractiveness', 'combined_score', 'Sentiment_Score']].head(5).to_string(index=False))
    
    print("\n💩 TOP 5 LEAST ATTRACTIVE COMPANIES:")
    print(final_df[['Ticker', 'Final_Score', 'Attractiveness', 'combined_score', 'Sentiment_Score']].tail(5).to_string(index=False))
    
    print(f"\n💾 Report saved to: {output_file}")

if __name__ == "__main__":
    main()
