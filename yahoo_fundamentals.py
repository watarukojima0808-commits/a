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
MODULES = (
    "financialData,defaultKeyStatistics,calendarEvents,summaryDetail,"
    "earningsTrend,earningsHistory,earnings"
)
SUMMARY_URL = (
    "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
    "?modules={modules}&crumb={crumb}"
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
            raw = _open(opener, SUMMARY_URL.format(symbol=f"{code}.T", modules=MODULES, crumb=crumb))
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

    # アナリストの通期コンセンサス。0y = 今期、+1y = 来期。
    # 日本株は Yahoo 側で growth が計算されていないため、EPS予想から自分で出す。
    trend = {t.get("period"): t for t in ((r.get("earningsTrend") or {}).get("trend") or [])}

    def est(period: str, node: str) -> float | None:
        t = trend.get(period) or {}
        return _raw(t.get(node) or {}, "avg")

    eps_now, eps_next = est("0y", "earningsEstimate"), est("+1y", "earningsEstimate")
    rev_now, rev_next = est("0y", "revenueEstimate"), est("+1y", "revenueEstimate")
    analysts_fy = _raw((trend.get("+1y") or {}).get("earningsEstimate") or {}, "numberOfAnalysts")

    def growth(now: float | None, nxt: float | None) -> float | None:
        # 赤字予想からの回復は率にすると桁が壊れるため、プラス同士のときだけ出す。
        if now is None or nxt is None or now <= 0:
            return None
        return nxt / now - 1

    # 前回決算がコンセンサスに対してどうだったか。
    history = (r.get("earningsHistory") or {}).get("history") or []
    surprises = [
        {
            "quarter": (h.get("quarter") or {}).get("fmt"),
            "actual": _raw(h, "epsActual"),
            "estimate": _raw(h, "epsEstimate"),
            "surprise": _raw(h, "surprisePercent"),
        }
        for h in history
    ]
    last = surprises[-1] if surprises else None
    beats = sum(1 for s in surprises if (s["surprise"] or 0) > 0)

    yearly = [
        {
            "year": y.get("date"),
            "revenue": _raw(y, "revenue"),
            "earnings": _raw(y, "earnings"),
        }
        for y in ((r.get("earnings") or {}).get("financialsChart") or {}).get("yearly", [])
    ]

    return {
        "eps_now": eps_now,
        "eps_next": eps_next,
        "eps_growth": growth(eps_now, eps_next),
        "revenue_next_growth": growth(rev_now, rev_next),
        "fy_now_end": (trend.get("0y") or {}).get("endDate"),
        "fy_next_end": (trend.get("+1y") or {}).get("endDate"),
        "analysts_fy": analysts_fy,
        "last_quarter": last["quarter"] if last else None,
        "last_actual": last["actual"] if last else None,
        "last_estimate": last["estimate"] if last else None,
        "last_surprise": last["surprise"] if last else None,
        "beats": beats,
        "quarters": len(surprises),
        "yearly": yearly,
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


def guidance_adjust(f: dict | None) -> float:
    """来期のコンセンサスEPSが今期比でどうかを、波形スコアへの調整値にする。"""
    g = (f or {}).get("eps_growth")
    if g is None:
        return 0.0
    if g >= 0.10:
        return 5.0
    if g >= 0:
        return 2.0
    if g > -0.10:
        return -3.0
    return -6.0


def guidance_text(f: dict | None) -> str:
    """「+14.8%」形式。予想が取れない場合は「—」。"""
    g = (f or {}).get("eps_growth")
    return "—" if g is None else f"{g * 100:+.1f}%"


def surprise_text(f: dict | None) -> str:
    """前回決算がコンセンサスに対して上振れたか下振れたか。"""
    if not f or f.get("last_surprise") is None:
        return "—"
    s = f["last_surprise"] * 100
    word = "上振れ" if s > 0 else "下振れ"
    return f"{f['last_quarter']} {word} {s:+.1f}%"


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
