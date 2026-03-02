#!/usr/bin/env python3
"""
US Stock Backtesting Tool
=========================
Screens US stocks using Finviz fundamental criteria, then backtests
an equal-weight portfolio with historical SMA technical filters.

Screening Criteria:
  - Market Cap:             >= $300M
  - Stock Price:            >= $10
  - Avg Daily Volume:       >= 400K shares
  - EPS Growth (This Year): >= 25%
  - EPS Growth (Next Year): >= 25%
  - EPS Growth (QoQ):       >= 20%
  - Sales Growth (QoQ):     >= 20%
  - ROE:                    >= 15%
  - Price above 50-day MA
  - Price above 200-day MA

NOTE: Fundamental data is sourced from Finviz's CURRENT screener,
which introduces look-ahead bias for historical backtests.
The SMA50/SMA200 technical filters are applied historically at each
rebalancing date, providing a momentum overlay.

Usage:
    python stock_backtest.py                           # Run with defaults
    python stock_backtest.py --start 2019-01-01        # Custom start date
    python stock_backtest.py --rebalance quarterly     # Quarterly rebalance
    python stock_backtest.py --tickers AAPL MSFT NVDA  # Custom universe
    python stock_backtest.py --ticker-file tickers.txt # Load from file
    python stock_backtest.py --capital 500000          # Custom capital
    python stock_backtest.py --list-stocks             # Screen only, no backtest
"""

import re
import sys
import time
import argparse
import warnings
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from bs4 import BeautifulSoup

import matplotlib
matplotlib.use("Agg")
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt

warnings.filterwarnings("ignore")

# ============================================================
# CONSTANTS
# ============================================================

SCREENING_SUMMARY = """
Screening Criteria:
  Market Cap:               >= $300M
  Stock Price:              >= $10
  Avg Daily Volume:         >= 400K
  EPS Growth (This Year):   >= 25%
  EPS Growth (Next Year):   >= 25%
  EPS Growth QoQ:           >= 20%
  Sales Growth QoQ:         >= 20%
  ROE:                      >= 15%
  Price vs 50-day MA:       Above
  Price vs 200-day MA:      Above
"""

# Finviz filter codes
# Reference: https://finviz.com/screener.ashx
FINVIZ_FILTERS = [
    "cap_smallover",     # Market cap >= $300M (Small cap and above)
    "sh_price_o10",      # Stock price > $10
    "sh_avgvol_o400",    # Average volume > 400K
    "fa_epsthisY_o25",   # EPS growth this year > 25%
    "fa_epsnextY_o25",   # EPS growth next year > 25%
    "fa_epsqoq_o20",     # EPS growth quarter-over-quarter > 20%
    "fa_salesqoq_o20",   # Sales growth quarter-over-quarter > 20%
    "fa_roe_o15",        # Return on equity > 15%
    "ta_sma50_pa",       # Price above 50-day SMA
    "ta_sma200_pa",      # Price above 200-day SMA
]

# Default fallback tickers if Finviz is unreachable
FALLBACK_TICKERS = [
    "NVDA", "AAPL", "MSFT", "META", "GOOGL", "AMZN",
    "AVGO", "AMD", "CRM", "NOW", "PANW", "SNPS", "CDNS",
    "ANET", "MELI", "DDOG", "CRWD", "ZS", "SHOP", "NET",
]


# ============================================================
# FINVIZ SCREENER
# ============================================================

