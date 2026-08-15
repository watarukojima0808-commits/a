#!/usr/bin/env python3
"""
エリオット波動 3波入り候補スクリーナー

日足のジグザグ（転換点）から「1波の上昇 → 2波の押し」を検出し、
2波が終わって3波に移行しようとしている銘柄をスコア順に抽出します。
大引け後（16:00以降）の実行を想定しています。

Usage: python elliott_wave3.py
       python elliott_wave3.py --top 15 --out-dir reports/elliott
"""

import os
import sys
import json
import argparse
from io import StringIO
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from nikkei_daytrade import (
    JST,
    DEFAULT_LIST,
    Bar,
    average_true_range,
    fetch_bars,
    load_codes,
    send_gmail,
)

# 波動判定には1波の起点より前の値動きも要る。2年分あれば日足の中期波動は十分に入る。
FETCH_RANGE = "2y"
MIN_BARS = 120


# --------------------------------------------------------------------------
# 転換点 (ジグザグ)
# --------------------------------------------------------------------------

class Pivot:
    __slots__ = ("idx", "price", "kind", "confirmed")

    def __init__(self, idx: int, price: float, kind: str, confirmed: bool = True):
        self.idx, self.price, self.kind, self.confirmed = idx, price, kind, confirmed


def zigzag(bars: list[Bar], pct: float) -> list[Pivot]:
    """高値・安値ベースのジグザグ。pct (0.05 = 5%) 以上の逆行で転換とみなす。

    末尾には未確定の極値も 1 本追加する。2波の底は「まだ pct 分も戻していない」
    段階で拾いたいので、確定した転換点だけでは遅すぎるため。
    """
    if len(bars) < 3:
        return []

    pivots: list[Pivot] = []
    direction = 0                      # 1: 上昇中 / -1: 下降中 / 0: 未定
    hi, hi_i = bars[0].h, 0
    lo, lo_i = bars[0].l, 0

    for i, b in enumerate(bars):
        if direction >= 0 and b.h > hi:
            hi, hi_i = b.h, i
        if direction <= 0 and b.l < lo:
            lo, lo_i = b.l, i

        if direction >= 0 and b.l <= hi * (1 - pct):
            pivots.append(Pivot(hi_i, hi, "H"))
            direction = -1
            lo, lo_i = b.l, i
        elif direction <= 0 and b.h >= lo * (1 + pct):
            pivots.append(Pivot(lo_i, lo, "L"))
            direction = 1
            hi, hi_i = b.h, i

    if direction == 1:
        pivots.append(Pivot(hi_i, hi, "H", confirmed=False))
    elif direction == -1:
        pivots.append(Pivot(lo_i, lo, "L", confirmed=False))
    return pivots


# --------------------------------------------------------------------------
# 指標
# --------------------------------------------------------------------------

def sma(bars: list[Bar], period: int) -> float:
    window = bars[-period:]
    return sum(b.c for b in window) / len(window) if window else 0.0


