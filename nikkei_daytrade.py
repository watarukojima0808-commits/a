#!/usr/bin/env python3
"""
日経225 デイトレード銘柄スクリーナー

前営業日までの日足データから、当日デイトレードに向いた銘柄をスコア順に抽出します。
寄り付き前 (8:45頃) の実行を想定しています。

Usage: python nikkei_daytrade.py
       python nikkei_daytrade.py --top 15 --out-dir reports
       python nikkei_daytrade.py --update-list
"""

import os
import ssl
import sys
import csv
import json
import time
import gzip
import random
import smtplib
import argparse
import urllib.error
import urllib.request
from io import StringIO
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

CHART_API = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.T?range=6mo&interval=1d"
NIKKEI_COMPONENTS = "https://indexes.nikkei.co.jp/nkave/index/component?idx=nk225"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip",
}

DEFAULT_LIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nikkei225.csv")
JST = timezone(timedelta(hours=9))


# --------------------------------------------------------------------------
# 通信
# --------------------------------------------------------------------------

def http_get(url: str, timeout: int = 20, retries: int = 3) -> bytes:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except (urllib.error.URLError, ssl.SSLError, TimeoutError) as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt + random.random())
    raise RuntimeError(f"取得失敗 {url}: {last_error}")


# --------------------------------------------------------------------------
# 銘柄リスト
# --------------------------------------------------------------------------

def load_codes(path: str) -> list[tuple[str, str]]:
    if not os.path.exists(path):
        sys.exit(
            f"銘柄リストが見つかりません: {path}\n"
            "--update-list で日経公式から取得するか、--codes-file で code,name 形式のCSVを指定してください。"
        )
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    codes = [(r[0].strip(), r[1].strip() if len(r) > 1 else r[0].strip())
             for r in rows if r and r[0].strip().isdigit()]
    if not codes:
        sys.exit(f"銘柄リストが空です: {path}")
    return codes


def update_list(path: str) -> None:
    """日経公式の構成銘柄ページから code,name を取り直して保存する。"""
    import re
    import html

    print("日経公式から構成銘柄を取得中...")
    page = http_get(NIKKEI_COMPONENTS).decode("utf-8", errors="replace")

    # 構成銘柄テーブルは「コード → 銘柄名」の順にセルが並ぶ
    pattern = re.compile(
        r'component-list__code[^>]*>\s*(\d{4})\s*<.*?component-list__name[^>]*>\s*(?:<[^>]+>\s*)*([^<]+)',
        re.S,
    )
    found = [(c, html.unescape(n).strip()) for c, n in pattern.findall(page)]

    if len(found) < 200:
        # マークアップ変更に備えたフォールバック: 銘柄ページへのリンクからコードだけ拾う
        codes = re.findall(r'pcode=(\d{4})', page)
        seen: dict[str, str] = {}
        for c in codes:
            seen.setdefault(c, c)
        found = sorted(seen.items())

    if len(found) < 200:
        sys.exit(
            f"構成銘柄を {len(found)} 件しか取得できませんでした。"
            "ページ構造が変わった可能性があります。--codes-file で手動指定してください。"
        )

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows(found)
    print(f"{len(found)} 銘柄を {path} に保存しました。")


# --------------------------------------------------------------------------
# 株価取得
# --------------------------------------------------------------------------

class Bar:
    __slots__ = ("date", "o", "h", "l", "c", "v")

    def __init__(self, date, o, h, l, c, v):
        self.date, self.o, self.h, self.l, self.c, self.v = date, o, h, l, c, v