class FinvizScreener:
    """
    Scrapes Finviz's free screener to get qualifying stock tickers.
    Applies all fundamental and technical filters via URL parameters.

    Finviz may block automated requests. When that happens the scraper
    falls back automatically to a broader set of CSS selectors and a
    regex-based ticker extraction from the raw HTML.
    """

    BASE_URL = "https://finviz.com/screener.ashx"
    HOME_URL = "https://finviz.com/"
    # Realistic browser headers including Referer
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Cache-Control": "max-age=0",
    }
    # Valid ticker: 1–5 uppercase letters (optionally ending with . + 1-2 letters)
    _TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z]{1,2})?$")

    def __init__(self, filters: List[str] = FINVIZ_FILTERS):
        self.filters = filters
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def _build_url(self, offset: int = 0) -> str:
        f = ",".join(self.filters)
        return f"{self.BASE_URL}?v=111&f={f}&r={offset + 1}"

    def _warm_up_session(self) -> None:
        """Visit homepage first to obtain cookies and avoid bot detection."""
        try:
            self.session.get(self.HOME_URL, timeout=10)
            self.session.headers["Referer"] = self.HOME_URL
            time.sleep(0.8)
        except Exception:
            pass

    def _parse_tickers(self, html: str) -> List[str]:
        """
        Extract tickers from HTML using multiple strategies:
        1. CSS class 'screener-link-primary' (Finviz standard)
        2. CSS class 'tab-link' filtered to look like tickers
        3. Regex scan of the raw HTML as last resort
        """
        soup = BeautifulSoup(html, "html.parser")

        # Strategy 1: standard class
        found = [
            a.text.strip()
            for a in soup.find_all("a", class_="screener-link-primary")
            if self._TICKER_RE.match(a.text.strip())
        ]
        if found:
            return found

        # Strategy 2: 'tab-link' anchors whose text looks like a ticker
        found = [
            a.text.strip()
            for a in soup.find_all("a", class_="tab-link")
            if self._TICKER_RE.match(a.text.strip())
        ]
        if found:
            return found

        # Strategy 3: find the screener results table and grab all cell text
        table = soup.find("table", id="screener-views-table")
        if not table:
            table = soup.find("table", {"class": lambda c: c and "screener" in c})
        if table:
            found = [
                td.get_text(strip=True)
                for td in table.find_all("td")
                if self._TICKER_RE.match(td.get_text(strip=True))
            ]
            if found:
                return found

        # Strategy 4: regex scan – look for ticker-like tokens in links
        found = list(dict.fromkeys(
            m.group(1)
            for m in re.finditer(r'quote\.ashx\?t=([A-Z]{1,5})', html)
        ))
        return found

    def _page_looks_blocked(self, html: str) -> bool:
        """Heuristic: return True if Finviz returned a challenge/CAPTCHA page."""
        blocked_signals = [
            "enable javascript",
            "captcha",
            "please wait",
            "cloudflare",
            "access denied",
            "403 forbidden",
        ]
        snippet = html[:4000].lower()
        return any(sig in snippet for sig in blocked_signals)

    def screen(self) -> List[str]:
        """
        Fetch all qualifying tickers from Finviz.
        Returns a list of ticker symbols, or an empty list on failure.
        Raises RuntimeError only on hard network errors on the first request.
        """
        self._warm_up_session()

        all_tickers: List[str] = []
        offset = 0
        url0 = self._build_url()

        print("Connecting to Finviz screener...")
        print(f"  URL: {url0}\n")

        while True:
            url = self._build_url(offset)
            try:
                resp = self.session.get(url, timeout=25)
                resp.raise_for_status()
            except requests.RequestException as e:
                if offset == 0:
                    raise RuntimeError(f"Cannot reach Finviz: {e}") from e
                print(f"\n  Request stopped at offset {offset}: {e}")
                break

            if self._page_looks_blocked(resp.text):
                print(
                    "\n  Finviz returned a challenge/block page.\n"
                    "  Try opening the URL in a browser to verify the results,\n"
                    f"  then use --tickers to pass the list manually.\n"
                    f"  URL: {url0}"
                )
                break

            page_tickers = self._parse_tickers(resp.text)
            if not page_tickers:
                # First page with no tickers — could be genuine empty result
                # or a layout change. Print a diagnostic snippet.
                if offset == 0:
                    snippet = resp.text[:500].replace("\n", " ")
                    print(f"\n  [DEBUG] No tickers found on first page.")
                    print(f"  HTTP status : {resp.status_code}")
                    print(f"  HTML snippet: {snippet}\n")
                break

            all_tickers.extend(page_tickers)
            print(f"  {len(all_tickers)} stocks found...", end="\r")

            if len(page_tickers) < 20:   # Last page
                break

            offset += 20
            time.sleep(1.5)  # Polite rate-limiting

        print(f"  {len(all_tickers)} qualifying stocks found.          ")
        return all_tickers


# ============================================================
# PRICE DATA MANAGER
# ============================================================

