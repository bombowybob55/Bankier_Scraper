#!/usr/bin/env python3
import asyncio
import csv
import html
import json
import random
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

try:
    import httpx
    from selectolax.parser import HTMLParser
    HAS_FORUM_DEPS = True
except Exception:
    httpx = None
    HTMLParser = None
    HAS_FORUM_DEPS = False

try:
    from fake_useragent import UserAgent
except Exception:  # pragma: no cover - fallback for environments without data
    UserAgent = None

BASE_DIR = Path(__file__).resolve().parent
REPORTS_DIR = BASE_DIR / "Reports"

SENTIMENT_DIR = REPORTS_DIR / "Sentiment"
TECH_DIR = REPORTS_DIR / "Technical"
COMBINED_DIR = REPORTS_DIR / "Combined"
MACRO_DIR = REPORTS_DIR / "Macro"
ALERTS_DIR = REPORTS_DIR / "Alerts"
FUNDAMENTAL_DIR = REPORTS_DIR / "Fundamental"

DATE_RE = re.compile(r"(\d{8})_(\d{6})")

BANKIER_BASE_URL = "https://www.bankier.pl"
FORUM_URL = "https://www.bankier.pl/forum/forum_gielda,6,726.html"
FORUM_HEADLINES_LIMIT = 8
FORUM_SOURCE_LABEL = "Forum Giełda"

MAX_RETRIES = 3
REQUEST_TIMEOUT = 10.0