def fetch_bars(code: str) -> list[Bar]:
    data = json.loads(http_get(CHART_API.format(symbol=code)))
    result = data.get("chart", {}).get("result")
    if not result:
        return []

    res = result[0]
    stamps = res.get("timestamp") or []
    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
    opens, highs = quote.get("open", []), quote.get("high", [])
    lows, closes, vols = quote.get("low", []), quote.get("close", []), quote.get("volume", [])

    bars = []
    for i, ts in enumerate(stamps):
        o, h, l, c, v = opens[i], highs[i], lows[i], closes[i], vols[i]
        if None in (o, h, l, c) or not v:
            continue
        bars.append(Bar(datetime.fromtimestamp(ts, JST).date(), o, h, l, c, v))
    return bars


# --------------------------------------------------------------------------
# 指標計算
# --------------------------------------------------------------------------

def average_true_range(bars: list[Bar], period: int = 14) -> float:
    trs = []
    for prev, cur in zip(bars[-period - 1:-1], bars[-period:]):
        trs.append(max(cur.h - cur.l, abs(cur.h - prev.c), abs(cur.l - prev.c)))
    return sum(trs) / len(trs) if trs else 0.0


def analyze(code: str, name: str, bars: list[Bar]) -> dict | None:
    if len(bars) < 30:
        return None

    last = bars[-1]
    atr = average_true_range(bars)
    if last.c <= 0 or atr <= 0:
        return None

    vol_base = [b.v for b in bars[-21:-1]]
    avg_vol = sum(vol_base) / len(vol_base)
    turnover = sum(b.c * b.v for b in bars[-20:]) / 20

    recent = bars[-20:]
    ma25 = sum(b.c for b in bars[-25:]) / 25

    return {
        "code": code,
        "name": name,
        "date": last.date,
        "close": last.c,
        "high": last.h,
        "low": last.l,
        "volume": last.v,
        "atr": atr,
        "atr_pct": atr / last.c * 100,
        "range_pct": (last.h - last.l) / last.c * 100,
        "vol_ratio": last.v / avg_vol if avg_vol else 0.0,
        "turnover": turnover,
        "ret1": (last.c / bars[-2].c - 1) * 100,
        "ret5": (last.c / bars[-6].c - 1) * 100,
        "ma25_dev": (last.c / ma25 - 1) * 100 if ma25 else 0.0,
        "high20": max(b.h for b in recent),
        "low20": min(b.l for b in recent),
    }


def score(m: dict) -> float:
    """デイトレ適性を100点満点で採点する。"""
    # 値幅がなければ利益機会がない。ATR 4% 以上で満点。
    volatility = min(m["atr_pct"] / 4.0, 1.0) * 40

    # 出来高急増は当日も値動きが続くサイン。平均の2.5倍で満点。
    surge = min(max(m["vol_ratio"] - 1.0, 0.0) / 1.5, 1.0) * 25

    # 売買代金が細いと約定が滑る。1日100億円で満点。
    liquidity = min(m["turnover"] / 10_000_000_000, 1.0) * 20

    # 方向は問わず、動いている銘柄を評価する。5日で8%で満点。
    momentum = min(abs(m["ret5"]) / 8.0, 1.0) * 15

    return volatility + surge + liquidity + momentum


def label(m: dict) -> str:
    tags = []
    if m["vol_ratio"] >= 2.0:
        tags.append("出来高急増")
    elif m["vol_ratio"] >= 1.5:
        tags.append("出来高増")
    if m["close"] >= m["high20"] * 0.995:
        tags.append("20日高値圏")
    elif m["close"] <= m["low20"] * 1.005:
        tags.append("20日安値圏")
    if m["ma25_dev"] >= 10:
        tags.append("25日線から上方乖離")
    elif m["ma25_dev"] <= -10:
        tags.append("25日線から下方乖離")
    if m["atr_pct"] >= 3.0:
        tags.append("高ボラ")
    return " / ".join(tags) if tags else "—"


# --------------------------------------------------------------------------
# 出力
# --------------------------------------------------------------------------