class PriceData:
    """
    Downloads and caches historical OHLCV data via yfinance.
    Computes SMA50 and SMA200 for each ticker.
    """

    MIN_HISTORY_DAYS = 210  # Need 200+ days for SMA200

    def __init__(self, start: str, end: str, commission: float = 0.001):
        self.start = start
        self.end = end
        self.commission = commission
        self._store: Dict[str, pd.DataFrame] = {}

    # ----------------------------------------------------------
    def load(self, tickers: List[str], chunk_size: int = 50) -> Dict[str, pd.DataFrame]:
        """
        Download OHLCV data for all tickers in chunks.
        Returns dict of {ticker: DataFrame with Close/SMA50/SMA200}.
        """
        print(f"\nDownloading price data  ({self.start} → {self.end}) ...")
        result: Dict[str, pd.DataFrame] = {}

        for i in range(0, len(tickers), chunk_size):
            chunk = tickers[i : i + chunk_size]
            try:
                raw = yf.download(
                    chunk,
                    start=self.start,
                    end=self.end,
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                    timeout=30,
                )
            except Exception as e:
                print(f"\n  Chunk download failed: {e}")
                if i + chunk_size < len(tickers):
                    time.sleep(1)
                continue

            if raw.empty:
                continue

            for ticker in chunk:
                try:
                    if len(chunk) == 1:
                        df = raw.copy()
                    elif isinstance(raw.columns, pd.MultiIndex):
                        df = raw.xs(ticker, axis=1, level=1).copy()
                    else:
                        continue

                    df = df.dropna(how="all")
                    if len(df) < self.MIN_HISTORY_DAYS:
                        continue

                    df["SMA50"] = df["Close"].rolling(50).mean()
                    df["SMA200"] = df["Close"].rolling(200).mean()
                    result[ticker] = df

                except (KeyError, Exception):
                    pass

            print(f"  {len(result)}/{len(tickers)} loaded ...", end="\r")

            if i + chunk_size < len(tickers):
                time.sleep(0.3)

        print(f"  {len(result)} stocks with sufficient history loaded.    ")
        self._store = result
        return result

    # ----------------------------------------------------------
    def price_at(self, ticker: str, date: pd.Timestamp) -> Optional[float]:
        """Return adjusted close price on or just before `date`."""
        df = self._store.get(ticker)
        if df is None:
            return None
        mask = df.index <= date
        if not mask.any():
            return None
        val = df.loc[df.index[mask][-1], "Close"]
        return float(val) if pd.notna(val) else None

    # ----------------------------------------------------------
    def passes_ma_filter(
        self, ticker: str, date: pd.Timestamp, min_price: float = 10.0
    ) -> bool:
        """
        Returns True only if:
          - Close > SMA50
          - Close > SMA200
          - Close >= min_price
        """
        df = self._store.get(ticker)
        if df is None:
            return False
        mask = df.index <= date
        if not mask.any():
            return False

        row = df.loc[df.index[mask][-1]]
        close = row.get("Close")
        sma50 = row.get("SMA50")
        sma200 = row.get("SMA200")

        if any(pd.isna(v) for v in [close, sma50, sma200]):
            return False

        c, s50, s200 = float(close), float(sma50), float(sma200)
        return c >= min_price and c > s50 and c > s200

    # ----------------------------------------------------------
    def fetch_benchmark(self, ticker: str) -> pd.DataFrame:
        """Download benchmark OHLCV data."""
        df = yf.download(
            ticker, start=self.start, end=self.end,
            auto_adjust=True, progress=False
        )
        return df


# ============================================================
# BACKTESTER
# ============================================================