def parse_dt_from_name(name: str):
    match = DATE_RE.search(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def find_latest_report(dir_path: Path, pattern: str):
    files = list(dir_path.glob(pattern))
    if not files:
        return None, None, ""

    dated = []
    for f in files:
        dt = parse_dt_from_name(f.name)
        if dt:
            dated.append((dt, f))

    if dated:
        dt, f = max(dated, key=lambda x: x[0])
        return f, dt, "filename"

    # Fallback to mtime if no timestamp in filename
    f = max(files, key=lambda p: p.stat().st_mtime)
    dt = datetime.fromtimestamp(f.stat().st_mtime)
    return f, dt, "mtime"


def parse_timeline_report(path: Path):
    data = {
        "date": None,
        "bullish": 0,
        "bearish": 0,
        "neutral": 0,
    }

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return None

    for line in lines:
        line = line.strip()
        if line.startswith("Data raportu:"):
            date_str = line.split("Data raportu:", 1)[1].strip()
            try:
                data["date"] = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        elif line.startswith("Data:"):
            date_str = line.split("Data:", 1)[1].strip()
            try:
                data["date"] = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass

        if "Spółek z sentmentem BULLISH/POZYTYWNYM:" in line:
            try:
                data["bullish"] = int(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "Spółek z sentmentem BEARISH/NEGATYWNYM:" in line:
            try:
                data["bearish"] = int(line.split(":")[-1].strip())
            except ValueError:
                pass
        elif "Spółek z sentmentem NEUTRALNYM:" in line:
            try:
                data["neutral"] = int(line.split(":")[-1].strip())
            except ValueError:
                pass

        if line.startswith("BULLISH:") and "BEARISH:" in line and "NEUTRAL:" in line:
            parts = line.split("|")
            for part in parts:
                part = part.strip()
                if part.startswith("BULLISH:"):
                    data["bullish"] = int(part.split(":")[1].strip())
                elif part.startswith("BEARISH:"):
                    data["bearish"] = int(part.split(":")[1].strip())
                elif part.startswith("NEUTRAL:"):
                    data["neutral"] = int(part.split(":")[1].strip())

    if data["date"] is None:
        fallback_dt = parse_dt_from_name(path.name)
        if fallback_dt:
            data["date"] = fallback_dt
        else:
            return None

    return data


def load_sentiment_timeline(reports_dir: Path, limit: int = 20):
    reports = list(reports_dir.glob("sentiment_report_*.txt"))
    if not reports:
        return []

    results = []
    for report in reports:
        parsed = parse_timeline_report(report)
        if parsed:
            results.append(parsed)

    results.sort(key=lambda x: x["date"])
    if limit and len(results) > limit:
        results = results[-limit:]

    timeline = []
    for row in results:
        timeline.append({
            "date": row["date"].strftime("%Y-%m-%d %H:%M"),
            "bullish": row["bullish"],
            "bearish": row["bearish"],
            "neutral": row["neutral"],
        })

    return timeline


def parse_combined_report(path: Path, limit: int = 12):
    if not path or not path.exists():
        return [], []

    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        return [], []

    desired_headers = [
        "Ticker",
        "Final_Score",
        "Attractiveness",
        "combined_score",
        "Sentiment_Score",
        "Threads",
        "last_close",
    ]
    headers = [h for h in desired_headers if h in rows[0].keys()]

    formatted_rows = []
    for row in rows[:limit]:
        out = {}
        for h in headers:
            val = row.get(h, "")
            if h in {"Final_Score", "combined_score", "Sentiment_Score", "last_close"}:
                try:
                    val = fmt_num(float(val), 2)
                except Exception:
                    pass
            if h == "Threads":
                try:
                    val = str(int(float(val)))
                except Exception:
                    pass
            out[h] = val
        formatted_rows.append(out)

    return headers, formatted_rows


def _build_user_agent():
    if UserAgent is None:
        return "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    try:
        ua = UserAgent()
        return ua.random
    except Exception:
        return "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def get_random_headers():
    return {
        "User-Agent": _build_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
        "Referer": BANKIER_BASE_URL + "/",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


async def fetch_page(client, url: str):
    for attempt in range(MAX_RETRIES):
        try:
            headers = get_random_headers()
            response = await client.get(url, headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True)
            if response.status_code == 200:
                return response.text
            if response.status_code == 503:
                await asyncio.sleep((2 ** attempt) + random.random())
                continue
            return None
        except Exception:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(1 + random.random())
            else:
                return None
    return None


def parse_thread_date(date_str):
    if not date_str:
        return None
    date_str = date_str.strip()
    now = datetime.now()

    if "dziś" in date_str.lower() or "dzisiaj" in date_str.lower():
        time_match = re.search(r"(\d{1,2}):(\d{2})", date_str)
        if time_match:
            return now.replace(
                hour=int(time_match.group(1)),
                minute=int(time_match.group(2)),
                second=0,
                microsecond=0,
            )
        return now

    if "wczoraj" in date_str.lower():
        time_match = re.search(r"(\d{1,2}):(\d{2})", date_str)
        yesterday = now - timedelta(days=1)
        if time_match:
            return yesterday.replace(
                hour=int(time_match.group(1)),
                minute=int(time_match.group(2)),
                second=0,
                microsecond=0,
            )
        return yesterday

    if "-" in date_str and ":" in date_str:
        match = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})", date_str)
        if match:
            return datetime.strptime(
                f"{match.group(1)} {match.group(2)}:{match.group(3)}", "%Y-%m-%d %H:%M"
            )

    return None


def format_forum_date(date_str: str) -> str:
    dt = parse_thread_date(date_str)
    if dt:
        return dt.strftime("%Y-%m-%d %H:%M")
    return date_str.strip() if date_str else ""


async def fetch_forum_headlines_async(url: str, limit: int = FORUM_HEADLINES_LIMIT):
    if not HAS_FORUM_DEPS or httpx is None or HTMLParser is None:
        return []

    async with httpx.AsyncClient() as client:
        html_doc = await fetch_page(client, url)

    if not html_doc:
        return []

    tree = HTMLParser(html_doc)
    rows = tree.css("table.threadsList tbody tr")
    if not rows:
        rows = tree.css("table#threadsList tbody tr")

    results = []
    seen = set()
    for row in rows:
        title_node = row.css_first("td.threadTitle a")
        date_node = row.css_first("td.createDate")
        if not title_node:
            continue
        title = title_node.text(strip=True)
        href = title_node.attributes.get("href")
        if not title or not href:
            continue
        if title.lower() in seen:
            continue
        seen.add(title.lower())
        date_raw = date_node.text(strip=True) if date_node else ""
        results.append({
            "title": title,
            "url": urljoin(BANKIER_BASE_URL, href),
            "date_raw": date_raw,
            "date_display": format_forum_date(date_raw),
        })
        if len(results) >= limit:
            break

    return results


def parse_sentiment_report(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    meta = {
        "generated_at": None,
        "period": None,
        "duration": None,
    }

    for line in lines:
        if line.startswith("Data:"):
            meta["generated_at"] = line.split("Data:", 1)[1].strip()
        elif line.startswith("Okres:"):
            meta["period"] = line.split("Okres:", 1)[1].strip()
        elif line.startswith("Czas wykonania:"):
            meta["duration"] = line.split("Czas wykonania:", 1)[1].strip()

    rows = []
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        if "+----" in line:
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if not parts:
            continue
        # Header row
        if any(p.lower() == "spółka" for p in parts):
            continue
        if not parts[0].strip().isdigit():
            continue
        if len(parts) < 9:
            continue
        sentiment_raw = parts[8]
        sentiment_clean = re.sub(r"[^A-Za-z ]+", "", sentiment_raw).strip().upper()
        if "BULL" in sentiment_clean:
            sentiment = "Bullish"
        elif "BEAR" in sentiment_clean:
            sentiment = "Bearish"
        else:
            sentiment = "Neutral"

        def to_int(val):
            try:
                return int(val)
            except ValueError:
                return 0

        def to_float(val):
            try:
                return float(val.replace("%", ""))
            except ValueError:
                return 0.0

        rows.append({
            "company": parts[1],
            "threads": to_int(parts[2]),
            "positive": to_int(parts[3]),
            "negative": to_int(parts[4]),
            "neutral": to_int(parts[5]),
            "positive_pct": to_float(parts[6]),
            "negative_pct": to_float(parts[7]),
            "sentiment": sentiment,
        })

    counts = Counter(r["sentiment"] for r in rows)
    summary = {
        "Bullish": counts.get("Bullish", 0),
        "Bearish": counts.get("Bearish", 0),
        "Neutral": counts.get("Neutral", 0),
    }

    top_positive = sorted(rows, key=lambda r: (r["positive_pct"], r["threads"]), reverse=True)[:10]
    top_negative = sorted(rows, key=lambda r: (r["negative_pct"], r["threads"]), reverse=True)[:10]

    return meta, rows, summary, top_positive, top_negative


def parse_technical_csv(path: Path):
    rows = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    def to_float(val):
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    for row in rows:
        row["combined_score"] = to_float(row.get("combined_score"))
        row["rsi_value"] = to_float(row.get("rsi_value"))
        row["mfi_value"] = to_float(row.get("mfi_value"))
        row["last_close"] = to_float(row.get("last_close"))

    counts = Counter(row.get("sentiment", "Unknown") for row in rows)

    top_up = sorted(rows, key=lambda r: r["combined_score"], reverse=True)[:10]
    top_down = sorted(rows, key=lambda r: r["combined_score"])[:10]

    latest_date = None
    for row in rows:
        last_date = row.get("last_date")
        if last_date:
            try:
                dt = datetime.strptime(last_date, "%Y-%m-%d")
            except ValueError:
                continue
            if not latest_date or dt > latest_date:
                latest_date = dt

    return rows, counts, top_up, top_down, latest_date


def esc(value):
    return html.escape(str(value))


def fmt_num(val, digits=2):
    try:
        return f"{val:.{digits}f}"
    except Exception:
        return str(val)


# ---------------------------------------------------------------------------
# MACRO SNAPSHOT
# ---------------------------------------------------------------------------

def load_macro_snapshot() -> dict:
    """Wczytuje najnowszy plik macro_snapshot_*.json."""
    files = list(MACRO_DIR.glob("macro_snapshot_*.json"))
    if not files:
        return {}
    latest = max(files, key=lambda p: p.stat().st_mtime)
    try:
        with latest.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _macro_item_html(label: str, data: dict) -> str:
    """Renderuje jeden kafelek Macro Pulse."""
    price = data.get("price")
    chg   = data.get("change_pct")
    if price is None:
        return ""
    chg_val  = chg if chg is not None else 0.0
    arrow    = "&#9650;" if chg_val >= 0 else "&#9660;"
    color    = "var(--green)" if chg_val >= 0 else "var(--red)"
    chg_abs  = abs(chg_val)
    # format price: FX 4 digits, indices integers, commodities 2
    if price >= 1000:
        price_str = f"{price:,.0f}"
    elif price >= 10:
        price_str = f"{price:.2f}"
    else:
        price_str = f"{price:.4f}"

    return (
        f'<div class="macro-item">'
        f'<span class="macro-label">{esc(label)}</span>'
        f'<span class="macro-price">{esc(price_str)}</span>'
        f'<span class="macro-chg" style="color:{color}">{arrow} {chg_abs:.2f}%</span>'
        f'</div>'
    )


def build_macro_pulse_html(snapshot: dict) -> str:
    """Buduje HTML sekcji Macro Pulse."""
    if not snapshot:
        return ""
    generated = snapshot.get("generated_at", "")
    nbp_rate = snapshot.get("nbp", {}).get("reference_rate", {}).get("rate")
    nbp_str  = f"NBP {nbp_rate}%" if nbp_rate else ""

    LABELS = {
        # Indeksy
        "WIG20": "WIG20", "DAX": "DAX", "SP500": "S&P500",
        "NASDAQ": "NASDAQ", "DJIA": "DJIA",
        # FX
        "EURPLN": "EUR/PLN", "USDPLN": "USD/PLN", "CHFPLN": "CHF/PLN",
        # Surowce
        "GOLD": "Złoto", "OIL_WTI": "Ropa WTI", "COPPER": "Miedź",
        # Obligacje
        "US10Y": "US 10Y",
    }

    items = []
    for key, label in LABELS.items():
        for cat in ("indices", "fx", "commodities", "bonds"):
            data = snapshot.get(cat, {}).get(key)
            if data:
                items.append(_macro_item_html(label, data))
                break

    if not items:
        return ""

    items_html = "".join(items)
    return f"""
  <div class="macro-pulse reveal" id="macro">
    <div class="macro-header">
      <span class="macro-title">Macro Pulse</span>
      <span class="macro-ts">Aktualizacja: {esc(generated)}{' · ' + nbp_str if nbp_str else ''}</span>
    </div>
    <div class="macro-track">{items_html}</div>
  </div>"""


# ---------------------------------------------------------------------------
# ALERTS
# ---------------------------------------------------------------------------

def load_alerts_data() -> list:
    """Wczytuje najnowszy plik alerts_*.json."""
    files = list(ALERTS_DIR.glob("alerts_*.json"))
    if not files:
        return []
    latest = max(files, key=lambda p: p.stat().st_mtime)
    try:
        with latest.open("r", encoding="utf-8") as f:
            payload = json.load(f)
            return payload.get("alerts", [])
    except Exception:
        return []


SIGNAL_CLASSES = {"buy": "alert-buy", "sell": "alert-sell", "watch": "alert-watch"}
SIGNAL_LABELS  = {"buy": "KUP", "sell": "SPRZEDAJ", "watch": "OBSERWUJ"}
SEVERITY_ICON  = {"high": "&#128293;", "medium": "&#9888;", "low": "&#8505;"}


# ---------------------------------------------------------------------------
# FUNDAMENTY GPW
# ---------------------------------------------------------------------------

def load_fundamentals_gpw() -> list:
    """Wczytuje najnowszy plik gpw_fundamentals_*.csv."""
    files = list(FUNDAMENTAL_DIR.glob("gpw_fundamentals_*.csv"))
    if not files:
        return []
    latest = max(files, key=lambda p: p.stat().st_mtime)
    try:
        import csv as _csv
        rows = []
        with latest.open("r", encoding="utf-8", errors="replace", newline="") as f:
            reader = _csv.DictReader(f)
            for row in reader:
                rows.append(row)
        return rows
    except Exception:
        return []


def _fmt_fundamental(val, suffix="", digits=1, scale=1.0, fallback="-"):
    """Formatuje wartość fundamentalna do wyświetlenia."""
    if val is None or str(val).strip() in ("", "None", "nan"):
        return fallback
    try:
        f = float(val) * scale
        return f"{f:.{digits}f}{suffix}"
    except Exception:
        return fallback


def build_fundamentals_html(rows: list) -> str:
    """Buduje HTML sekcji Fundamenty GPW."""
    if not rows:
        return '<div class="chart-empty">Brak danych. Uruchom fundaments_gpw.py aby pobrać fundamenty.</div>'

    # Posortuj wg fundamental_score malejąco
    def score(r):
        try: return float(r.get("fundamental_score", 0))
        except: return 0
    rows_sorted = sorted(rows, key=score, reverse=True)

    # Top 10 wg najniższego P/E (z posiadających P/E)
    rows_with_pe = [r for r in rows if _fmt_fundamental(r.get("pe_trailing")) != "-"]
    top_pe = sorted(rows_with_pe, key=lambda r: float(r.get("pe_trailing", 999)))[:10]

    # Top 10 dywidendowe
    rows_with_div = [r for r in rows if _fmt_fundamental(r.get("div_yield_pct")) != "-" and float(r.get("div_yield_pct", 0)) > 0]
    top_div = sorted(rows_with_div, key=lambda r: float(r.get("div_yield_pct", 0)), reverse=True)[:10]

    def label_class(label: str) -> str:
        l = label.lower()
        if "nied" in l: return "bullish"
        if "atrak" in l: return "bullish"
        if "neutraln" in l: return "neutral"
        return "bearish"

    def pe_row(r):
        pe  = _fmt_fundamental(r.get("pe_trailing"), digits=1)
        pe_f = _fmt_fundamental(r.get("pe_forward"), digits=1)
        roe  = _fmt_fundamental(r.get("roe_pct"), suffix="%", digits=1)
        div  = _fmt_fundamental(r.get("div_yield_pct"), suffix="%", digits=2, scale=0.01)
        pb   = _fmt_fundamental(r.get("p_bv"), digits=2)
        lbl  = r.get("fundamental_label", "")
        cls  = label_class(lbl)
        return (
            f'<tr>'
            f'<td><strong>{esc(r.get("ticker",""))}</strong></td>'
            f'<td>{esc(r.get("name",""))}</td>'
            f'<td>{esc(r.get("index",""))}</td>'
            f'<td>{pe}</td><td>{pe_f}</td>'
            f'<td>{div}</td><td>{roe}</td><td>{pb}</td>'
            f'<td><span class="pill {cls}">{esc(lbl)}</span></td>'
            f'</tr>'
        )

    def div_row(r):
        pe   = _fmt_fundamental(r.get("pe_trailing"), digits=1)
        div  = _fmt_fundamental(r.get("div_yield_pct"), suffix="%", digits=2, scale=0.01)
        roe  = _fmt_fundamental(r.get("roe_pct"), suffix="%", digits=1)
        mc   = _fmt_fundamental(r.get("market_cap_m"), suffix=" mln PLN", digits=0)
        return (
            f'<tr>'
            f'<td><strong>{esc(r.get("ticker",""))}</strong></td>'
            f'<td>{esc(r.get("name",""))}</td>'
            f'<td>{div}</td>'
            f'<td>{pe}</td><td>{roe}</td><td>{mc}</td>'
            f'</tr>'
        )

    pe_thead = '<tr><th>Ticker</th><th>Spółka</th><th>Indeks</th><th>P/E</th><th>P/E fwd</th><th>Dywidenda</th><th>ROE</th><th>P/BV</th><th>Ocena</th></tr>'
    div_thead = '<tr><th>Ticker</th><th>Spółka</th><th>Dywidenda %</th><th>P/E</th><th>ROE</th><th>Kapitalizacja</th></tr>'

    pe_rows_html  = "".join(pe_row(r) for r in top_pe)
    div_rows_html = "".join(div_row(r) for r in top_div)

    # Podsumowanie stat
    total = len(rows)
    cheap = sum(1 for r in rows if _fmt_fundamental(r.get("pe_trailing")) != "-" and float(r.get("pe_trailing", 99)) < 15)
    paying_div = len(rows_with_div)
    avg_roe_vals = [float(r.get("roe_pct", 0)) for r in rows if _fmt_fundamental(r.get("roe_pct")) != "-"]
    avg_roe = round(sum(avg_roe_vals) / len(avg_roe_vals), 1) if avg_roe_vals else 0

    return f"""  <div class=\"stat-grid\">
    <div class=\"stat-card\"><div class=\"label\">Spółek</div><div class=\"value\">{total}</div></div>
    <div class=\"stat-card\"><div class=\"label\">P/E < 15 (tanie)</div><div class=\"value\">{cheap}</div></div>
    <div class=\"stat-card\"><div class=\"label\">Wypłaca dywidendę</div><div class=\"value\">{paying_div}</div></div>
    <div class=\"stat-card\"><div class=\"label\">Avg ROE</div><div class=\"value\">{avg_roe}%</div></div>
  </div>
  <div class=\"grid-2\" style=\"margin-top:18px;\">
    <div>
      <h3>Top 10 wg P/E (najtańsze)</h3>
      <div class=\"table-wrap\">
        <table><thead>{pe_thead}</thead><tbody>{pe_rows_html}</tbody></table>
      </div>
    </div>
    <div>
      <h3>Top 10 Dywidendowe</h3>
      <div class=\"table-wrap\">
        <table><thead>{div_thead}</thead><tbody>{div_rows_html}</tbody></table>
      </div>
    </div>
  </div>"""


def build_alerts_html(alerts: list) -> str:
    """Buduje HTML sekcji Alertów Technicznych."""
    if not alerts:
        return '<div class="chart-empty">Brak alertów. Uruchom alerts.py lub brak danych historycznych.</div>'

    rows = []
    for a in alerts[:40]:  # max 40 na stronie
        sig   = a.get("signal", "watch")
        sev   = a.get("severity", "low")
        cls   = SIGNAL_CLASSES.get(sig, "alert-watch")
        icon  = SEVERITY_ICON.get(sev, "")
        badge = SIGNAL_LABELS.get(sig, sig.upper())
        rsi   = a.get("rsi")
        rsi_s = f"{rsi:.1f}" if rsi is not None else "-"
        price = a.get("price")
        price_s = f"{price:.2f}" if price is not None else "-"
        mkt   = a.get("market", "")
        rows.append(
            f'<tr class="{cls}">'
            f'<td>{icon} {esc(a.get("ticker",""))}</td>'
            f'<td><span class="mkt-badge">{esc(mkt)}</span></td>'
            f'<td>{esc(a.get("label",""))}</td>'
            f'<td><span class="pill {sig}">{badge}</span></td>'
            f'<td>{rsi_s}</td>'
            f'<td>{price_s}</td>'
            f'<td>{esc(a.get("date",""))}</td>'
            f'</tr>'
        )

    body = "".join(rows)
    return f"""<div class="table-wrap">
  <table class="sortable-table" id="alertsTable">
    <thead><tr>
      <th data-col="0">Ticker</th>
      <th data-col="1">Rynek</th>
      <th data-col="2">Sygnał</th>
      <th data-col="3">Rekomendacja</th>
      <th data-col="4">RSI</th>
      <th data-col="5">Cena</th>
      <th data-col="6">Data</th>
    </tr></thead>
    <tbody>{body}</tbody>
  </table>
</div>"""


def render_table(headers, rows):
    thead = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body_rows = []
    for row in rows:
        tds = "".join(f"<td>{esc(row.get(h, ''))}</td>" for h in headers)
        body_rows.append(f"<tr>{tds}</tr>")
    return (
        "<div class=\"table-wrap\">"
        "<table>"
        f"<thead><tr>{thead}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table>"
        "</div>"
    )


def render_table_custom(headers, rows, wrap_class="table-wrap", table_class=""):
    thead = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body_rows = []
    for row in rows:
        tds = "".join(f"<td>{esc(row.get(h, ''))}</td>" for h in headers)
        body_rows.append(f"<tr>{tds}</tr>")
    class_attr = f" class=\"{table_class}\"" if table_class else ""
    return (
        f"<div class=\"{wrap_class}\">"
        f"<table{class_attr}>"
        f"<thead><tr>{thead}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table>"
        "</div>"
    )


def build_html(
    forum_headlines,
    forum_updated,
    forum_source,
    sentiment_timeline,
    combined_headers,
    combined_rows,
    combined_report_path,
    sentiment_meta,
    sentiment_rows,
    sentiment_summary,
    top_positive,
    top_negative,
    tech_pl_rows,
    tech_pl_counts,
    tech_pl_top_up,
    tech_pl_top_down,
    tech_pl_latest,
    tech_zagr_rows,
    tech_zagr_counts,
    tech_zagr_top_up,
    tech_zagr_top_down,
    tech_zagr_latest,
    sentiment_report_path,
    tech_pl_report_path,
    tech_zagr_report_path,
    sentiment_report_dt,
    tech_pl_report_dt,
    tech_zagr_report_dt,
    macro_snapshot=None,
    alerts_data=None,
    fundamentals_data=None,
):
    def fmt_dt(dt):
        return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "-"

    sentiment_updated = sentiment_meta.get("generated_at") or fmt_dt(sentiment_report_dt)
    tech_pl_updated = fmt_dt(tech_pl_report_dt)
    tech_zagr_updated = fmt_dt(tech_zagr_report_dt)
    forum_updated_display = forum_updated or "-"

    # Macro Pulse + Alerty
    macro_snapshot = macro_snapshot or {}
    alerts_data    = alerts_data    or []
    macro_pulse_html = build_macro_pulse_html(macro_snapshot)
    alerts_count     = len(alerts_data)
    alerts_high      = sum(1 for a in alerts_data if a.get("severity") == "high")
    alerts_html_body = build_alerts_html(alerts_data)
    fundamentals_data  = fundamentals_data or []
    fundamentals_html  = build_fundamentals_html(fundamentals_data)
    num_fundamentals   = len(fundamentals_data)

    hero_tickers = [
        t.get("ticker") for t in (tech_pl_top_up[:5] + tech_zagr_top_up[:5]) if t.get("ticker")
    ]
    if not hero_tickers:
        hero_tickers = ["WIG20", "DAX", "S&P 500", "NASDAQ", "EURUSD"]

    def format_top_tech(rows):
        formatted = []
        for row in rows:
            formatted.append({
                "ticker": row.get("ticker", ""),
                "combined_score": fmt_num(row.get("combined_score", 0), 1),
                "sentiment": row.get("sentiment", ""),
                "last_close": fmt_num(row.get("last_close", 0), 2),
                "last_date": row.get("last_date", ""),
                "rsi_value": fmt_num(row.get("rsi_value", 0), 2),
                "mfi_value": fmt_num(row.get("mfi_value", 0), 2),
            })
        return formatted

    tech_headers = [
        "ticker",
        "combined_score",
        "sentiment",
        "last_close",
        "last_date",
        "rsi_value",
        "mfi_value",
    ]

    sentiment_table_headers = [
        "company",
        "threads",
        "positive",
        "negative",
        "neutral",
        "positive_pct",
        "negative_pct",
        "sentiment",
    ]

    sentiment_display_rows = []
    for row in sentiment_rows:
        sentiment_display_rows.append({
            "company": row["company"],
            "threads": row["threads"],
            "positive": row["positive"],
            "negative": row["negative"],
            "neutral": row["neutral"],
            "positive_pct": fmt_num(row["positive_pct"], 1) + "%",
            "negative_pct": fmt_num(row["negative_pct"], 1) + "%",
            "sentiment": row["sentiment"],
        })

    top_positive_rows = [
        {
            "company": row["company"],
            "positive_pct": fmt_num(row["positive_pct"], 1) + "%",
            "threads": row["threads"],
            "sentiment": row["sentiment"],
        }
        for row in top_positive
    ]
    top_negative_rows = [
        {
            "company": row["company"],
            "negative_pct": fmt_num(row["negative_pct"], 1) + "%",
            "threads": row["threads"],
            "sentiment": row["sentiment"],
        }
        for row in top_negative
    ]

    top_sent_headers_pos = ["company", "positive_pct", "threads", "sentiment"]
    top_sent_headers_neg = ["company", "negative_pct", "threads", "sentiment"]

    tech_pl_top_rows = format_top_tech(tech_pl_top_up)
    tech_pl_bottom_rows = format_top_tech(tech_pl_top_down)
    tech_zagr_top_rows = format_top_tech(tech_zagr_top_up)
    tech_zagr_bottom_rows = format_top_tech(tech_zagr_top_down)

    tech_pl_full_rows = format_top_tech(tech_pl_rows)
    tech_zagr_full_rows = format_top_tech(tech_zagr_rows)

    ticker_tape = " ".join([f"<span>{esc(t)}</span>" for t in hero_tickers])
    timeline_json = json.dumps(sentiment_timeline)
    alerts_json   = json.dumps([
        {"ticker": a.get("ticker", ""), "signal": a.get("signal", ""),
         "severity": a.get("severity", ""), "type": a.get("type", "")}
        for a in alerts_data
    ])

    combined_table = ""
    if combined_headers and combined_rows:
        combined_table = render_table_custom(
            combined_headers,
            combined_rows,
            wrap_class="table-wrap compact",
            table_class="compact-table",
        )
    else:
        combined_table = "<div class=\"chart-empty\">Brak danych combined_analysis.py. Uruchom skrypt i wygeneruj raport.</div>"

    forum_items = ""
    if forum_headlines:
        for item in forum_headlines:
            date_display = item.get("date_display", "")
            date_html = f"<span class=\"headline-date\">{esc(date_display)}</span>" if date_display else ""
            forum_items += (
                "<li>"
                f"{date_html}"
                f"<a href=\"{esc(item.get('url', '#'))}\" target=\"_blank\" rel=\"noopener noreferrer\">"
                f"{esc(item.get('title', ''))}</a>"
                "</li>"
            )
    else:
        if not HAS_FORUM_DEPS:
            forum_items = "<li>Brak danych z forum: zainstaluj httpx, selectolax, fake-useragent.</li>"
        else:
            forum_items = "<li>Brak danych z forum. Odśwież raport z dostępem do internetu.</li>"

    html_out = f"""<!doctype html>
<html lang=\"pl\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Swetrowo | Market Brief</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Space+Mono:wght@400;700&display=swap');
    :root {{
      --bg: #0b0f1a;
      --bg-alt: #101827;
      --card: #111827;
      --card-soft: rgba(17, 24, 39, 0.82);
      --line: rgba(148, 163, 184, 0.22);
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #38bdf8;
      --accent-2: #f97316;
      --green: #22c55e;
      --red: #ef4444;
      --amber: #f59e0b;
      --meme: #facc15;
    }}

    /* === MACRO PULSE === */
    .macro-pulse {{
      max-width: 1280px;
      margin: 14px auto 0;
      padding: 0 32px;
    }}
    .macro-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8px;
      padding: 0 4px;
    }}
    .macro-title {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 16px;
      letter-spacing: 2px;
      color: var(--accent);
      text-transform: uppercase;
    }}
    .macro-ts {{
      font-size: 11px;
      color: var(--muted);
    }}
    .macro-track {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .macro-item {{
      display: flex;
      flex-direction: column;
      align-items: center;
      background: rgba(15, 23, 42, 0.85);
      border: 1px solid rgba(56, 189, 248, 0.22);
      border-radius: 12px;
      padding: 8px 14px;
      min-width: 90px;
      transition: border-color 0.2s;
    }}
    .macro-item:hover {{ border-color: rgba(56, 189, 248, 0.55); }}
    .macro-label {{
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--muted);
      margin-bottom: 3px;
    }}
    .macro-price {{
      font-size: 15px;
      font-weight: 700;
      color: var(--text);
    }}
    .macro-chg {{
      font-size: 11px;
      margin-top: 2px;
    }}

    /* === ALERTY === */
    .alert-count-badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(239,68,68,0.15);
      border: 1px solid rgba(239,68,68,0.4);
      color: var(--red);
      border-radius: 999px;
      font-size: 11px;
      padding: 4px 10px;
      margin-left: 12px;
      letter-spacing: 1px;
    }}
    tr.alert-buy   {{ background: rgba(34,197,94,0.06); }}
    tr.alert-sell  {{ background: rgba(239,68,68,0.06); }}
    tr.alert-watch {{ background: rgba(245,158,11,0.04); }}
    .pill.buy   {{ background: rgba(34,197,94,0.15);  color: var(--green); border-color: rgba(34,197,94,0.4); }}
    .pill.sell  {{ background: rgba(239,68,68,0.15);  color: var(--red);   border-color: rgba(239,68,68,0.4); }}
    .pill.watch {{ background: rgba(245,158,11,0.15); color: var(--amber); border-color: rgba(245,158,11,0.4); }}
    .mkt-badge {{
      font-size: 10px;
      background: rgba(56,189,248,0.1);
      border: 1px solid rgba(56,189,248,0.3);
      color: var(--accent);
      border-radius: 6px;
      padding: 2px 6px;
      text-transform: uppercase;
      letter-spacing: 1px;
    }}

    /* === ZAKŁADKI SEKTOROWE === */
    .tabs {{
      display: flex;
      gap: 8px;
      margin-bottom: 18px;
      flex-wrap: wrap;
    }}
    .tab-btn {{
      padding: 7px 16px;
      border-radius: 999px;
      border: 1px solid rgba(56,189,248,0.35);
      background: rgba(56,189,248,0.08);
      color: var(--muted);
      font-family: 'Space Mono', monospace;
      font-size: 12px;
      cursor: pointer;
      letter-spacing: 1px;
      text-transform: uppercase;
      transition: 0.2s;
    }}
    .tab-btn:hover, .tab-btn.active {{
      background: rgba(56,189,248,0.2);
      border-color: var(--accent);
      color: var(--text);
    }}
    .tab-panel {{ display: none; }}
    .tab-panel.active {{ display: block; }}

    /* === SORTOWANIE === */
    th.sortable {{
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }}
    th.sortable:hover {{ color: var(--accent); }}
    th.sort-asc::after  {{ content: ' ▲'; color: var(--accent); }}
    th.sort-desc::after {{ content: ' ▼'; color: var(--accent); }}

    /* === REFRESH COUNTDOWN === */
    .refresh-bar {{
      position: fixed;
      bottom: 12px;
      right: 20px;
      background: rgba(15,23,42,0.88);
      border: 1px solid rgba(56,189,248,0.3);
      border-radius: 999px;
      padding: 6px 16px;
      font-size: 11px;
      color: var(--muted);
      z-index: 99;
      backdrop-filter: blur(8px);
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: 'Space Mono', monospace;
      color: var(--text);
      background:
        radial-gradient(circle at 12% 10%, rgba(250, 204, 21, 0.22), transparent 35%),
        radial-gradient(circle at 80% 8%, rgba(56, 189, 248, 0.22), transparent 35%),
        radial-gradient(circle at 30% 70%, rgba(249, 115, 22, 0.16), transparent 45%),
        repeating-linear-gradient(135deg, rgba(255, 255, 255, 0.03) 0 8px, transparent 8px 16px),
        var(--bg);
      min-height: 100vh;
    }}

    a {{ color: inherit; text-decoration: none; }}

    header {{
      position: sticky;
      top: 0;
      z-index: 10;
      backdrop-filter: blur(14px);
      background: rgba(10, 15, 28, 0.8);
      border-bottom: 1px solid var(--line);
    }}

    .nav {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 32px;
      max-width: 1280px;
      margin: 0 auto;
    }}

    .logo {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 26px;
      letter-spacing: 2px;
      text-transform: uppercase;
    }}

    .forum-frame {{
      max-width: 1280px;
      margin: 26px auto 0;
      padding: 0 32px;
      background: transparent;
      border: none;
    }}

    .forum-frame::after {{
      display: none;
    }}

    .forum-card {{
      border: 2px solid rgba(56, 189, 248, 0.5);
      background: linear-gradient(120deg, rgba(15, 23, 42, 0.92), rgba(30, 64, 175, 0.35));
      border-radius: 20px;
      padding: 18px 22px;
      display: grid;
      gap: 14px;
      box-shadow: 0 16px 28px rgba(2, 6, 23, 0.6);
    }}

    .forum-header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }}

    .forum-header h2 {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 24px;
      letter-spacing: 1px;
    }}

    .forum-meta {{
      color: var(--muted);
      font-size: 12px;
    }}

    .forum-badge {{
      padding: 6px 12px;
      border-radius: 999px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 1px;
      color: var(--accent);
      border: 1px solid rgba(34, 211, 238, 0.5);
      background: rgba(34, 211, 238, 0.1);
    }}

    .headline-list {{
      list-style: none;
      display: grid;
      gap: 10px;
      font-size: 14px;
    }}

    .headline-list li {{
      display: flex;
      align-items: baseline;
      gap: 8px;
    }}

    .headline-date {{
      display: inline-block;
      font-size: 11px;
      color: var(--muted);
      margin-right: 10px;
      text-transform: uppercase;
      letter-spacing: 1px;
    }}

    .headline-list li a {{
      color: var(--text);
      transition: color 0.2s ease;
    }}

    .headline-list li a:hover {{
      color: var(--accent-2);
    }}

    .nav-links {{
      display: flex;
      gap: 18px;
      font-size: 14px;
      color: var(--muted);
    }}

    .nav-links a {{
      padding: 6px 12px;
      border-radius: 999px;
      border: 1px solid rgba(56, 189, 248, 0.3);
      background: rgba(56, 189, 248, 0.08);
      color: var(--text);
      transition: 0.2s ease;
    }}

    .nav-links a:hover {{
      border-color: rgba(249, 115, 22, 0.6);
      background: rgba(249, 115, 22, 0.18);
      color: var(--accent-2);
    }}

    .hero {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 32px 32px 12px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }}

    .ticker-strip {{
      border: 2px solid rgba(239, 68, 68, 0.5);
      background: rgba(127, 29, 29, 0.35);
      border-radius: 16px;
      padding: 12px 16px;
      overflow: hidden;
    }}

    .ticker-strip .ticker-track span {{
      color: #f87171;
    }}

    .chart-card {{
      border: 2px solid rgba(56, 189, 248, 0.45);
      border-radius: 20px;
      padding: 18px;
      background: rgba(15, 23, 42, 0.9);
      box-shadow: 0 16px 30px rgba(2, 6, 23, 0.55);
    }}

    .table-wrap.compact {{
      max-height: 220px;
    }}

    .compact-table {{
      min-width: 520px;
    }}

    .chart-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 10px;
    }}

    .chart-title {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 22px;
      letter-spacing: 1px;
    }}

    .chart-sub {{
      font-size: 11px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 1px;
    }}

    .chart-legend {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 1px;
    }}

    .legend-item {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
    }}

    .legend-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--accent);
    }}

    .legend-dot.bullish {{ background: var(--green); }}
    .legend-dot.bearish {{ background: var(--red); }}
    .legend-dot.neutral {{ background: var(--amber); }}

    #sentimentChart {{
      width: 100%;
      height: 220px;
      display: block;
    }}

    .chart-empty {{
      font-size: 12px;
      color: var(--muted);
      margin-top: 10px;
    }}

    .hero-card {{
      background: linear-gradient(120deg, rgba(15, 23, 42, 0.9), rgba(30, 64, 175, 0.28));
      border: 2px solid rgba(56, 189, 248, 0.45);
      padding: 32px;
      border-radius: 24px;
      box-shadow: 0 18px 40px rgba(3, 7, 18, 0.6);
    }}

    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 2px;
      color: var(--meme);
      background: rgba(250, 204, 21, 0.12);
      border: 1px solid rgba(250, 204, 21, 0.4);
      padding: 6px 10px;
      border-radius: 999px;
      margin-bottom: 12px;
    }}

    h1 {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: clamp(48px, 7vw, 76px);
      line-height: 0.95;
      margin-bottom: 12px;
      letter-spacing: 2px;
    }}

    .hero p {{
      font-size: 16px;
      color: var(--muted);
      margin-bottom: 24px;
    }}

    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }}

    .meme-tags {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 18px;
    }}

    .meme-tag {{
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 11px;
      letter-spacing: 1px;
      text-transform: uppercase;
      background: rgba(56, 189, 248, 0.12);
      border: 1px solid rgba(56, 189, 248, 0.35);
      color: var(--text);
    }}

    .meme-tag.hot {{
      background: rgba(249, 115, 22, 0.2);
      border-color: rgba(249, 115, 22, 0.5);
      color: var(--accent-2);
    }}

    .meta-card {{
      background: rgba(15, 23, 42, 0.7);
      border: 2px solid rgba(56, 189, 248, 0.25);
      padding: 14px 16px;
      border-radius: 16px;
      font-size: 13px;
      color: var(--muted);
    }}

    .meta-card strong {{
      display: block;
      color: var(--text);
      font-size: 14px;
      margin-bottom: 4px;
    }}

    .ticker-tape {{
      overflow: hidden;
      border-radius: 18px;
      border: 2px dashed rgba(249, 115, 22, 0.5);
      background: rgba(17, 24, 39, 0.8);
      padding: 18px;
    }}

    .ticker-track {{
      display: inline-flex;
      gap: 28px;
      white-space: nowrap;
      animation: ticker 18s linear infinite;
      font-size: 14px;
      text-transform: uppercase;
      letter-spacing: 1px;
    }}

    .ticker-track span {{
      color: var(--accent-2);
    }}

    @keyframes ticker {{
      0% {{ transform: translateX(0); }}
      100% {{ transform: translateX(-50%); }}
    }}

    main {{
      max-width: 1280px;
      margin: 0 auto 80px;
      padding: 0 32px;
      display: flex;
      flex-direction: column;
      gap: 36px;
    }}

    section {{
      background: rgba(15, 23, 42, 0.88);
      border: 2px solid rgba(148, 163, 184, 0.25);
      border-radius: 24px;
      padding: 28px;
      position: relative;
      overflow: hidden;
      box-shadow: 0 14px 30px rgba(2, 6, 23, 0.55);
    }}

    section::after {{
      content: '';
      position: absolute;
      inset: 0;
      background: linear-gradient(120deg, rgba(250, 204, 21, 0.08), transparent 60%);
      pointer-events: none;
    }}

    section h2 {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 28px;
      margin-bottom: 6px;
      letter-spacing: 1px;
    }}

    section p {{
      color: var(--muted);
      font-size: 14px;
      margin-bottom: 18px;
    }}

    .stat-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
      margin-bottom: 22px;
    }}

    .stat-card {{
      background: rgba(15, 23, 42, 0.75);
      border: 2px solid rgba(250, 204, 21, 0.18);
      border-left: 4px solid var(--meme);
      border-radius: 18px;
      padding: 14px 16px;
    }}

    .stat-card .label {{
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 1px;
    }}

    .stat-card .value {{
      font-size: 22px;
      font-weight: 600;
      margin-top: 6px;
    }}

    .grid-2 {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 18px;
    }}

    .table-wrap {{
      overflow: auto;
      max-height: 420px;
      border: 2px solid rgba(148, 163, 184, 0.22);
      border-radius: 18px;
      background: rgba(11, 15, 26, 0.7);
    }}

    h3 {{
      font-family: 'Bebas Neue', sans-serif;
      font-size: 20px;
      margin-bottom: 10px;
      margin-top: 16px;
      letter-spacing: 1px;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 620px;
    }}

    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid rgba(148, 163, 184, 0.12);
      text-align: left;
      font-size: 13px;
    }}

    th {{
      text-transform: uppercase;
      letter-spacing: 1px;
      font-size: 11px;
      color: var(--muted);
      background: rgba(15, 23, 42, 0.85);
      position: sticky;
      top: 0;
      z-index: 2;
    }}

    tbody tr:hover {{
      background: rgba(56, 189, 248, 0.08);
    }}

    .pill {{
      display: inline-flex;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 1px;
      border: 1px solid transparent;
    }}

    .pill.bullish {{ background: rgba(34, 197, 94, 0.15); color: var(--green); border-color: rgba(34, 197, 94, 0.4); }}
    .pill.bearish {{ background: rgba(239, 68, 68, 0.15); color: var(--red); border-color: rgba(239, 68, 68, 0.4); }}
    .pill.neutral {{ background: rgba(245, 158, 11, 0.15); color: var(--amber); border-color: rgba(245, 158, 11, 0.4); }}

    footer {{
      max-width: 1280px;
      margin: 0 auto 40px;
      padding: 0 32px;
      color: var(--muted);
      font-size: 12px;
    }}

    .reveal {{
      opacity: 0;
      transform: translateY(20px);
      transition: 0.8s ease;
    }}

    .reveal.visible {{
      opacity: 1;
      transform: translateY(0);
    }}

    @media (max-width: 960px) {{
      .nav {{ flex-direction: column; gap: 10px; }}
      .nav-links {{ flex-wrap: wrap; justify-content: center; }}
      .macro-track {{ gap: 6px; }}
      .macro-item {{ min-width: 70px; padding: 6px 10px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class=\"nav\">
      <div class=\"logo\">Swetrowo <span style=\"font-size:13px;color:var(--accent);letter-spacing:1px;\">Market Brief</span></div>
      <nav class=\"nav-links\">
        <a href=\"#macro\">Makro</a>
        <a href=\"#alerts\">Alerty</a>
        <a href=\"#fundamentals\">Fundamenty</a>
        <a href=\"#sentiment\">Sentyment</a>
        <a href=\"#tech-markets\">Techniczne</a>
      </nav>
    </div>
  </header>
  {macro_pulse_html}

  <section class=\"hero\">
    <div class=\"ticker-strip reveal\">
      <div class=\"ticker-track\">{ticker_tape}{ticker_tape}</div>
    </div>
    <div class=\"hero-card reveal\">
      <div class=\"badge\">HODL Desk</div>
      <h1>Swetrowo</h1>
      <p>Feed społeczności inwestycyjnej: HODL, STONKS, FOMO i szybki przegląd sentymentu oraz technicznych filtrów.</p>
      <div class=\"meme-tags\">
        <span class=\"meme-tag hot\">HODL</span>
        <span class=\"meme-tag\">STONKS</span>
        <span class=\"meme-tag\">DIAMOND HANDS</span>
        <span class=\"meme-tag hot\">FOMO</span>
        <span class=\"meme-tag\">WIG20</span>
      </div>
      <div class=\"meta-grid\">
        <div class=\"meta-card\"><strong>Sentyment</strong>{esc(sentiment_updated)}</div>
        <div class=\"meta-card\"><strong>Techniczne PL</strong>{esc(tech_pl_updated)}</div>
        <div class=\"meta-card\"><strong>Techniczne Global</strong>{esc(tech_zagr_updated)}</div>
      </div>
    </div>
    <div class=\"chart-card reveal\">
      <div class=\"chart-header\">
        <div>
          <div class=\"chart-title\">Sentiment Pulse</div>
          <div class=\"chart-sub\">Liczba spółek wg sentymentu w czasie (BULL / BEAR / NEUTRAL)</div>
        </div>
        <div class=\"chart-legend\">
          <span class=\"legend-item\"><span class=\"legend-dot bullish\"></span>Bullish</span>
          <span class=\"legend-item\"><span class=\"legend-dot bearish\"></span>Bearish</span>
          <span class=\"legend-item\"><span class=\"legend-dot neutral\"></span>Neutral</span>
        </div>
      </div>
      <canvas id=\"sentimentChart\"></canvas>
      <div class=\"chart-empty\" id=\"chartEmpty\">Brak danych timeline. Uruchom sentiment_timeline.py / wygeneruj nowe raporty.</div>
    </div>
    <div class=\"chart-card reveal\">
      <div class=\"chart-header\">
        <div>
          <div class=\"chart-title\">Combined Analysis</div>
          <div class=\"chart-sub\">Źródło: combined_analysis.py · {esc(combined_report_path.name if combined_report_path else '-')}</div>
        </div>
      </div>
      {combined_table}
    </div>
  </section>

  <section class=\"forum-frame reveal\">
    <div class=\"forum-card\">
      <div class=\"forum-header\">
        <div>
          <h2>Forum Pulse: świeże wątki</h2>
          <div class=\"forum-meta\">Źródło: {esc(forum_source)} · Aktualizacja: {esc(forum_updated_display)}</div>
        </div>
        <div class=\"forum-badge\">Forum Pulse</div>
      </div>
      <ul class=\"headline-list\">
        {forum_items}
      </ul>
    </div>
  </section>

  <div class="refresh-bar" id="refreshBar">&#128337; Odświeżenie za: <span id="countdownVal">5:00</span></div>

  <main>
    <section id=\"alerts\" class=\"reveal\">
      <h2>Alerty Techniczne <span class=\"alert-count-badge\">{alerts_high} &uarr; WYSOKICH &middot; {alerts_count} łącznie</span></h2>
      <p>RSI ekstremalne, Golden/Death Cross, skoki wolumenu, 52-tygodniowe szczyty/dółki.</p>
      {alerts_html_body}
    </section>

    <section id=\"fundamentals\" class=\"reveal\">
      <h2>Fundamenty GPW <span class=\"alert-count-badge\" style=\"background:rgba(56,189,248,0.1);border-color:rgba(56,189,248,0.4);color:var(--accent);\">{num_fundamentals} spółek</span></h2>
      <p>Dane fundamentalne WIG20 + mWIG40: P/E, dywidenda, ROE, P/BV — źródło: Yahoo Finance / yfinance.</p>
      {fundamentals_html}
    </section>

    <section id=\"sentiment\" class=\"reveal\">
      <h2>Sentyment: Antigrav</h2>
      <p>Wyciąg z raportu sentimentu (okres: {esc(sentiment_meta.get('period') or '-')}, czas wykonania: {esc(sentiment_meta.get('duration') or '-')}).</p>
      <div class=\"stat-grid\">
        <div class=\"stat-card\"><div class=\"label\">Bullish</div><div class=\"value\">{sentiment_summary['Bullish']}</div></div>
        <div class=\"stat-card\"><div class=\"label\">Neutral</div><div class=\"value\">{sentiment_summary['Neutral']}</div></div>
        <div class=\"stat-card\"><div class=\"label\">Bearish</div><div class=\"value\">{sentiment_summary['Bearish']}</div></div>
      </div>
      <div class=\"grid-2\">
        <div>
          <h3>Top pozytywne</h3>
          {render_table(top_sent_headers_pos, top_positive_rows)}
        </div>
        <div>
          <h3>Top negatywne</h3>
          {render_table(top_sent_headers_neg, top_negative_rows)}
        </div>
      </div>
      <div style=\"margin-top:18px;\">
        <h3>Pełny raport sentymentu</h3>
        {render_table(sentiment_table_headers, sentiment_display_rows)}
      </div>
    </section>

    <section id=\"tech-markets\" class=\"reveal\">
      <h2>Analiza Techniczna</h2>
      <p>Raporty wskaźników RSI, MACD, Bollinger, MFI — GPW i globalnie.</p>

      <div class=\"tabs\">
        <button class=\"tab-btn active\" onclick=\"switchTab(event,'tab-pl')\">Polska (GPW)</button>
        <button class=\"tab-btn\" onclick=\"switchTab(event,'tab-global')\">Rynki Globalne</button>
      </div>

      <div id=\"tab-pl\" class=\"tab-panel active\">
        <div class=\"stat-grid\">
          <div class=\"stat-card\"><div class=\"label\">Bullish</div><div class=\"value\">{tech_pl_counts.get('Bullish', 0)}</div></div>
          <div class=\"stat-card\"><div class=\"label\">Neutral</div><div class=\"value\">{tech_pl_counts.get('Neutral', 0)}</div></div>
          <div class=\"stat-card\"><div class=\"label\">Bearish</div><div class=\"value\">{tech_pl_counts.get('Bearish', 0) + tech_pl_counts.get('Strong Bearish', 0)}</div></div>
          <div class=\"stat-card\"><div class=\"label\">Data danych</div><div class=\"value\" style=\"font-size:15px;\">{esc(tech_pl_latest.strftime('%Y-%m-%d') if tech_pl_latest else '-')}</div></div>
        </div>
        <div class=\"grid-2\">
          <div><h3>Najmocniejsze</h3>{render_table(tech_headers, tech_pl_top_rows)}</div>
          <div><h3>Najsłabsze</h3>{render_table(tech_headers, tech_pl_bottom_rows)}</div>
        </div>
        <div id=\"tech-pl\" style=\"margin-top:18px;\"><h3>Pełna lista GPW</h3>{render_table(tech_headers, tech_pl_full_rows)}</div>
      </div>

      <div id=\"tab-global\" class=\"tab-panel\">
        <div class=\"stat-grid\">
          <div class=\"stat-card\"><div class=\"label\">Bullish</div><div class=\"value\">{tech_zagr_counts.get('Bullish', 0)}</div></div>
          <div class=\"stat-card\"><div class=\"label\">Neutral</div><div class=\"value\">{tech_zagr_counts.get('Neutral', 0)}</div></div>
          <div class=\"stat-card\"><div class=\"label\">Bearish</div><div class=\"value\">{tech_zagr_counts.get('Bearish', 0) + tech_zagr_counts.get('Strong Bearish', 0)}</div></div>
          <div class=\"stat-card\"><div class=\"label\">Data danych</div><div class=\"value\" style=\"font-size:15px;\">{esc(tech_zagr_latest.strftime('%Y-%m-%d') if tech_zagr_latest else '-')}</div></div>
        </div>
        <div class=\"grid-2\">
          <div><h3>Najmocniejsze</h3>{render_table(tech_headers, tech_zagr_top_rows)}</div>
          <div><h3>Najsłabsze</h3>{render_table(tech_headers, tech_zagr_bottom_rows)}</div>
        </div>
        <div id=\"tech-global\" style=\"margin-top:18px;\"><h3>Pełna lista Global</h3>{render_table(tech_headers, tech_zagr_full_rows)}</div>
      </div>
    </section>
  </main>

  <footer>
    <p>Swetrowo Market Brief &mdash; dane: GPW + global | Źródła: {esc(sentiment_report_path.name if sentiment_report_path else '-')}, {esc(tech_pl_report_path.name if tech_pl_report_path else '-')}, {esc(tech_zagr_report_path.name if tech_zagr_report_path else '-')}. Wygenerowano: {esc(datetime.now().strftime('%Y-%m-%d %H:%M'))}.</p>
  </footer>

  <script>
    const sentimentTimeline = {timeline_json};
    const chartEmpty = document.getElementById('chartEmpty');
    const chartCanvas = document.getElementById('sentimentChart');

    function niceAxisMax(value) {{
      if (value <= 0) return 1;
      const exponent = Math.floor(Math.log10(value));
      const magnitude = Math.pow(10, exponent);
      const residual = value / magnitude;
      let niceResidual;
      if (residual <= 1) niceResidual = 1;
      else if (residual <= 2) niceResidual = 2;
      else if (residual <= 5) niceResidual = 5;
      else niceResidual = 10;
      return niceResidual * magnitude;
    }}

    function formatAxisDate(dateStr) {{
      // dateStr like "2026-08-13 15:55"
      const [datePart] = dateStr.split(' ');
      const [, m, d] = datePart.split('-');
      return `${{d}}.${{m}}`;
    }}

    function pickLabelIndices(count, maxLabels) {{
      if (count <= maxLabels) {{
        return Array.from({{ length: count }}, (_, i) => i);
      }}
      const idxs = new Set();
      for (let i = 0; i < maxLabels; i++) {{
        idxs.add(Math.round(i * (count - 1) / (maxLabels - 1)));
      }}
      return [...idxs].sort((a, b) => a - b);
    }}

    function drawSentimentChart(data) {{
      if (!chartCanvas) return;
      const ctx = chartCanvas.getContext('2d');
      if (!ctx || data.length < 2) {{
        chartEmpty.style.display = 'block';
        return;
      }}
      chartEmpty.style.display = 'none';

      const dpr = window.devicePixelRatio || 1;
      const width = chartCanvas.clientWidth;
      const height = chartCanvas.clientHeight || 220;
      chartCanvas.width = width * dpr;
      chartCanvas.height = height * dpr;
      ctx.scale(dpr, dpr);

      const padding = {{ left: 32, right: 16, top: 16, bottom: 32 }};
      const innerWidth = width - padding.left - padding.right;
      const innerHeight = height - padding.top - padding.bottom;

      const rawMax = Math.max(
        ...data.map(row => Math.max(row.bullish, row.bearish, row.neutral, 1))
      );
      const gridLines = 4;
      const maxVal = niceAxisMax(rawMax);

      const xStep = innerWidth / (data.length - 1);
      const scaleY = val => padding.top + innerHeight * (1 - val / maxVal);
      const scaleX = idx => padding.left + idx * xStep;

      ctx.clearRect(0, 0, width, height);

      // Horizontal grid lines with Y-axis value labels (unit: liczba spółek)
      ctx.strokeStyle = 'rgba(148, 163, 184, 0.2)';
      ctx.lineWidth = 1;
      ctx.font = '10px "Space Mono", monospace';
      ctx.fillStyle = 'rgba(226, 232, 240, 0.75)';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      for (let i = 0; i <= gridLines; i++) {{
        const y = padding.top + (innerHeight / gridLines) * i;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(width - padding.right, y);
        ctx.stroke();

        const val = Math.round(maxVal - (maxVal / gridLines) * i);
        ctx.fillText(String(val), padding.left - 6, y);
      }}
      ctx.textBaseline = 'alphabetic';

      // X-axis tick marks + date labels
      const maxLabels = Math.max(2, Math.floor(innerWidth / 55));
      const labelIdxs = pickLabelIndices(data.length, maxLabels);
      ctx.strokeStyle = 'rgba(148, 163, 184, 0.35)';
      ctx.fillStyle = 'rgba(226, 232, 240, 0.85)';
      labelIdxs.forEach(idx => {{
        const x = scaleX(idx);
        ctx.beginPath();
        ctx.moveTo(x, height - padding.bottom);
        ctx.lineTo(x, height - padding.bottom + 4);
        ctx.stroke();

        if (idx === 0) {{
          ctx.textAlign = 'left';
        }} else if (idx === data.length - 1) {{
          ctx.textAlign = 'right';
        }} else {{
          ctx.textAlign = 'center';
        }}
        ctx.fillText(formatAxisDate(data[idx].date), x, height - 8);
      }});

      function drawLine(key, color) {{
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        data.forEach((row, idx) => {{
          const x = scaleX(idx);
          const y = scaleY(row[key]);
          if (idx === 0) {{
            ctx.moveTo(x, y);
          }} else {{
            ctx.lineTo(x, y);
          }}
        }});
        ctx.stroke();

        ctx.fillStyle = color;
        data.forEach((row, idx) => {{
          const x = scaleX(idx);
          const y = scaleY(row[key]);
          ctx.beginPath();
          ctx.arc(x, y, 3, 0, Math.PI * 2);
          ctx.fill();
        }});
      }}

      drawLine('bullish', '#22c55e');
      drawLine('bearish', '#ef4444');
      drawLine('neutral', '#f59e0b');
    }}

    const observer = new IntersectionObserver((entries) => {{
      entries.forEach(entry => {{
        if (entry.isIntersecting) {{
          entry.target.classList.add('visible');
        }}
      }});
    }}, {{ threshold: 0.15 }});

    document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
    drawSentimentChart(sentimentTimeline);
    window.addEventListener('resize', () => drawSentimentChart(sentimentTimeline));

    /* === SORTOWANIE TABEL === */
    function makeSortable(table) {{
      const headers = table.querySelectorAll('th[data-col]');
      headers.forEach(th => {{
        th.classList.add('sortable');
        th.addEventListener('click', () => {{
          const col = parseInt(th.dataset.col);
          const asc = th.classList.contains('sort-asc');
          headers.forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
          th.classList.add(asc ? 'sort-desc' : 'sort-asc');
          const tbody = table.querySelector('tbody');
          const rows  = Array.from(tbody.querySelectorAll('tr'));
          rows.sort((a, b) => {{
            const av = a.cells[col]?.innerText.trim() || '';
            const bv = b.cells[col]?.innerText.trim() || '';
            const an = parseFloat(av.replace(/[^0-9.-]/g, ''));
            const bn = parseFloat(bv.replace(/[^0-9.-]/g, ''));
            if (!isNaN(an) && !isNaN(bn)) return asc ? bn - an : an - bn;
            return asc ? bv.localeCompare(av, 'pl') : av.localeCompare(bv, 'pl');
          }});
          rows.forEach(r => tbody.appendChild(r));
        }});
      }});
    }}

    document.querySelectorAll('table').forEach(t => {{
      const ths = t.querySelectorAll('th');
      ths.forEach((th, i) => {{ if (!th.dataset.col) th.dataset.col = i; }});
      makeSortable(t);
    }});

    /* === ZAKŁADKI === */
    function switchTab(e, panelId) {{
      const section = e.target.closest('section');
      section.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      section.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      e.target.classList.add('active');
      document.getElementById(panelId)?.classList.add('active');
    }}

    /* === COUNTDOWN DO ODŚWIEżENIA === */
    (function() {{
      const totalSec = 5 * 60;
      let remaining = totalSec;
      const el = document.getElementById('countdownVal');
      if (!el) return;
      setInterval(() => {{
        remaining--;
        if (remaining <= 0) {{ location.reload(); return; }}
        const m = Math.floor(remaining / 60);
        const s = remaining % 60;
        el.textContent = m + ':' + String(s).padStart(2, '0');
      }}, 1000);
    }})();
  </script>
</body>
</html>
"""
    return html_out


def main():
    sentiment_report, sentiment_dt, _ = find_latest_report(SENTIMENT_DIR, "sentiment_report_*.txt")
    tech_pl_report, tech_pl_dt, _ = find_latest_report(TECH_DIR, "techincals_report_*.csv")
    tech_zagr_report, tech_zagr_dt, _ = find_latest_report(TECH_DIR, "techincals_zagr_report_*.csv")
    combined_report, combined_dt, _ = find_latest_report(COMBINED_DIR, "combined_*.csv")

    if not sentiment_report or not tech_pl_report or not tech_zagr_report:
        missing = []
        if not sentiment_report:
            missing.append("sentiment")
        if not tech_pl_report:
            missing.append("technical PL")
        if not tech_zagr_report:
            missing.append("technical global")
        raise SystemExit(f"Missing report files: {', '.join(missing)}")

    sentiment_meta, sentiment_rows, sentiment_summary, top_positive, top_negative = parse_sentiment_report(sentiment_report)
    tech_pl_rows, tech_pl_counts, tech_pl_top_up, tech_pl_top_down, tech_pl_latest = parse_technical_csv(tech_pl_report)
    tech_zagr_rows, tech_zagr_counts, tech_zagr_top_up, tech_zagr_top_down, tech_zagr_latest = parse_technical_csv(tech_zagr_report)
    combined_headers, combined_rows = parse_combined_report(combined_report, limit=12)

    forum_headlines = []
    forum_updated = None
    try:
        forum_headlines = asyncio.run(fetch_forum_headlines_async(FORUM_URL, FORUM_HEADLINES_LIMIT))
        forum_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except Exception as exc:
        print(f"Warning: failed to fetch forum headlines: {exc}")

    sentiment_timeline = load_sentiment_timeline(SENTIMENT_DIR, limit=20)

    # Nowe źródła danych v2
    macro_snapshot   = load_macro_snapshot()
    alerts_data      = load_alerts_data()
    fundamentals_data = load_fundamentals_gpw()
    if macro_snapshot:
        print(f"Macro snapshot: {macro_snapshot.get('generated_at', '?')}")
    print(f"Alerty: {len(alerts_data)} szt.")
    print(f"Fundamenty GPW: {len(fundamentals_data)} spółek")

    html_out = build_html(
        forum_headlines,
        forum_updated,
        FORUM_SOURCE_LABEL,
        sentiment_timeline,
        combined_headers,
        combined_rows,
        combined_report,
        sentiment_meta,
        sentiment_rows,
        sentiment_summary,
        top_positive,
        top_negative,
        tech_pl_rows,
        tech_pl_counts,
        tech_pl_top_up,
        tech_pl_top_down,
        tech_pl_latest,
        tech_zagr_rows,
        tech_zagr_counts,
        tech_zagr_top_up,
        tech_zagr_top_down,
        tech_zagr_latest,
        sentiment_report,
        tech_pl_report,
        tech_zagr_report,
        sentiment_dt,
        tech_pl_dt,
        tech_zagr_dt,
        macro_snapshot=macro_snapshot,
        alerts_data=alerts_data,
        fundamentals_data=fundamentals_data,
    )

    output_path = BASE_DIR / "swetrowo.html"
    output_path.write_text(html_out, encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