def format_report(picks: list[dict], stats: dict) -> str:
    now = datetime.now(JST)
    out = StringIO()
    w = out.write

    w(f"# 日経225 デイトレ候補 {now:%Y-%m-%d (%a)}\n\n")
    w(f"生成時刻: {now:%H:%M} JST / 参照データ: {stats['data_date']} 終値ベース\n\n")
    w(f"対象 {stats['analyzed']} 銘柄 → 条件通過 {stats['passed']} 銘柄 → 上位 {len(picks)} 銘柄を掲載\n\n")

    if not picks:
        w("条件を満たす銘柄がありませんでした。--min-atr や --min-turnover を緩めて再実行してください。\n")
        return out.getvalue()

    w("| # | コード | 銘柄 | 終値 | 前日比 | ATR(14) | 出来高比 | 売買代金 | スコア |\n")
    w("|---|--------|------|------|--------|---------|----------|----------|--------|\n")
    for i, m in enumerate(picks, 1):
        w(
            f"| {i} | {m['code']} | {m['name']} | {m['close']:,.0f} | {m['ret1']:+.1f}% | "
            f"{m['atr_pct']:.1f}% | {m['vol_ratio']:.1f}x | "
            f"{m['turnover'] / 100_000_000:,.0f}億 | {m['score']:.0f} |\n"
        )

    w("\n## 銘柄メモ\n\n")
    for i, m in enumerate(picks, 1):
        w(f"### {i}. {m['code']} {m['name']}  (スコア {m['score']:.0f})\n\n")
        w(f"- 特徴: {label(m)}\n")
        w(f"- 前日終値 {m['close']:,.0f}円 / 前日レンジ {m['low']:,.0f}〜{m['high']:,.0f}円 ({m['range_pct']:.1f}%)\n")
        w(f"- 想定変動幅 (ATR14): ±{m['atr']:,.0f}円\n")
        w(f"- 直近20日 高値 {m['high20']:,.0f}円 / 安値 {m['low20']:,.0f}円\n")
        w(f"- 5日騰落率 {m['ret5']:+.1f}% / 25日線乖離 {m['ma25_dev']:+.1f}%\n\n")

    w("---\n\n")
    w("本レポートは日足データからの機械的な抽出であり、投資判断を推奨するものではありません。\n")
    w("売買は自己責任で行ってください。\n")
    return out.getvalue()


def print_console(picks: list[dict], stats: dict) -> None:
    now = datetime.now(JST)
    print()
    print("=" * 78)
    print(f" 日経225 デイトレ候補  {now:%Y-%m-%d (%a) %H:%M} JST  ({stats['data_date']} 終値ベース)")
    print("=" * 78)

    if not picks:
        print("\n条件を満たす銘柄がありませんでした。--min-atr / --min-turnover を緩めてください。\n")
        return

    print(f"{'#':>2} {'コード':<6} {'銘柄':<14} {'終値':>9} {'前日比':>7} "
          f"{'ATR':>6} {'出来高比':>8} {'代金':>7} {'点':>4}")
    print("-" * 78)
    for i, m in enumerate(picks, 1):
        name = m["name"][:12]
        pad = " " * (14 - sum(2 if ord(ch) > 0x2E80 else 1 for ch in name))
        print(
            f"{i:>2} {m['code']:<6} {name}{pad}{m['close']:>9,.0f} {m['ret1']:>+6.1f}% "
            f"{m['atr_pct']:>5.1f}% {m['vol_ratio']:>7.1f}x {m['turnover'] / 100_000_000:>5,.0f}億 "
            f"{m['score']:>4.0f}"
        )
    print("-" * 78)
    for i, m in enumerate(picks, 1):
        print(f"{i:>2}. {m['code']} {m['name']}: {label(m)}")
    print()