class Backtester:
    """
    Simulates an equal-weight portfolio rebalanced on a fixed schedule.

    At each rebalancing date:
      1. Sell all current holdings (apply commission).
      2. Filter the universe by SMA50/SMA200 (historical, no look-ahead).
      3. Allocate equal capital to each qualifying stock (apply commission).

    Equal allocation means: each stock receives (total_cash / n_stocks).
    """

    FREQ_MAP = {
        "weekly": "W-FRI",
        "monthly": "MS",
        "quarterly": "QS",
    }

    def __init__(self, universe: List[str], pd_mgr: PriceData, cfg: dict):
        self.universe = universe
        self.pd = pd_mgr
        self.cfg = cfg

    # ----------------------------------------------------------
    def run(self) -> dict:
        rebalance_dates = pd.date_range(
            start=self.cfg["start_date"],
            end=self.cfg["end_date"],
            freq=self.FREQ_MAP.get(self.cfg["rebalance_frequency"], "MS"),
        )
        rebalance_set = set(rebalance_dates.normalize())

        daily_dates = pd.date_range(
            start=self.cfg["start_date"],
            end=self.cfg["end_date"],
            freq="B",
        )

        cash: float = self.cfg["initial_capital"]
        positions: Dict[str, float] = {}   # {ticker: shares}
        comm = self.cfg["commission_rate"]

        nav_records: list = []
        trades: list = []
        rebalance_log: list = []

        print(f"\n{'='*62}")
        print("  RUNNING BACKTEST")
        print(f"{'='*62}")
        print(f"  Period     : {self.cfg['start_date']} → {self.cfg['end_date']}")
        print(f"  Rebalance  : {self.cfg['rebalance_frequency']}")
        print(f"  Capital    : ${cash:,.0f}")
        print(f"  Universe   : {len(self.universe)} stocks")
        print(f"  Commission : {comm*100:.2f}% per trade\n")

        for date in daily_dates:
            is_rebalance = date.normalize() in rebalance_set

            # ---- REBALANCE ----
            if is_rebalance:

                # 1. Sell all holdings
                for ticker, shares in list(positions.items()):
                    price = self.pd.price_at(ticker, date)
                    if price:
                        gross = shares * price
                        net = gross * (1 - comm)
                        cash += net
                        trades.append({
                            "date": date.date(),
                            "ticker": ticker,
                            "action": "SELL",
                            "shares": round(shares, 6),
                            "price": round(price, 4),
                            "gross_value": round(gross, 2),
                            "commission": round(gross * comm, 2),
                        })
                positions = {}

                # 2. Apply historical SMA filter to universe
                selected = [
                    t for t in self.universe
                    if self.pd.passes_ma_filter(t, date, self.cfg["min_price"])
                ]

                # 3. Buy equal-weight
                if selected:
                    alloc = cash / len(selected)
                    total_cost = 0.0

                    for ticker in selected:
                        price = self.pd.price_at(ticker, date)
                        if price and price > 0:
                            # Gross-up cost for commission
                            shares = alloc / (price * (1 + comm))
                            cost = shares * price * (1 + comm)
                            if total_cost + cost <= cash + 0.01:  # Rounding buffer
                                positions[ticker] = shares
                                total_cost += cost
                                trades.append({
                                    "date": date.date(),
                                    "ticker": ticker,
                                    "action": "BUY",
                                    "shares": round(shares, 6),
                                    "price": round(price, 4),
                                    "gross_value": round(shares * price, 2),
                                    "commission": round(shares * price * comm, 2),
                                })

                    cash -= total_cost
                    cash = max(cash, 0.0)

                    print(
                        f"  {date.date()} | {len(selected):3d} stocks | "
                        f"~${alloc:>10,.0f}/stock | "
                        f"Cash: ${cash:>10,.0f}"
                    )
                else:
                    print(
                        f"  {date.date()} | No qualifying stocks | "
                        f"Holding cash: ${cash:>10,.0f}"
                    )

                rebalance_log.append({
                    "date": date.date(),
                    "num_stocks": len(selected),
                    "tickers": selected,
                    "alloc_per_stock": cash / max(len(selected), 1) if selected else 0,
                    "cash_after_rebalance": round(cash, 2),
                })

            # ---- MARK-TO-MARKET ----
            equity = sum(
                (self.pd.price_at(t, date) or 0) * s
                for t, s in positions.items()
            )
            nav_records.append({
                "date": date,
                "nav": cash + equity,
                "cash": cash,
                "equity": equity,
                "num_positions": len(positions),
            })

        nav_df = pd.DataFrame(nav_records).set_index("date")
        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()

        return {
            "nav": nav_df,
            "trades": trades_df,
            "rebalance_log": rebalance_log,
        }


# ============================================================
# PERFORMANCE ANALYZER
# ============================================================