def rsi(bars: list[Bar], period: int = 14) -> float:
    if len(bars) < period + 1:
        return 50.0
    gains = losses = 0.0
    for prev, cur in zip(bars[-period - 1:-1], bars[-period:]):
        diff = cur.c - prev.c
        gains += max(diff, 0.0)
        losses += max(-diff, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100 - 100 / (1 + rs)


def avg_volume(bars: list[Bar], start: int, end: int) -> float:
    """bars[start+1 .. end] の平均出来高。区間が空なら 0。"""
    window = bars[start + 1:end + 1]
    return sum(b.v for b in window) / len(window) if window else 0.0


# --------------------------------------------------------------------------
# 波動の検出
# --------------------------------------------------------------------------

def find_wave(bars: list[Bar], pct: float) -> tuple[Pivot, Pivot, Pivot] | None:
    """直近の 安値 → 高値 → 安値 (= 1波の起点, 1波の頂点, 2波の底) を返す。"""
    seq = zigzag(bars, pct)

    # 末尾が未確定の高値なら、それは3波の初動そのものとみなして外し、
    # その手前の「安値 = 2波の底」を評価対象にする。
    if seq and seq[-1].kind == "H" and not seq[-1].confirmed:
        seq = seq[:-1]
    if len(seq) < 3:
        return None

    l2, h1, l0 = seq[-1], seq[-2], seq[-3]
    if (l2.kind, h1.kind, l0.kind) != ("L", "H", "L"):
        return None
    return l0, h1, l2


def cross_check(bars: list[Bar], h1: Pivot, l2: Pivot, pct: float) -> bool:
    """別の閾値でも同じ波形が見えるか。ノイズを1波2波と読んでいないかの確認。

    チャートツールのエリオット指標は 4% 前後の固定閾値が多く、そちらでも同じ底が
    出るなら、閾値の取り方に依存しない波形といえる。
    """
    found = find_wave(bars, pct)
    if found is None:
        return False
    _, h1b, l2b = found
    return abs(l2b.idx - l2.idx) <= 2 and abs(h1b.idx - h1.idx) <= 3


def detect(code: str, name: str, bars: list[Bar], args: argparse.Namespace) -> dict | None:
    if len(bars) < MIN_BARS:
        return None

    last = bars[-1]
    atr = average_true_range(bars)
    if last.c <= 0 or atr <= 0:
        return None

    atr_pct = atr / last.c * 100
    turnover = sum(b.c * b.v for b in bars[-20:]) / 20

    if last.c < args.min_price or (args.max_price > 0 and last.c > args.max_price):
        return None
    if turnover < args.min_turnover * 100_000_000:
        return None

    # 値動きの荒い銘柄ほど、ノイズを転換点と誤認しないよう閾値を広げる。
    pct = min(max(args.swing, atr_pct * args.swing_atr) / 100, 0.12)
    found = find_wave(bars, pct)
    if found is None:
        return None
    l0, h1, l2 = found

    wave1 = h1.price - l0.price
    wave2 = h1.price - l2.price
    if wave1 <= 0 or wave2 <= 0:
        return None

    # エリオットの絶対ルール: 2波は1波の起点を割らない。割ったらこの数え方は無効。
    if l2.price <= l0.price:
        return None

    retrace = wave2 / wave1
    wave1_pct = wave1 / l0.price * 100
    days_w1 = h1.idx - l0.idx
    days_w2 = l2.idx - h1.idx
    days_since = (len(bars) - 1) - l2.idx

    if wave1_pct < args.min_wave1:
        return None
    if days_w1 < 3 or days_w2 < 2:
        return None
    if not args.min_retrace <= retrace <= args.max_retrace:
        return None
    if not 1 <= days_since <= args.max_age:
        return None

    # 底からは反発済み、ただし1波高値はまだ抜いていない = 「3波に入ろうとしている」状態。
    if last.c <= l2.price:
        return None
    recovery = (last.c - l2.price) / wave2
    if last.c > h1.price and not args.allow_breakout:
        return None
    if recovery > args.max_recovery and not args.allow_breakout:
        return None

    vol_w1 = avg_volume(bars, l0.idx, h1.idx)
    vol_w2 = avg_volume(bars, h1.idx, l2.idx)
    vol_reb = avg_volume(bars, l2.idx, len(bars) - 1)
    ret3 = (last.c / bars[-4].c - 1) * 100 if len(bars) >= 4 else 0.0

    base_low = min(b.l for b in bars[max(0, l0.idx - 60):l0.idx + 1])

    m = {
        "code": code,
        "name": name,
        "date": last.date,
        "close": last.c,
        "atr": atr,
        "atr_pct": atr_pct,
        "turnover": turnover,
        "swing_pct": pct * 100,
        "cross_pct": args.cross_swing,
        "cross_agree": (
            cross_check(bars, h1, l2, args.cross_swing / 100)
            if args.cross_swing > 0 else None
        ),
        "l0_date": bars[l0.idx].date,
        "l0": l0.price,
        "h1_date": bars[h1.idx].date,
        "h1": h1.price,
        "l2_date": bars[l2.idx].date,
        "l2": l2.price,
        "l2_confirmed": l2.confirmed,
        "wave1_pct": wave1_pct,
        "wave1_atr": wave1 / atr,
        "wave2_pct": wave2 / h1.price * 100,
        "retrace": retrace,
        "recovery": recovery,
        "days_w1": days_w1,
        "days_w2": days_w2,
        "days_since": days_since,
        "rebound_pct": (last.c / l2.price - 1) * 100,
        "vol_w1": vol_w1,
        "vol_w2": vol_w2,
        "vol_reb": vol_reb,
        "vol_contraction": vol_w2 / vol_w1 if vol_w1 else 0.0,
        "vol_rebound": vol_reb / vol_w2 if vol_w2 else 0.0,
        "ret3": ret3,
        "rsi": rsi(bars),
        "sma5": sma(bars, 5),
        "sma25": sma(bars, 25),
        "sma75": sma(bars, 75),
        "fresh_base": l0.price <= base_low * 1.02,
        "target100": l2.price + wave1,
        "target162": l2.price + wave1 * 1.618,
        # 底ちょうどに損切りを置くとヒゲで刈られる。現実的に置ける位置まで下げてリスクを測る。
        "stop": l2.price - atr * 0.3,
    }

    risk = last.c - m["stop"]
    m["risk_pct"] = risk / last.c * 100
    m["rr"] = min((m["target162"] - last.c) / risk, 99.0) if risk > 0 else 0.0
    if m["rr"] < args.min_rr:
        return None
    return m


def score(m: dict) -> float:
    """3波入りの確度を100点満点で採点する。"""
    # 押しの深さ (25点)。0.5〜0.618 が最も2波らしい。浅すぎ・深すぎは減点。
    r = m["retrace"]
    if 0.5 <= r <= 0.618:
        fib = 25.0
    elif r < 0.5:
        fib = max(0.0, 25 - (0.5 - r) * 50)
    else:
        fib = max(0.0, 25 - (r - 0.618) * 58)

    # 2波での出来高減少 (18点)。押し目で薄商いなら売り圧力が枯れている。
    contraction = m["vol_contraction"]
    dry = min(max(1.1 - contraction, 0.0) / 0.5, 1.0) * 18 if contraction else 0.0

    # 反発の勢い (20点)。5日線回復・出来高増加・直近3日の上昇。
    momentum = 0.0
    if m["close"] > m["sma5"]:
        momentum += 7
    momentum += min(max(m["vol_rebound"] - 0.9, 0.0) / 0.5, 1.0) * 7
    momentum += min(max(m["ret3"], 0.0) / 3.0, 1.0) * 6

    # 1波の力強さ (15点)。値幅と、ATR何個分動いたか。
    impulse = min(m["wave1_pct"] / 20.0, 1.0) * 8 + min(m["wave1_atr"] / 12.0, 1.0) * 7

    # 地合い (12点)。中期が上向きで、1波が底から出ているか。
    trend = 0.0
    if m["sma25"] > m["sma75"]:
        trend += 5
    if m["close"] > m["sma75"]:
        trend += 4
    if m["fresh_base"]:
        trend += 3

    base = fib + dry + momentum + impulse + trend
    if m["cross_agree"] is None:
        # 照合を切っている場合は90点満点になるため、100点満点に伸ばし直す。
        return base * 100 / 90

    # 波形の頑健性 (10点)。閾値を変えても同じ2波が見えるか。
    return base + (10.0 if m["cross_agree"] else 0.0)


def cross_mark(m: dict) -> str:
    """照合結果の表示。照合を切っている場合は判定なしを示す。"""
    if m["cross_agree"] is None:
        return "…"
    return "○" if m["cross_agree"] else "−"


def label(m: dict) -> str:
    tags = []
    if m["cross_agree"]:
        tags.append(f"{m['cross_pct']:.0f}%閾値でも同じ底")
    r = m["retrace"]
    if r < 0.382:
        tags.append("浅い押し")
    elif r <= 0.618:
        tags.append("理想的な押し")
    elif r <= 0.786:
        tags.append("やや深い押し")
    else:
        tags.append("深い押し")

    if m["vol_contraction"] and m["vol_contraction"] <= 0.8:
        tags.append("2波で出来高減少")
    if m["vol_rebound"] >= 1.3:
        tags.append("反発で出来高増")
    if m["close"] > m["sma5"]:
        tags.append("5日線回復")
    if m["close"] > m["h1"]:
        tags.append("1波高値を更新済み")
    elif m["close"] >= m["h1"] * 0.97:
        tags.append("1波高値ブレイク間近")
    if m["rsi"] >= 50:
        tags.append("RSI50回復")
    if not m["l2_confirmed"]:
        tags.append("底は未確定")
    return " / ".join(tags)


# --------------------------------------------------------------------------
# 出力
# --------------------------------------------------------------------------

def print_console(picks: list[dict], stats: dict) -> None:
    now = datetime.now(JST)
    print()
    print("=" * 96)
    print(f" エリオット3波入り候補  {now:%Y-%m-%d (%a) %H:%M} JST  ({stats['data_date']} 終値ベース)")
    print("=" * 96)

    if not picks:
        print("\n条件を満たす銘柄がありませんでした。--min-wave1 を下げるか --max-age を広げてください。\n")
        return

    print(f"{'#':>2} {'コード':<6} {'銘柄':<14} {'終値':>9} {'1波':>7} {'押し':>6} "
          f"{'反発':>6} {'経過':>5} {'RR':>5} {'照合':>4} {'底':>6} {'点':>4}")
    print("-" * 96)
    for i, m in enumerate(picks, 1):
        name = m["name"][:12]
        pad = " " * max(1, 14 - sum(2 if ord(ch) > 0x2E80 else 1 for ch in name))
        print(
            f"{i:>2} {m['code']:<6} {name}{pad}{m['close']:>9,.0f} {m['wave1_pct']:>+6.1f}% "
            f"{m['retrace'] * 100:>5.0f}% {m['rebound_pct']:>+5.1f}% {m['days_since']:>4}日 "
            f"{m['rr']:>4.1f} {cross_mark(m):>3} "
            f"{'確定' if m['l2_confirmed'] else '未確定':>4} {m['score']:>4.0f}"
        )
    print("-" * 96)
    for i, m in enumerate(picks, 1):
        print(f"{i:>2}. {m['code']} {m['name']}: {label(m)}")
    print()


def format_report(picks: list[dict], stats: dict) -> str:
    now = datetime.now(JST)
    out = StringIO()
    w = out.write

    w(f"# エリオット3波入り候補 {now:%Y-%m-%d (%a)}\n\n")
    w(f"生成時刻: {now:%H:%M} JST / 参照データ: {stats['data_date']} 終値ベース\n\n")
    w(f"対象 {stats['analyzed']} 銘柄 → 波形一致 {stats['passed']} 銘柄 → 上位 {len(picks)} 銘柄を掲載\n\n")

    if not picks:
        w("2波の条件を満たす銘柄がありませんでした。相場全体が調整局面か、"
          "押しが浅すぎる／深すぎる状態です。\n")
        return out.getvalue()

    agreed = [m for m in picks if m["cross_agree"]]
    if agreed:
        names = "、".join("{} {}".format(m["code"], m["name"]) for m in agreed)
        w(f"うち **{len(agreed)}銘柄** は閾値を変えても同じ2波の底が出ています (照合 ○): {names}\n\n")

    w("| # | コード | 銘柄 | 終値 | 1波 | 押し戻し | 底から | 経過 | 目標(1.618) | 損切り | RR | 照合 | 底 | スコア |\n")
    w("|---|--------|------|------|-----|----------|--------|------|-------------|--------|----|------|----|--------|\n")
    for i, m in enumerate(picks, 1):
        w(
            f"| {i} | {m['code']} | {m['name']} | {m['close']:,.0f} | {m['wave1_pct']:+.1f}% | "
            f"{m['retrace'] * 100:.0f}% | {m['rebound_pct']:+.1f}% | {m['days_since']}日 | "
            f"{m['target162']:,.0f} | {m['stop']:,.0f} | {m['rr']:.1f} | "
            f"{cross_mark(m)} | {'確定' if m['l2_confirmed'] else '未確定'} | "
            f"{m['score']:.0f} |\n"
        )
    if picks[0]["cross_agree"] is not None:
        w("\n照合 ○ = 転換点の閾値を "
          f"{picks[0]['cross_pct']:.0f}% に変えても同じ2波の底が出る（閾値の取り方に依存しない波形）。\n")
    else:
        w("\n照合 … = 照合を無効にして実行しています。\n")
    w("底「未確定」= 2波の底からの反発がまだ閾値に届いておらず、"
      "チャートツールのエリオット指標では通常まだカウントされない段階。\n\n")

    w("## 銘柄メモ\n\n")
    for i, m in enumerate(picks, 1):
        w(f"### {i}. {m['code']} {m['name']}  (スコア {m['score']:.0f})\n\n")
        w(f"- 特徴: {label(m)}\n")
        w(f"- **1波**: {m['l0_date']} 安値 {m['l0']:,.0f}円 → {m['h1_date']} 高値 {m['h1']:,.0f}円 "
          f"({m['wave1_pct']:+.1f}% / {m['days_w1']}日 / ATR {m['wave1_atr']:.1f}個分)\n")
        w(f"- **2波**: {m['h1_date']} → {m['l2_date']} 安値 {m['l2']:,.0f}円 "
          f"({-m['wave2_pct']:.1f}% / {m['days_w2']}日 / 押し戻し {m['retrace'] * 100:.0f}%)\n")
        w(f"- **現在**: {m['close']:,.0f}円 (2波の底から {m['rebound_pct']:+.1f}% / {m['days_since']}日経過 / "
          f"1波の値幅の {m['recovery'] * 100:.0f}% を回復)\n")
        w(f"- 出来高: 1波 {m['vol_w1'] / 10000:,.0f}万株 → 2波 {m['vol_w2'] / 10000:,.0f}万株 "
          f"(比 {m['vol_contraction']:.2f}) → 反発後 {m['vol_reb'] / 10000:,.0f}万株 (比 {m['vol_rebound']:.2f})\n")
        w(f"- RSI(14) {m['rsi']:.0f} / 5日線 {m['sma5']:,.0f} / 25日線 {m['sma25']:,.0f} / 75日線 {m['sma75']:,.0f}\n")
        w(f"- 波形の確からしさ: 転換点の閾値 {m['swing_pct']:.1f}% (ATR {m['atr_pct']:.1f}% × {m['swing_pct'] / m['atr_pct']:.1f}) で検出、"
          f"{m['cross_pct']:.0f}%閾値でも "
          f"{'**同じ2波の底を検出**' if m['cross_agree'] else '別の波形になる（この閾値でしか見えない）'}。"
          f"2波の底は{'確定済み' if m['l2_confirmed'] else '**未確定**（反発がまだ閾値に届いていない）'}\n")
        w(f"- **エントリー目安**: 1波高値 {m['h1']:,.0f}円 の上抜けで3波確度が上がる\n")
        w(f"- **損切り**: {m['stop']:,.0f}円 (2波の底 {m['l2']:,.0f}円 の少し下 / 現値から {-m['risk_pct']:.1f}%) — "
          f"底を割ると、この波動の数え方自体が無効になる\n")
        w(f"- **目標**: 1.0倍 {m['target100']:,.0f}円 / 1.618倍 {m['target162']:,.0f}円 "
          f"(リスクリワード {m['rr']:.1f})\n\n")

    w("---\n\n")
    w("波動のカウントは日足のジグザグからの機械的な推定です。エリオット波動は解釈の幅が大きく、\n")
    w("同じチャートでも数え方は一つに定まりません。投資判断を推奨するものではなく、\n")
    w("売買は自己責任で行ってください。\n")
    return out.getvalue()


# --------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    codes = load_codes(args.codes_file)
    print(f"{len(codes)} 銘柄の株価を取得中... (2年分の日足)")

    results: list[dict] = []
    fetched = 0
    failures = 0

    def work(item: tuple[str, str]) -> tuple[bool, dict | None, object]:
        code, name = item
        try:
            bars = fetch_bars(code, FETCH_RANGE)
        except Exception:
            return False, None, None
        if len(bars) < MIN_BARS:
            return False, None, None
        return True, detect(code, name, bars, args), bars[-1].date

    latest_dates = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (ok, m, date) in enumerate(pool.map(work, codes), 1):
            if ok:
                fetched += 1
                latest_dates.append(date)
                if m is not None:
                    results.append(m)
            else:
                failures += 1
            if i % 25 == 0 or i == len(codes):
                print(f"  {i}/{len(codes)} 完了", end="\r", flush=True)
    print()

    if failures:
        print(f"[WARN] {failures} 銘柄はデータ取得できずスキップしました。")
    if not fetched:
        sys.exit("株価データを取得できませんでした。ネットワーク接続を確認してください。")

    # 大半が取れていない状態のランキングは母集団が偏るため、出さずに落とす。
    success_rate = fetched / len(codes)
    if success_rate < args.min_success_rate:
        sys.exit(
            f"取得成功率 {success_rate:.0%} が下限 {args.min_success_rate:.0%} を下回りました "
            f"({fetched}/{len(codes)} 銘柄)。レート制限の可能性があります。"
            "--workers を下げるか、時間をおいて再実行してください。"
        )

    data_date = max(latest_dates)
    if args.require_fresh and data_date < datetime.now(JST).date():
        print(f"最新データが {data_date} で本日分ではありません（休場日と判断）。何もせず終了します。")
        return

    for m in results:
        m["score"] = score(m)
    results.sort(key=lambda m: m["score"], reverse=True)
    picks = results[: args.top]

    stats = {
        "analyzed": fetched,
        "passed": len(results),
        "data_date": data_date.strftime("%Y-%m-%d"),
    }

    print_console(picks, stats)
    report = format_report(picks, stats)

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        stem = os.path.join(args.out_dir, f"{datetime.now(JST):%Y-%m-%d}")
        with open(f"{stem}.md", "w", encoding="utf-8") as f:
            f.write(report)
        # 後から「この波動カウントは当たっていたか」を検証できるよう、数値も残す。
        with open(f"{stem}.json", "w", encoding="utf-8") as f:
            json.dump({
                "generated": datetime.now(JST).isoformat(timespec="seconds"),
                "data_date": stats["data_date"],
                "analyzed": stats["analyzed"],
                "passed": stats["passed"],
                "picks": [
                    {
                        "code": m["code"], "name": m["name"], "close": m["close"],
                        "score": m["score"], "retrace": m["retrace"],
                        "cross_agree": m["cross_agree"], "l2_confirmed": m["l2_confirmed"],
                        "swing_pct": m["swing_pct"],
                        "wave1_pct": m["wave1_pct"], "days_since": m["days_since"],
                        "l0": m["l0"], "l0_date": str(m["l0_date"]),
                        "h1": m["h1"], "h1_date": str(m["h1_date"]),
                        "l2": m["l2"], "l2_date": str(m["l2_date"]),
                        "entry_trigger": m["h1"], "stop": m["stop"],
                        "target100": m["target100"], "target162": m["target162"],
                        "rr": m["rr"],
                    }
                    for m in picks
                ],
            }, f, ensure_ascii=False, indent=1)
        print(f"レポートを保存しました: {stem}.md / {stem}.json")

    if args.gmail_from:
        subject = f"【エリオット3波入り候補】{datetime.now(JST):%m/%d} {len(picks)}銘柄"
        if not send_gmail(args.gmail_from, args.gmail_to, args.gmail_password, subject, report):
            # 自動実行では送信失敗が唯一の異常サインになるため、黙って成功扱いにしない。
            sys.exit("メール送信に失敗しました。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="エリオット波動 3波入り候補スクリーナー",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  python elliott_wave3.py\n"
            "  python elliott_wave3.py --top 15 --out-dir reports/elliott\n"
            "  python elliott_wave3.py --min-wave1 12 --max-age 7\n"
        ),
    )
    parser.add_argument("--top", type=int, default=10, metavar="件数", help="表示する上位銘柄数 (デフォルト: 10)")
    parser.add_argument("--min-wave1", type=float, default=8.0, metavar="パーセント", help="1波の最低上昇率 (デフォルト: 8%%)")
    parser.add_argument("--min-retrace", type=float, default=0.382, metavar="比率", help="2波の押し戻し率の下限 (デフォルト: 0.382)")
    parser.add_argument("--max-retrace", type=float, default=0.786, metavar="比率", help="2波の押し戻し率の上限 (デフォルト: 0.786)")
    parser.add_argument("--max-age", type=int, default=15, metavar="日数", help="2波の底からの最大経過日数 (デフォルト: 15)")
    parser.add_argument("--max-recovery", type=float, default=0.70, metavar="比率", help="2波の下げ幅をどこまで戻した銘柄まで拾うか (デフォルト: 0.70)")
    parser.add_argument("--allow-breakout", action="store_true", help="1波高値をすでに抜いた銘柄も対象にする")
    parser.add_argument("--min-rr", type=float, default=1.0, metavar="倍", help="リスクリワード(1.618目標)の下限 (デフォルト: 1.0)")
    parser.add_argument("--swing", type=float, default=4.0, metavar="パーセント", help="転換点とみなす逆行率の下限 (デフォルト: 4%%)")
    parser.add_argument("--swing-atr", type=float, default=2.5, metavar="倍", help="転換点の閾値をATRの何倍にするか (デフォルト: 2.5)")
    parser.add_argument("--cross-swing", type=float, default=4.0, metavar="パーセント", help="波形の照合に使う固定閾値。0で照合しない (デフォルト: 4%%)")
    parser.add_argument("--min-turnover", type=float, default=10, metavar="億円", help="20日平均売買代金の下限 (デフォルト: 10億円)")
    parser.add_argument("--min-price", type=float, default=300, metavar="円", help="株価の下限 (デフォルト: 300円)")
    parser.add_argument("--max-price", type=float, default=0, metavar="円", help="株価の上限 (0で無制限)")
    parser.add_argument("--codes-file", default=DEFAULT_LIST, metavar="パス", help="銘柄リストCSV (code,name)")
    parser.add_argument("--out-dir", metavar="パス", help="Markdownレポートの保存先ディレクトリ")
    parser.add_argument("--workers", type=int, default=6, metavar="数", help="並列取得数 (デフォルト: 6)")
    parser.add_argument("--min-success-rate", type=float, default=0.6, metavar="割合", help="許容する最低取得成功率 (デフォルト: 0.6)")
    parser.add_argument("--require-fresh", action="store_true", help="最新データが当日でなければ（休場日）何もせず終了する")
    parser.add_argument("--gmail-from", default=os.environ.get("GMAIL_FROM"), metavar="アドレス", help="送信元Gmailアドレス (環境変数 GMAIL_FROM)")
    parser.add_argument("--gmail-to", default=os.environ.get("GMAIL_TO"), metavar="アドレス", help="送信先メールアドレス (環境変数 GMAIL_TO)")
    parser.add_argument("--gmail-password", default=os.environ.get("GMAIL_PASSWORD"), metavar="パスワード", help="Gmailアプリパスワード (環境変数 GMAIL_PASSWORD)")
    args = parser.parse_args()

    if args.gmail_from or args.gmail_to or args.gmail_password:
        if not all([args.gmail_from, args.gmail_to, args.gmail_password]):
            sys.exit("Gmail通知には --gmail-from / --gmail-to / --gmail-password をすべて指定してください。")

    try:
        run(args)
    except KeyboardInterrupt:
        print("\n中断しました。")


if __name__ == "__main__":
    main()