def send_gmail(sender: str, to: str, password: str, subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        print("[Gmail] 送信完了")
    except Exception as e:
        print(f"[Gmail] 送信失敗: {e}")


# --------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    codes = load_codes(args.codes_file)
    print(f"{len(codes)} 銘柄の株価を取得中...")

    results: list[dict] = []
    failures = 0

    def work(item: tuple[str, str]) -> dict | None:
        code, name = item
        try:
            return analyze(code, name, fetch_bars(code))
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, m in enumerate(pool.map(work, codes), 1):
            if m is None:
                failures += 1
            else:
                results.append(m)
            if i % 25 == 0 or i == len(codes):
                print(f"  {i}/{len(codes)} 完了", end="\r", flush=True)
    print()

    if failures:
        print(f"[WARN] {failures} 銘柄はデータ取得できずスキップしました。")
    if not results:
        sys.exit("株価データを取得できませんでした。ネットワーク接続を確認してください。")

    passed = [
        m for m in results
        if m["atr_pct"] >= args.min_atr
        and m["turnover"] >= args.min_turnover * 100_000_000
        and m["close"] >= args.min_price
        and (args.max_price <= 0 or m["close"] <= args.max_price)
    ]
    for m in passed:
        m["score"] = score(m)
    passed.sort(key=lambda m: m["score"], reverse=True)
    picks = passed[: args.top]

    stats = {
        "analyzed": len(results),
        "passed": len(passed),
        "data_date": max(m["date"] for m in results).strftime("%Y-%m-%d"),
    }

    print_console(picks, stats)
    report = format_report(picks, stats)

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        path = os.path.join(args.out_dir, f"{datetime.now(JST):%Y-%m-%d}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"レポートを保存しました: {path}")

    if args.gmail_from:
        subject = f"【日経225 デイトレ候補】{datetime.now(JST):%m/%d} 上位{len(picks)}銘柄"
        send_gmail(args.gmail_from, args.gmail_to, args.gmail_password, subject, report)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="日経225 デイトレード銘柄スクリーナー",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  python nikkei_daytrade.py\n"
            "  python nikkei_daytrade.py --top 15 --min-atr 2.0 --out-dir reports\n"
            "  python nikkei_daytrade.py --update-list\n"
        ),
    )
    parser.add_argument("--top", type=int, default=10, metavar="件数", help="表示する上位銘柄数 (デフォルト: 10)")
    parser.add_argument("--min-atr", type=float, default=1.5, metavar="パーセント", help="ATR14の下限 (デフォルト: 1.5%%)")
    parser.add_argument("--min-turnover", type=float, default=20, metavar="億円", help="20日平均売買代金の下限 (デフォルト: 20億円)")
    parser.add_argument("--min-price", type=float, default=500, metavar="円", help="株価の下限 (デフォルト: 500円)")
    parser.add_argument("--max-price", type=float, default=0, metavar="円", help="株価の上限 (0で無制限)")
    parser.add_argument("--codes-file", default=DEFAULT_LIST, metavar="パス", help="銘柄リストCSV (code,name)")
    parser.add_argument("--out-dir", metavar="パス", help="Markdownレポートの保存先ディレクトリ")
    parser.add_argument("--workers", type=int, default=6, metavar="数", help="並列取得数 (デフォルト: 6)")
    parser.add_argument("--update-list", action="store_true", help="日経公式から構成銘柄リストを更新して終了")
    parser.add_argument("--gmail-from", metavar="アドレス", help="送信元Gmailアドレス")
    parser.add_argument("--gmail-to", metavar="アドレス", help="送信先メールアドレス")
    parser.add_argument("--gmail-password", metavar="パスワード", help="Gmailアプリパスワード (16桁)")
    args = parser.parse_args()

    if args.update_list:
        update_list(args.codes_file)
        return

    if args.gmail_from or args.gmail_to or args.gmail_password:
        if not all([args.gmail_from, args.gmail_to, args.gmail_password]):
            sys.exit("Gmail通知には --gmail-from / --gmail-to / --gmail-password をすべて指定してください。")

    try:
        run(args)
    except KeyboardInterrupt:
        print("\n中断しました。")


if __name__ == "__main__":
    main()