class Analyzer:
    """Computes performance metrics and renders charts."""

    RISK_FREE = 0.05  # Annual

    def __init__(self, benchmark_ticker: str = "SPY"):
        self.bench_ticker = benchmark_ticker

    # ----------------------------------------------------------
    def metrics(
        self,
        nav_df: pd.DataFrame,
        bench_df: Optional[pd.DataFrame] = None,
    ) -> Dict[str, str]:
        """Return ordered dict of {metric_name: formatted_string}."""
        p = nav_df["nav"]
        r = p.pct_change().dropna()
        n_years = max(len(p) / 252, 0.01)

        total_ret = p.iloc[-1] / p.iloc[0] - 1
        cagr = (p.iloc[-1] / p.iloc[0]) ** (1 / n_years) - 1
        vol = r.std() * np.sqrt(252)

        rf_d = self.RISK_FREE / 252
        sharpe = (
            (r.mean() - rf_d) / r.std() * np.sqrt(252) if r.std() > 0 else 0
        )

        neg_r = r[r < rf_d]
        if len(neg_r) > 1 and neg_r.std() > 0:
            sortino = (r.mean() - rf_d) * 252 / (neg_r.std() * np.sqrt(252))
        else:
            sortino = 0.0

        cum = (1 + r).cumprod()
        max_dd = ((cum / cum.expanding().max()) - 1).min()
        calmar = cagr / abs(max_dd) if max_dd != 0 else 0

        monthly = p.resample("ME").last().pct_change().dropna()
        win_rate = (monthly > 0).mean()

        out: Dict[str, str] = {
            "--- Portfolio Metrics ---": "",
            "Total Return": f"{total_ret:+.2%}",
            "CAGR": f"{cagr:+.2%}",
            "Ann. Volatility": f"{vol:.2%}",
            "Sharpe Ratio": f"{sharpe:.3f}",
            "Sortino Ratio": f"{sortino:.3f}",
            "Calmar Ratio": f"{calmar:.3f}",
            "Max Drawdown": f"{max_dd:.2%}",
            "Monthly Win Rate": f"{win_rate:.1%}",
            "--- P&L Summary ---": "",
            "Initial Capital": f"${p.iloc[0]:,.0f}",
            "Final Value": f"${p.iloc[-1]:,.0f}",
            "Net P&L": f"${p.iloc[-1] - p.iloc[0]:+,.0f}",
        }

        if bench_df is not None and not bench_df.empty:
            b = bench_df["Close"].reindex(p.index, method="ffill").dropna()
            if len(b) > 10:
                br = b.pct_change().dropna()
                b_total = b.iloc[-1] / b.iloc[0] - 1
                b_cagr = (b.iloc[-1] / b.iloc[0]) ** (1 / n_years) - 1

                aligned = pd.concat([r, br], axis=1).dropna()
                aligned.columns = ["p", "b"]
                if len(aligned) > 10 and aligned["b"].std() > 0:
                    cov_m = np.cov(aligned["p"], aligned["b"])
                    beta = cov_m[0, 1] / cov_m[1, 1] if cov_m[1, 1] > 0 else 1.0
                    alpha = cagr - (
                        self.RISK_FREE + beta * (b_cagr - self.RISK_FREE)
                    )
                else:
                    beta, alpha = 1.0, 0.0

                out.update({
                    f"--- vs {self.bench_ticker} ---": "",
                    f"{self.bench_ticker} Total Return": f"{b_total:+.2%}",
                    f"{self.bench_ticker} CAGR": f"{b_cagr:+.2%}",
                    "Excess Return": f"{total_ret - b_total:+.2%}",
                    "Alpha (Annual)": f"{alpha:+.2%}",
                    "Beta": f"{beta:.3f}",
                })

        return out

    # ----------------------------------------------------------
    def print_report(
        self,
        metrics: Dict[str, str],
        rebalance_log: List[dict],
        trades_df: pd.DataFrame,
    ) -> None:
        print(f"\n{'='*62}")
        print("  PERFORMANCE REPORT")
        print(f"{'='*62}")
        for k, v in metrics.items():
            if k.startswith("---"):
                print(f"\n  {k}")
            else:
                print(f"  {k:<32} {v:>14}")

        if rebalance_log:
            counts = [r["num_stocks"] for r in rebalance_log if r["num_stocks"] > 0]
            print(f"\n  --- Holdings ---")
            print(f"  {'Rebalance events':<32} {len(rebalance_log):>14}")
            if counts:
                print(f"  {'Avg stocks / rebalance':<32} {np.mean(counts):>13.1f}")
                print(
                    f"  {'Max / Min stocks':<32} "
                    f"{max(counts):>6} / {min(counts):<6}"
                )

        if not trades_df.empty:
            print(f"\n  --- Trading Activity ---")
            print(f"  {'Total trades':<32} {len(trades_df):>14}")
            buys = (trades_df["action"] == "BUY").sum()
            sells = (trades_df["action"] == "SELL").sum()
            print(f"  {'Buy / Sell orders':<32} {buys:>6} / {sells:<6}")
            if "commission" in trades_df.columns:
                total_comm = trades_df["commission"].sum()
                print(f"  {'Total commission paid':<32} ${total_comm:>13,.0f}")

    # ----------------------------------------------------------
    def plot(
        self,
        nav_df: pd.DataFrame,
        bench_df: Optional[pd.DataFrame],
        rebalance_log: List[dict],
        output: str = "backtest_results.png",
        title_info: str = "",
    ) -> None:
        """Generate 5-panel performance chart."""

        fig = plt.figure(figsize=(18, 14))
        gs = gridspec.GridSpec(3, 2, figure=fig, hspace=0.48, wspace=0.35)

        p = nav_df["nav"]
        r = p.pct_change().dropna()

        # ── Panel 1: Cumulative Performance ──────────────────────
        ax1 = fig.add_subplot(gs[0, :])
        norm_p = p / p.iloc[0] * 100
        ax1.plot(p.index, norm_p, "b-", lw=2.2, label="Strategy (Equal-Weight)", zorder=3)

        if bench_df is not None and not bench_df.empty:
            b = bench_df["Close"].reindex(p.index, method="ffill").dropna()
            if len(b) > 1:
                norm_b = b / b.iloc[0] * 100
                ax1.plot(norm_b.index, norm_b, "r--", lw=1.6,
                         label=f"Benchmark ({self.bench_ticker})", alpha=0.85)

        ax1.axhline(100, color="gray", ls=":", alpha=0.5)
        ax1.set_title("Cumulative Performance (Base = 100)", fontsize=13, fontweight="bold")
        ax1.set_ylabel("Index Value")
        ax1.legend(fontsize=11)
        ax1.grid(True, alpha=0.3)

        # ── Panel 2: Drawdown ─────────────────────────────────────
        ax2 = fig.add_subplot(gs[1, 0])
        cum = (1 + r).cumprod()
        dd = (cum / cum.expanding().max() - 1) * 100
        ax2.fill_between(dd.index, dd, 0, color="tomato", alpha=0.65)
        ax2.set_title("Drawdown (%)", fontsize=12)
        ax2.set_ylabel("Drawdown (%)")
        ax2.grid(True, alpha=0.3)

        # ── Panel 3: Daily Return Distribution ───────────────────
        ax3 = fig.add_subplot(gs[1, 1])
        dr = r * 100
        ax3.hist(dr, bins=60, color="steelblue", alpha=0.75, ec="white", lw=0.4)
        ax3.axvline(0, color="red", ls="--", alpha=0.75, lw=1.2)
        ax3.axvline(
            dr.mean(), color="limegreen", ls="-", alpha=0.9, lw=1.5,
            label=f"Mean: {dr.mean():.3f}%"
        )
        ax3.set_title("Daily Return Distribution", fontsize=12)
        ax3.set_xlabel("Return (%)")
        ax3.set_ylabel("Frequency")
        ax3.legend(fontsize=10)
        ax3.grid(True, alpha=0.3)

        # ── Panel 4: Holdings per Rebalance ──────────────────────
        ax4 = fig.add_subplot(gs[2, 0])
        if rebalance_log:
            rb_dates = [pd.Timestamp(str(x["date"])) for x in rebalance_log]
            rb_counts = [x["num_stocks"] for x in rebalance_log]
            ax4.bar(rb_dates, rb_counts, color="teal", alpha=0.7, width=22)
            if rb_counts:
                avg = np.mean(rb_counts)
                ax4.axhline(avg, color="orange", ls="--", lw=1.5,
                            label=f"Avg: {avg:.0f}")
                ax4.legend(fontsize=10)
        ax4.set_title("Holdings per Rebalance", fontsize=12)
        ax4.set_ylabel("Number of Stocks")
        ax4.grid(True, alpha=0.3, axis="y")

        # ── Panel 5: Monthly Returns Heatmap ─────────────────────
        ax5 = fig.add_subplot(gs[2, 1])
        monthly = p.resample("ME").last().pct_change().dropna() * 100

        if len(monthly) >= 3:
            mdf = pd.DataFrame({
                "Y": monthly.index.year,
                "M": monthly.index.month,
                "R": monthly.values,
            })
            pivot = mdf.pivot_table(values="R", index="Y", columns="M", aggfunc="sum")
            month_labels = [
                "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
            ]
            pivot.columns = [month_labels[m - 1] for m in pivot.columns]

            vals = pivot.values[~np.isnan(pivot.values)]
            vmax = max(abs(vals).max(), 0.5) if len(vals) > 0 else 5.0

            im = ax5.imshow(pivot.values, cmap="RdYlGn", aspect="auto",
                            vmin=-vmax, vmax=vmax)
            ax5.set_xticks(range(len(pivot.columns)))
            ax5.set_xticklabels(pivot.columns, fontsize=8)
            ax5.set_yticks(range(len(pivot.index)))
            ax5.set_yticklabels(pivot.index.astype(str), fontsize=8)
            ax5.set_title("Monthly Returns Heatmap (%)", fontsize=12)

            for yi in range(len(pivot.index)):
                for xi in range(len(pivot.columns)):
                    val = pivot.iloc[yi, xi]
                    if not np.isnan(val):
                        text_color = "white" if abs(val) > vmax * 0.55 else "black"
                        ax5.text(xi, yi, f"{val:.1f}", ha="center", va="center",
                                 fontsize=7.5, color=text_color, fontweight="bold")

            plt.colorbar(im, ax=ax5, shrink=0.9, label="Return (%)")

        fig.suptitle(
            f"US Stock Backtest — Equal-Weight Fundamental + Momentum Strategy\n"
            f"{title_info}",
            fontsize=13, fontweight="bold", y=1.01,
        )

        fig.savefig(output, dpi=150, bbox_inches="tight",
                    facecolor="white", edgecolor="none")
        print(f"\n  Chart saved: {output}")
        plt.close(fig)


