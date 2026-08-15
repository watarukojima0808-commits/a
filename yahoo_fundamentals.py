#!/usr/bin/env python3
"""
Yahoo Finance の業績データ取得

株価チャートAPIと違い quoteSummary はクッキー + crumb を要求するため、
セッションを1回だけ確立して使い回します。取得できなかった場合は None を返し、
呼び出し側が業績なしで動作を続けられるようにしています。

Usage: python yahoo_fundamentals.py 9021 1925
"""

import sys
import gzip
import json
import time
import random
import threading
import http.cookiejar
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip",
}

QUOTE_PAGE = "https://finance.yahoo.com/quote/{symbol}"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
SUMMARY_URL = (
    "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
    "?modules=financialData,defaultKeyStatistics,calendarEvents,summaryDetail&crumb={crumb}"
)

_lock = threading.Lock()
_session: tuple[urllib.request.OpenerDirector, str] | None = None
_failed = False


def _open(opener, url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw


def session() -> tuple[urllib.request.OpenerDirector, str] | None:
    """クッキーと crumb を確立する。一度失敗したら以降は諦める（毎回待たされないため）。"""
    global _session, _failed
    with _lock:
        if _session is not None or _failed:
            return _session
        try:
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
            )
            _open(opener, QUOTE_PAGE.format(symbol="7203.T"))
            crumb = _open(opener, CRUMB_URL).decode("utf-8", errors="replace").strip()
            if not crumb or len(crumb) > 32 or "<" in crumb:
                raise RuntimeError(f"crumb を取得できません: {crumb[:40]!r}")
            _session = (opener, crumb)
        except Exception as e:
            print(f"[WARN] 業績データのセッションを確立できませんでした ({e})。業績なしで続行します。")
            _failed = True
        return _session


def _raw(node: dict, key: str):
    v = node.get(key)
    if isinstance(v, dict):
        return v.get("raw")
    return v


def fetch(code: str, retries: int = 2) -> dict | None:
    """4桁コードの業績データを取る。取得できなければ None。"""
    sess = session()
    if sess is None:
        return None
    opener, crumb = sess

    for attempt in range(retries):
        try:
            raw = _open(opener, SUMMARY_URL.format(symbol=f"{code}.T", crumb=crumb))
            break
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                return None
            time.sleep(1 + random.random())
    else:
        return None

    try:
        result = json.loads(raw)["quoteSummary"]["result"]
    except (ValueError, KeyError, TypeError):
        return None
    if not result:
        return None

    r = result[0]
    fin = r.get("financialData") or {}
    key = r.get("defaultKeyStatistics") or {}
    det = r.get("summaryDetail") or {}
    cal = r.get("calendarEvents") or {}

    dates = ((cal.get("earnings") or {}).get("earningsDate")) or []
    earnings_date = None
    for d in dates:
        ts = d.get("raw") if isinstance(d, dict) else None
        if ts:
            earnings_date = datetime.fromtimestamp(ts, JST).date()
            break

    return {
        "revenue_growth": _raw(fin, "revenueGrowth"),
        "earnings_growth": _raw(fin, "earningsGrowth"),
        "operating_margin": _raw(fin, "operatingMargins"),
        "profit_margin": _raw(fin, "profitMargins"),
        "roe": _raw(fin, "returnOnEquity"),
        "recommendation": fin.get("recommendationKey"),
        "analysts": _raw(fin, "numberOfAnalystOpinions"),
        "target_mean": _raw(fin, "targetMeanPrice"),
        "per": _raw(det, "trailingPE"),
        "forward_per": _raw(key, "forwardPE"),
        "pbr": _raw(key, "priceToBook"),
        "dividend_yield": _raw(det, "dividendYield"),
        "earnings_date": earnings_date,
    }


# --------------------------------------------------------------------------
# 評価
# --------------------------------------------------------------------------

# 業績の良し悪しは波形の良し悪しとは別物なので、波形スコアに対する調整値として扱う。
GRADE_ADJUST = {"◎": 8.0, "○": 3.0, "△": -3.0, "✕": -12.0, "?": 0.0}

GRADE_TEXT = {
    "◎": "増収増益",
    "○": "増益",
    "△": "減速",
    "✕": "減収減益",
    "?": "データなし",
}


def grade(f: dict | None) -> str:
    """直近四半期の前年同期比から、増益か減益かをざっくり格付けする。"""
    if not f:
        return "?"
    rev, earn = f.get("revenue_growth"), f.get("earnings_growth")
    if rev is None and earn is None:
        return "?"
    rev = rev if rev is not None else 0.0
    earn = earn if earn is not None else 0.0

    if earn <= -0.10 or (rev < 0 and earn < 0):
        return "✕"
    if earn < 0 or rev < 0:
        return "△"
    if earn >= 0.10:
        return "◎"
    return "○"


def summary(f: dict | None) -> str:
    """「◎ 増収増益 (売上 +10.2% / 利益 +38.0%)」の形にまとめる。"""
    g = grade(f)
    if g == "?" or not f:
        return "? データなし"

    text = GRADE_TEXT[g]
    if f.get("earnings_growth") is None and f.get("revenue_growth") is not None:
        # 利益成長が取れていない銘柄で「増益」と書くと誤解を招く。
        text = "増収" if f["revenue_growth"] > 0 else "減収"

    parts = []
    if f.get("revenue_growth") is not None:
        parts.append(f"売上 {f['revenue_growth'] * 100:+.1f}%")
    if f.get("earnings_growth") is not None:
        parts.append(f"利益 {f['earnings_growth'] * 100:+.1f}%")
    detail = f" ({' / '.join(parts)})" if parts else ""
    return f"{g} {text}{detail}"


def main() -> None:
    codes = sys.argv[1:]
    if not codes:
        sys.exit("Usage: python yahoo_fundamentals.py <証券コード> [...]")
    for code in codes:
        f = fetch(code)
        print(f"--- {code}: {summary(f)}")
        if f:
            for k, v in f.items():
                print(f"    {k:<18} {v}")


if __name__ == "__main__":
    main()