# ============================================================
# CLI
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="US Stock Backtesting Tool — Fundamental + Momentum",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--start", default="2020-01-01",
        help="Backtest start date YYYY-MM-DD  (default: 2020-01-01)",
    )
    parser.add_argument(
        "--end", default=datetime.now().strftime("%Y-%m-%d"),
        help="Backtest end date YYYY-MM-DD  (default: today)",
    )
    parser.add_argument(
        "--capital", type=float, default=1_000_000,
        help="Initial capital in USD  (default: 1000000)",
    )
    parser.add_argument(
        "--rebalance", default="monthly",
        choices=["weekly", "monthly", "quarterly"],
        help="Rebalancing frequency  (default: monthly)",
    )
    parser.add_argument(
        "--commission", type=float, default=0.001,
        help="Round-trip commission rate  (default: 0.001 = 0.1%%)",
    )
    parser.add_argument(
        "--benchmark", default="SPY",
        help="Benchmark ticker  (default: SPY)",
    )
    parser.add_argument(
        "--tickers", nargs="+",
        help="Custom universe — bypasses Finviz screening",
    )
    parser.add_argument(
        "--ticker-file",
        help="File with one ticker per line — bypasses Finviz screening",
    )
    parser.add_argument(
        "--list-stocks", action="store_true",
        help="Screen stocks only; do NOT run backtest",
    )
    parser.add_argument(
        "--output", default="backtest_results.png",
        help="Output chart file  (default: backtest_results.png)",
    )
    parser.add_argument(
        "--save-trades", default="trades.csv",
        help="CSV file for trade log  (default: trades.csv)",
    )
    return parser.parse_args()


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    args = parse_args()

    print("=" * 62)
    print("  US STOCK BACKTESTING TOOL")
    print("=" * 62)
    print(SCREENING_SUMMARY)

    cfg = {
        "start_date": args.start,
        "end_date": args.end,
        "initial_capital": args.capital,
        "rebalance_frequency": args.rebalance,
        "commission_rate": args.commission,
        "benchmark": args.benchmark,
        "min_price": 10.0,
    }

    # ── 1. Build Stock Universe ──────────────────────────────────
    universe: List[str] = []

    if args.tickers:
        universe = [t.upper().strip() for t in args.tickers]
        print(f"Universe: {len(universe)} manually specified tickers")

    elif args.ticker_file:
        with open(args.ticker_file) as fh:
            universe = [ln.strip().upper() for ln in fh if ln.strip()]
        print(f"Universe: {len(universe)} tickers loaded from {args.ticker_file}")

    else:
        try:
            screener = FinvizScreener()
            universe = screener.screen()
        except Exception as exc:
            print(f"\nFinviz screening failed: {exc}")
            universe = []

        if not universe:
            print(
                "\n  Finviz returned 0 results (blocked or no matches).\n"
                "  Auto-switching to built-in fallback universe for demonstration.\n"
                "  To use your own tickers: python stock_backtest.py --tickers AAPL MSFT ...\n"
                f"  Finviz screener URL:\n"
                f"    {FinvizScreener()._build_url()}\n"
            )
            universe = FALLBACK_TICKERS

    if not universe:
        print("No tickers available. Exiting.")
        sys.exit(1)

    # Print universe
    if len(universe) <= 30:
        print(f"  Tickers: {', '.join(universe)}")
    else:
        print(f"  First 30: {', '.join(universe[:30])} ...")

    # --list-stocks mode: just show the screen results
    if args.list_stocks:
        print("\nScreening complete (--list-stocks mode, no backtest run).")
        print("Qualifying tickers:")
        for i, t in enumerate(universe, 1):
            print(f"  {i:3d}. {t}")
        return

    # ── 2. Download Price Data ───────────────────────────────────
    pd_mgr = PriceData(args.start, args.end, args.commission)
    price_data = pd_mgr.load(universe)

    if not price_data:
        print("Failed to load any price data. Exiting.")
        sys.exit(1)

    valid_universe = list(price_data.keys())
    dropped = set(universe) - set(valid_universe)
    if dropped:
        print(f"  Dropped {len(dropped)} tickers (insufficient history): "
              f"{sorted(dropped)[:10]}{'...' if len(dropped)>10 else ''}")

    # ── 3. Download Benchmark ────────────────────────────────────
    print(f"\nFetching benchmark data ({args.benchmark}) ...")
    bench_df: Optional[pd.DataFrame] = pd_mgr.fetch_benchmark(args.benchmark)
    if bench_df is None or bench_df.empty:
        print(f"  Warning: {args.benchmark} data unavailable.")
        bench_df = None
    else:
        print(f"  {args.benchmark}: {len(bench_df)} trading days loaded.")

    # ── 4. Run Backtest ──────────────────────────────────────────
    bt = Backtester(valid_universe, pd_mgr, cfg)
    results = bt.run()

    # ── 5. Analyze & Report ──────────────────────────────────────
    analyzer = Analyzer(args.benchmark)
    perf = analyzer.metrics(results["nav"], bench_df)

    title_info = (
        f"{args.start} → {args.end}  |  "
        f"{args.rebalance.capitalize()} rebalance  |  "
        f"{len(valid_universe)} stocks  |  "
        f"${args.capital:,.0f} initial capital"
    )

    analyzer.print_report(perf, results["rebalance_log"], results["trades"])

    # ── 6. Save Outputs ──────────────────────────────────────────
    # Chart
    analyzer.plot(
        results["nav"],
        bench_df,
        results["rebalance_log"],
        output=args.output,
        title_info=title_info,
    )

    # Trade log CSV
    if not results["trades"].empty:
        results["trades"].to_csv(args.save_trades, index=False)
        print(f"  Trade log saved : {args.save_trades}")

    # NAV history CSV
    nav_file = "nav_history.csv"
    results["nav"].to_csv(nav_file)
    print(f"  NAV history saved: {nav_file}")

    # Rebalance log CSV
    if results["rebalance_log"]:
        rl_flat = [
            {k: v for k, v in row.items() if k != "tickers"}
            for row in results["rebalance_log"]
        ]
        pd.DataFrame(rl_flat).to_csv("rebalance_log.csv", index=False)
        print(f"  Rebalance log  : rebalance_log.csv")

    print(f"\n{'='*62}")
    print("  BACKTEST COMPLETE")
    print(f"{'='*62}")
    print("""
  IMPORTANT NOTES:
  ─────────────────────────────────────────────────────────
  1. Fundamental screening uses Finviz's CURRENT data.
     This introduces look-ahead bias for historical tests.
  2. SMA50 / SMA200 filters are applied historically at
     each rebalancing date (no look-ahead).
  3. For a true walk-forward test, historical fundamental
     data (paid service) would be required.
  4. Equal capital is allocated per stock at each rebalance.
  ─────────────────────────────────────────────────────────
""")


if __name__ == "__main__":
    main()
