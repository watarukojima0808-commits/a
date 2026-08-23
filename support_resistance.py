#!/usr/bin/env python3
"""
サポート／レジスタンス インジケーター

日足の高値・安値の転換点 (フラクタル) を価格帯ごとにまとめ、何度も反発・
反落している「意識されている価格」を支持線 (サポート) と抵抗線 (レジスタンス)
として抽出します。さらに現在値とラインの位置関係から、エントリー・損切り・
目標・リスクリワードまでを組み立てて表示します。

Usage: python support_resistance.py 7203
       python support_resistance.py 7203 --svg chart.svg
       python support_resistance.py --scan --top 15 --out-dir reports/sr
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
    fetch_symbol,
    load_codes,
    send_gmail,
)

# ラインの信頼性はタッチ回数で決まる。半年では 2〜3 回しか付かないことが多いので 1 年取る。
FETCH_RANGE = "1y"
MIN_BARS = 60

# タッチ後に何本先まで見て「反発の大きさ」を測るか。
REACTION_BARS = 10


# --------------------------------------------------------------------------
# 転換点とライン
# --------------------------------------------------------------------------

class Level:
    """1本の水平線。price を中心に ±tol の帯として扱う。"""

    __slots__ = ("price", "kind", "touches", "score", "reaction", "vol_ratio")

    def __init__(self, price: float, touches: list[dict]):
        self.price = price
        self.touches = touches          # [{"idx", "price", "date", "kind", "vol_ratio"}, ...]
        self.kind = "R"                 # 現在値との比較で後から S / R を決める
        self.score = 0.0
        self.reaction = 0.0
        self.vol_ratio = 0.0

    @property
    def count(self) -> int:
        return len(self.touches)

    @property
    def last_idx(self) -> int:
        return max(t["idx"] for t in self.touches)

    @property
    def first_idx(self) -> int:
        return min(t["idx"] for t in self.touches)

    @property
    def last_date(self):
        return max(self.touches, key=lambda t: t["idx"])["date"]


def find_pivots(bars: list[Bar], k: int) -> list[dict]:
    """前後 k 本より高い高値 / 低い安値を転換点として拾う。

    ジグザグと違い 1 本の足が高値・安値どちらの転換点にもなり得る。ラインは
    「何回タッチされたか」で強さが決まるため、転換点は多めに拾ったほうがよい。
    """
    pivots: list[dict] = []
    for i in range(k, len(bars) - k):
        window = bars[i - k:i + k + 1]
        b = bars[i]
        # 同値が並ぶ場合は最初の 1 本だけを転換点にする (同じ足を二重に数えない)。
        if b.h >= max(w.h for w in window) and b.h > max(w.h for w in window[:k]):
            pivots.append({"idx": i, "price": b.h, "date": b.date, "kind": "H"})
        if b.l <= min(w.l for w in window) and b.l < min(w.l for w in window[:k]):
            pivots.append({"idx": i, "price": b.l, "date": b.date, "kind": "L"})
    return pivots


def cluster_levels(bars: list[Bar], pivots: list[dict], tol: float) -> list[Level]:
    """近い価格の転換点をまとめて 1 本の線にする。帯の幅は 2*tol までに抑える。"""
    if not pivots:
        return []

    avg_vol = sum(b.v for b in bars) / len(bars)
    for p in pivots:
        p["vol_ratio"] = bars[p["idx"]].v / avg_vol if avg_vol else 1.0

    levels: list[Level] = []
    for p in sorted(pivots, key=lambda x: x["price"]):
        if levels and p["price"] - levels[-1].touches[0]["price"] <= 2 * tol:
            levels[-1].touches.append(p)
        else:
            levels.append(Level(p["price"], [p]))

    n = len(bars)
    for lv in levels:
        # 線の位置は「最近の / 出来高を伴ったタッチ」に寄せる。古い髭に引っ張られないため。
        total = 0.0
        weighted = 0.0
        for t in lv.touches:
            w = (0.5 + 0.5 * (t["idx"] + 1) / n) * min(2.0, max(0.5, t["vol_ratio"]))
            weighted += t["price"] * w
            total += w
        lv.price = weighted / total
        lv.vol_ratio = sum(t["vol_ratio"] for t in lv.touches) / lv.count
    return levels


def reaction_strength(bars: list[Bar], lv: Level, atr: float) -> float:
    """タッチ後に何ATR跳ね返されたかの平均。跳ね返しの弱い線はただの通過点。"""
    moves = []
    for t in lv.touches:
        ahead = bars[t["idx"] + 1:t["idx"] + 1 + REACTION_BARS]
        if len(ahead) < 3:
            continue
        if t["kind"] == "H":
            moves.append((t["price"] - min(b.l for b in ahead)) / atr)
        else:
            moves.append((max(b.h for b in ahead) - t["price"]) / atr)
    return sum(moves) / len(moves) if moves else 0.0


def score_level(bars: list[Bar], lv: Level, atr: float) -> float:
    """ラインの強さを 100 点満点で採点する。"""
    n = len(bars)
    age = n - 1 - lv.last_idx                       # 最終タッチからの経過 (営業日)
    span = lv.last_idx - lv.first_idx               # 何日にわたって機能しているか

    touch_pts = min(35.0, 12.0 * lv.count - 4.0)    # 2回=20 / 3回=32 / 4回以上=35
    recent_pts = 20.0 * max(0.0, 1.0 - age / 60.0)
    span_pts = 15.0 * min(1.0, span / 60.0)
    vol_pts = 15.0 * min(1.0, max(0.0, (lv.vol_ratio - 0.8) / 0.7))
    lv.reaction = reaction_strength(bars, lv, atr)
    react_pts = 15.0 * min(1.0, lv.reaction / 2.0)
    return touch_pts + recent_pts + span_pts + vol_pts + react_pts


def detect_levels(bars: list[Bar], args: argparse.Namespace) -> tuple[list[Level], float, float]:
    """支持線・抵抗線を検出して強さ順に返す。戻り値は (levels, atr, tol)。"""
    last = bars[-1]
    atr = average_true_range(bars)
    if atr <= 0:
        atr = last.c * 0.01
    tol = max(atr * args.tol_atr, last.c * args.tol_pct / 100)

    levels = cluster_levels(bars, find_pivots(bars, args.pivot), tol)
    levels = [lv for lv in levels if lv.count >= args.min_touches]
    for lv in levels:
        lv.score = score_level(bars, lv, atr)
        lv.kind = "R" if lv.price >= last.c else "S"
    levels.sort(key=lambda lv: lv.price)
    return levels, atr, tol


# --------------------------------------------------------------------------
# エントリー
# --------------------------------------------------------------------------

def make_plan(kind: str, name: str, lv: Level | None, entry: float, stop: float,
              target: float | None, atr: float, note: str, status: str,
              clarity: float = 0.0) -> dict | None:
    """エントリー / 損切り / 目標をまとめる。目標が無ければリスクの2倍を置く。"""
    risk = entry - stop if kind == "long" else stop - entry
    if risk <= 0:
        return None
    if target is None:
        target = entry + 2 * risk if kind == "long" else entry - 2 * risk
    reward = target - entry if kind == "long" else entry - target
    if reward <= 0:
        return None
    return {
        "kind": kind,
        "name": name,
        "level": lv,
        "level_price": lv.price if lv else 0.0,
        "level_score": lv.score if lv else 0.0,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk": risk,
        "risk_atr": risk / atr,
        "rr": reward / risk,
        "note": note,
        "status": status,
        "clarity": clarity,
    }


def cap_target(p: dict, atr: float, cap_atr: float) -> dict:
    """次の線が遠すぎる場合の目標を ATR で頭打ちにする。

    半年前に一度だけ効いた線をそのまま目標にすると、届くまで何ヶ月もかかる
    プランのリスクリワードだけが良く見えてしまうため。
    """
    cap = cap_atr * atr
    if abs(p["target"] - p["entry"]) > cap:
        p["target"] = p["entry"] + cap if p["kind"] == "long" else p["entry"] - cap
        p["note"] += f" / 次の線が遠いため目標は{cap_atr:.0f}ATRで設定"
    p["rr"] = abs(p["target"] - p["entry"]) / p["risk"]
    return p


def signals_today(bars: list[Bar], levels: list[Level], atr: float, tol: float,
                  args: argparse.Namespace) -> list[dict]:
    """最新の足で成立したエントリーサインを返す。"""
    last, prev = bars[-1], bars[-2]
    avg_vol = sum(b.v for b in bars[-21:-1]) / 20 if len(bars) >= 21 else last.v
    vol_ratio = last.v / avg_vol if avg_vol else 1.0
    rng = max(last.h - last.l, 1e-9)
    lower_wick = (min(last.o, last.c) - last.l) / rng
    upper_wick = (last.h - max(last.o, last.c)) / rng

    supports = [lv for lv in levels if lv.price < last.c]
    resists = [lv for lv in levels if lv.price >= last.c]
    s1 = max(supports, key=lambda lv: lv.price) if supports else None
    r1 = min(resists, key=lambda lv: lv.price) if resists else None

    out: list[dict] = []
    for lv in levels:
        # 抵抗線を終値で上抜けた → ブレイク買い。抜けた線が次の支持線になる前提で損切りを置く。
        if prev.c <= lv.price < last.c and (last.c - lv.price) >= args.break_atr * atr:
            clarity = 15.0 * min(1.0, (last.c - lv.price) / atr / 0.6)
            out.append(make_plan(
                "long", "抵抗線ブレイク", lv, last.c, lv.price - args.stop_atr * atr,
                r1.price if r1 else None, atr,
                f"{lv.price:,.0f}円を終値で上抜け (出来高 {vol_ratio:.1f}x)",
                "signal", clarity))

        # 支持線を終値で下抜けた → ブレイクダウン売り。
        if prev.c >= lv.price > last.c and (lv.price - last.c) >= args.break_atr * atr:
            clarity = 15.0 * min(1.0, (lv.price - last.c) / atr / 0.6)
            out.append(make_plan(
                "short", "支持線ブレイク", lv, last.c, lv.price + args.stop_atr * atr,
                s1.price if s1 else None, atr,
                f"{lv.price:,.0f}円を終値で下抜け (出来高 {vol_ratio:.1f}x)",
                "signal", clarity))

        # 支持線まで下げて終値では上に戻した → 押し目買い。翌日の前日高値超えで入る。
        if (lv.price < last.c and last.l <= lv.price + tol
                and (last.c - lv.price) <= args.near_atr * atr):
            out.append(make_plan(
                "long", "支持線で反発", lv, last.h, lv.price - args.stop_atr * atr,
                r1.price if r1 else None, atr,
                f"{lv.price:,.0f}円まで下げて終値では上に戻した (下ヒゲ {lower_wick:.0%})",
                "signal", 15.0 * min(1.0, lower_wick / 0.4)))

        # 抵抗線まで上げて終値では下に押し返された → 戻り売り。
        if (lv.price >= last.c and last.h >= lv.price - tol
                and (lv.price - last.c) <= args.near_atr * atr):
            out.append(make_plan(
                "short", "抵抗線で反落", lv, last.l, lv.price + args.stop_atr * atr,
                s1.price if s1 else None, atr,
                f"{lv.price:,.0f}円まで上げて終値では押し返された (上ヒゲ {upper_wick:.0%})",
                "signal", 15.0 * min(1.0, upper_wick / 0.4)))

    out = [cap_target(p, atr, args.max_target_atr) for p in out if p]
    for p in out:
        # 45点: ラインの強さ / 25点: リスクリワード / 15点: 出来高 / 15点: サインの明瞭さ
        p["vol_ratio"] = vol_ratio
        p["score"] = (p["level_score"] * 0.45
                      + min(p["rr"], 3.0) / 3.0 * 25
                      + min(vol_ratio, 2.0) / 2.0 * 15
                      + p["clarity"])
    if args.long_only:
        out = [p for p in out if p["kind"] == "long"]
    out = [p for p in out if p["rr"] >= args.min_rr]
    out.sort(key=lambda p: p["score"], reverse=True)

    # 同じラインで同じ方向のサインが二重に出ることがある (割った線の反発など)。強いほうだけ残す。
    seen: set[tuple[float, str]] = set()
    unique = []
    for p in out:
        key = (round(p["level_price"], 2), p["kind"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


def pending_plans(bars: list[Bar], levels: list[Level], atr: float, tol: float,
                  args: argparse.Namespace) -> list[dict]:
    """サインが出ていない日でも「ここまで来たら入る」という待機プランを出す。"""
    last = bars[-1]
    supports = sorted([lv for lv in levels if lv.price < last.c], key=lambda lv: -lv.price)
    resists = sorted([lv for lv in levels if lv.price >= last.c], key=lambda lv: lv.price)
    s1, s2 = (supports + [None, None])[:2]
    r1, r2 = (resists + [None, None])[:2]

    plans: list[dict | None] = []
    if r1:
        entry = r1.price + args.break_atr * atr
        plans.append(make_plan(
            "long", "抵抗線ブレイク待ち", r1, entry, r1.price - args.stop_atr * atr,
            r2.price if r2 else None, atr,
            f"{r1.price:,.0f}円を終値で上抜けたら買い (現在値まで {(r1.price / last.c - 1) * 100:+.1f}%)",
            "plan"))
    if s1:
        entry = s1.price + tol
        plans.append(make_plan(
            "long", "支持線まで押し目待ち", s1, entry, s1.price - args.stop_atr * atr,
            r1.price if r1 else None, atr,
            f"{s1.price:,.0f}円まで押して反発を確認したら買い (現在値から {(s1.price / last.c - 1) * 100:+.1f}%)",
            "plan"))
    if not args.long_only:
        if s1:
            entry = s1.price - args.break_atr * atr
            plans.append(make_plan(
                "short", "支持線ブレイク待ち", s1, entry, s1.price + args.stop_atr * atr,
                s2.price if s2 else None, atr,
                f"{s1.price:,.0f}円を終値で下抜けたら売り (現在値から {(s1.price / last.c - 1) * 100:+.1f}%)",
                "plan"))
        if r1:
            entry = r1.price - tol
            plans.append(make_plan(
                "short", "抵抗線まで戻り売り待ち", r1, entry, r1.price + args.stop_atr * atr,
                s1.price if s1 else None, atr,
                f"{r1.price:,.0f}円まで戻して失速したら売り (現在値まで {(r1.price / last.c - 1) * 100:+.1f}%)",
                "plan"))

    built = [cap_target(p, atr, args.max_target_atr) for p in plans if p]
    out = [p for p in built if p["rr"] >= args.min_rr]
    for p in out:
        p["vol_ratio"] = 0.0
        p["score"] = p["level_score"] * 0.45 + min(p["rr"], 3.0) / 3.0 * 25
    out.sort(key=lambda p: p["score"], reverse=True)
    # 目標までの値幅が損切り幅に見合わないプランは黙って消えると気づけないので数だけ残す。
    if out:
        out[0]["dropped"] = len(built) - len(out)
    return out


# --------------------------------------------------------------------------
# 描画 (ターミナル)
# --------------------------------------------------------------------------

def visible_width(s: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - visible_width(s))


def rpad(s: str, width: int) -> str:
    return " " * max(0, width - visible_width(s)) + s


def ascii_chart(bars: list[Bar], levels: list[Level], width: int = 68, height: int = 20) -> str:
    """終値ではなくローソク足でチャートを描き、支持線・抵抗線を重ねる。"""
    view = bars[-width:]
    if len(view) < 2:
        return ""

    lo = min(b.l for b in view)
    hi = max(b.h for b in view)
    # 現在値のすぐ外側にある線も見えるように、値幅の 15% までは上下に伸ばす。
    margin = (hi - lo) * 0.15
    shown = [lv for lv in levels if lo - margin <= lv.price <= hi + margin]
    if shown:
        lo = min(lo, min(lv.price for lv in shown))
        hi = max(hi, max(lv.price for lv in shown))
    if hi <= lo:
        return ""

    step = (hi - lo) / height
    grid = [[" "] * len(view) for _ in range(height)]
    labels: dict[int, str] = {}

    def row_of(price: float) -> int:
        return min(height - 1, max(0, int((hi - price) / step)))

    for lv in sorted(shown, key=lambda x: x.score):
        r = row_of(lv.price)
        ch = "━" if lv.score >= 60 else "─"
        grid[r] = [ch] * len(view)
        labels[r] = (f"{lv.price:,.0f} [{lv.kind}] {lv.count}回 強さ{lv.score:.0f}")

    for c, b in enumerate(view):
        body_hi, body_lo = max(b.o, b.c), min(b.o, b.c)
        for r in range(row_of(b.h), row_of(b.l) + 1):
            grid[r][c] = "│"
        for r in range(row_of(body_hi), row_of(body_lo) + 1):
            grid[r][c] = "█"

    close_row = row_of(bars[-1].c)
    out = StringIO()
    for r in range(height):
        mark = "◀ 現在値" if r == close_row else ""
        label = labels.get(r, "")
        tail = f"  {label} {mark}".rstrip() if (label or mark) else ""
        out.write("".join(grid[r]) + tail + "\n")
    out.write(f"{view[0].date:%m/%d} 〜 {view[-1].date:%m/%d} ({len(view)}本)  "
              f"━ 強い線 / ─ 弱い線 / [S] 支持線 [R] 抵抗線\n")
    return out.getvalue()


def plan_lines(p: dict) -> list[str]:
    side = "買い" if p["kind"] == "long" else "売り"
    tag = "サイン" if p["status"] == "signal" else "待機"
    lv = p["level"]
    lines = [f"[{tag}] {p['name']} ({side})  スコア {p['score']:.0f}"]
    lines.append(
        f"  エントリー {p['entry']:,.0f}円 / 損切り {p['stop']:,.0f}円 "
        f"({(p['stop'] / p['entry'] - 1) * 100:+.1f}%, {p['risk_atr']:.1f}ATR) / "
        f"目標 {p['target']:,.0f}円 ({(p['target'] / p['entry'] - 1) * 100:+.1f}%)"
    )
    lines.append(
        f"  リスクリワード {p['rr']:.1f}倍 / 根拠の線 {p['level_price']:,.0f}円 "
        f"(強さ {p['level_score']:.0f}, {lv.count if lv else 0}回タッチ)"
    )
    lines.append(f"  {p['note']}")
    return lines


def print_console(m: dict) -> None:
    now = datetime.now(JST)
    title = f"{m['code']} {m['name']}" if m["name"] else m["code"]
    print()
    print("=" * 78)
    print(f" {title}  サポート/レジスタンス  {now:%Y-%m-%d (%a) %H:%M} JST  "
          f"({m['date']:%Y-%m-%d} 終値ベース)")
    print("=" * 78)
    print(f" 終値 {m['close']:,.0f}円 ({m['ret1']:+.1f}%) / ATR14 {m['atr']:,.0f}円 "
          f"({m['atr'] / m['close'] * 100:.1f}%) / 許容誤差 ±{m['tol']:,.0f}円 / "
          f"出来高 {m['vol_ratio']:.1f}x")
    print()

    if not m["levels"]:
        print(" 有効なラインを検出できませんでした。--min-touches を 1 に下げるか "
              "--pivot を小さくしてください。\n")
        return

    print(m["chart"])
    print(f"{pad('種別', 6)}{rpad('価格', 7)} {rpad('現在値比', 8)} {pad('タッチ', 8)}"
          f"{pad('最終', 8)}{pad('跳ね返し', 10)}{rpad('強さ', 4)}")
    print("-" * 78)
    for lv in sorted(m["levels"], key=lambda x: -x.price):
        kind = "抵抗" if lv.kind == "R" else "支持"
        print(f"{pad(kind, 6)}{lv.price:>7,.0f} "
              f"{(lv.price / m['close'] - 1) * 100:>7.1f}% {pad(f'{lv.count}回', 8)}"
              f"{pad(f'{lv.last_date:%m/%d}', 8)}{pad(f'{lv.reaction:.1f}ATR', 10)}"
              f"{lv.score:>4.0f}")
    print("-" * 78)

    print("\n■ エントリー候補\n")
    if not m["plans"]:
        print(" 条件を満たすエントリー候補はありません。--min-rr を下げて再実行してください。\n")
        return
    for p in m["plans"]:
        for line in plan_lines(p):
            print(" " + line)
        print()
    dropped = sum(p.get("dropped", 0) for p in m["plans"])
    if dropped:
        print(f" ※ リスクリワードが {m['min_rr']:.1f} 倍に届かないプランを {dropped} 件省略しました。"
              " --min-rr を下げると表示されます。\n")


# --------------------------------------------------------------------------
# 描画 (SVG)
# --------------------------------------------------------------------------

def svg_chart(m: dict, bars: list[Bar], width: int = 960, height: int = 520,
              show_bars: int = 120) -> str:
    """ローソク足に支持線・抵抗線とエントリー計画を重ねた SVG を返す。"""
    view = bars[-show_bars:]
    left, right, top, bottom = 62, 170, 46, 48
    plot_w = width - left - right
    plot_h = height - top - bottom

    lo = min(b.l for b in view)
    hi = max(b.h for b in view)
    for lv in m["levels"]:
        if lo - (hi - lo) * 0.2 <= lv.price <= hi + (hi - lo) * 0.2:
            lo, hi = min(lo, lv.price), max(hi, lv.price)
    span = max(hi - lo, 1e-9)
    lo -= span * 0.04
    hi += span * 0.04
    span = hi - lo

    def y(price: float) -> float:
        return top + (hi - price) / span * plot_h

    slot = plot_w / len(view)
    body = max(1.6, slot * 0.62)

    e = StringIO()
    w = e.write
    w(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
      f'viewBox="0 0 {width} {height}" font-family="sans-serif">')
    w(f'<rect width="{width}" height="{height}" fill="#ffffff"/>')

    title = f"{m['code']} {m['name']}".strip()
    w(f'<text x="{left}" y="26" font-size="16" font-weight="bold" fill="#222">'
      f'{esc(title)}  サポート/レジスタンス</text>')
    w(f'<text x="{width - right + 8}" y="26" font-size="11" fill="#666">'
      f'{m["date"]:%Y-%m-%d} 終値 {m["close"]:,.0f}</text>')
    w(f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" '
      f'fill="#fbfbfb" stroke="#dddddd"/>')

    # 価格目盛り
    for i in range(5):
        price = lo + span * i / 4
        py = y(price)
        w(f'<line x1="{left}" y1="{py:.1f}" x2="{left + plot_w}" y2="{py:.1f}" '
          f'stroke="#eeeeee"/>')
        w(f'<text x="{left - 8}" y="{py + 4:.1f}" font-size="10" fill="#888" '
          f'text-anchor="end">{price:,.0f}</text>')

    # 日付目盛り
    for i in range(0, len(view), max(1, len(view) // 5)):
        tx = left + slot * (i + 0.5)
        w(f'<text x="{tx:.1f}" y="{top + plot_h + 14:.1f}" font-size="10" fill="#888" '
          f'text-anchor="middle">{view[i].date:%m/%d}</text>')

    # ローソク足 (陽線=赤 / 陰線=青)
    for i, b in enumerate(view):
        cx = left + slot * (i + 0.5)
        color = "#d64545" if b.c >= b.o else "#3a72c4"
        w(f'<line x1="{cx:.1f}" y1="{y(b.h):.1f}" x2="{cx:.1f}" y2="{y(b.l):.1f}" '
          f'stroke="{color}" stroke-width="1"/>')
        y_hi, y_lo = y(max(b.o, b.c)), y(min(b.o, b.c))
        w(f'<rect x="{cx - body / 2:.1f}" y="{y_hi:.1f}" width="{body:.1f}" '
          f'height="{max(1.0, y_lo - y_hi):.1f}" fill="{color}"/>')

    # 支持線・抵抗線
    for lv in m["levels"]:
        if not (lo <= lv.price <= hi):
            continue
        color = "#c0392b" if lv.kind == "R" else "#1f6fb2"
        ly = y(lv.price)
        w(f'<line x1="{left}" y1="{ly:.1f}" x2="{left + plot_w}" y2="{ly:.1f}" '
          f'stroke="{color}" stroke-width="{1 + lv.score / 50:.1f}" '
          f'stroke-dasharray="7 4" opacity="0.85"/>')
        w(f'<text x="{left + plot_w + 6}" y="{ly + 4:.1f}" font-size="10" fill="{color}">'
          f'{lv.price:,.0f} [{lv.kind}] {lv.count}回 強さ{lv.score:.0f}</text>')

    # 先頭のエントリー計画
    if m["plans"]:
        p = m["plans"][0]
        for price, color, tag in (
            (p["entry"], "#127c3f", "IN"),
            (p["stop"], "#8a8a8a", "STOP"),
            (p["target"], "#127c3f", "T"),
        ):
            if not (lo <= price <= hi):
                continue
            py = y(price)
            w(f'<line x1="{left}" y1="{py:.1f}" x2="{left + plot_w}" y2="{py:.1f}" '
              f'stroke="{color}" stroke-width="1.4" stroke-dasharray="2 3"/>')
            w(f'<text x="{left + 6}" y="{py - 4:.1f}" font-size="10" fill="{color}">'
              f'{tag} {price:,.0f}</text>')
        arrow = "▲" if p["kind"] == "long" else "▼"
        cx = left + plot_w - slot * 0.5
        ay = y(p["entry"]) + (16 if p["kind"] == "long" else -8)
        w(f'<text x="{cx:.1f}" y="{ay:.1f}" font-size="14" fill="#127c3f" '
          f'text-anchor="middle">{arrow}</text>')
        side = "買い" if p["kind"] == "long" else "売り"
        w(f'<text x="{left}" y="{height - 12}" font-size="11" fill="#333">'
          f'{esc(p["name"])} ({side}) — IN {p["entry"]:,.0f} / STOP {p["stop"]:,.0f} / '
          f'T {p["target"]:,.0f} / RR {p["rr"]:.1f}倍</text>')

    w(f'<text x="{left + plot_w}" y="{height - 12}" font-size="10" fill="#999" '
      f'text-anchor="end">{view[0].date:%Y-%m-%d} 〜 {view[-1].date:%Y-%m-%d}</text>')
    w("</svg>")
    return e.getvalue()


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------
# 銘柄ごとの解析
# --------------------------------------------------------------------------

def analyze(code: str, name: str, bars: list[Bar], args: argparse.Namespace,
            with_plans: bool = True) -> dict | None:
    if len(bars) < MIN_BARS:
        return None

    last = bars[-1]
    levels, atr, tol = detect_levels(bars, args)
    avg_vol = sum(b.v for b in bars[-21:-1]) / 20 if len(bars) >= 21 else last.v
    turnover = sum(b.c * b.v for b in bars[-20:]) / 20

    plans = signals_today(bars, levels, atr, tol, args) if levels else []
    signals = list(plans)
    if with_plans and levels:
        plans = plans + pending_plans(bars, levels, atr, tol, args)

    supports = [lv for lv in levels if lv.kind == "S"]
    resists = [lv for lv in levels if lv.kind == "R"]
    return {
        "code": code,
        "name": name,
        "date": last.date,
        "close": last.c,
        "ret1": (last.c / bars[-2].c - 1) * 100,
        "atr": atr,
        "tol": tol,
        "vol_ratio": last.v / avg_vol if avg_vol else 0.0,
        "turnover": turnover,
        "levels": levels,
        "s1": max(supports, key=lambda lv: lv.price) if supports else None,
        "r1": min(resists, key=lambda lv: lv.price) if resists else None,
        "signals": signals,
        "plans": plans,
        "score": max([p["score"] for p in signals], default=0.0),
        "chart": ascii_chart(bars, levels) if levels else "",
        "min_rr": args.min_rr,
    }


# --------------------------------------------------------------------------
# レポート
# --------------------------------------------------------------------------

def level_table(m: dict) -> str:
    out = StringIO()
    out.write("| 種別 | 価格 | 現在値比 | タッチ | 最終タッチ | 跳ね返し | 強さ |\n")
    out.write("|------|------|----------|--------|------------|----------|------|\n")
    for lv in sorted(m["levels"], key=lambda x: -x.price):
        out.write(
            f"| {'抵抗' if lv.kind == 'R' else '支持'} | {lv.price:,.0f}円 | "
            f"{(lv.price / m['close'] - 1) * 100:+.1f}% | {lv.count}回 | "
            f"{lv.last_date:%Y-%m-%d} | {lv.reaction:.1f}ATR | {lv.score:.0f} |\n"
        )
    return out.getvalue()


def format_single(m: dict) -> str:
    now = datetime.now(JST)
    out = StringIO()
    w = out.write
    title = f"{m['code']} {m['name']}".strip()

    w(f"# {title} サポート/レジスタンス {now:%Y-%m-%d (%a)}\n\n")
    w(f"生成時刻: {now:%H:%M} JST / 参照データ: {m['date']:%Y-%m-%d} 終値ベース\n\n")
    w(f"終値 {m['close']:,.0f}円 ({m['ret1']:+.1f}%) / ATR(14) {m['atr']:,.0f}円 "
      f"({m['atr'] / m['close'] * 100:.1f}%) / 許容誤差 ±{m['tol']:,.0f}円\n\n")

    if not m["levels"]:
        w("有効なラインを検出できませんでした。\n")
        return out.getvalue()

    w("## チャート\n\n```\n" + m["chart"] + "```\n\n")
    w("## 検出したライン\n\n" + level_table(m) + "\n")

    w("## エントリー候補\n\n")
    if not m["plans"]:
        w("条件を満たすエントリー候補はありません。\n\n")
    dropped = sum(p.get("dropped", 0) for p in m["plans"])
    if dropped:
        w(f"リスクリワードが {m['min_rr']:.1f} 倍に届かないプランを {dropped} 件省略しています。\n\n")
    for p in m["plans"]:
        side = "買い" if p["kind"] == "long" else "売り"
        tag = "サイン" if p["status"] == "signal" else "待機"
        w(f"### [{tag}] {p['name']} ({side}) — スコア {p['score']:.0f}\n\n")
        w(f"- エントリー: {p['entry']:,.0f}円\n")
        w(f"- 損切り: {p['stop']:,.0f}円 ({(p['stop'] / p['entry'] - 1) * 100:+.1f}%, "
          f"{p['risk_atr']:.1f}ATR)\n")
        w(f"- 目標: {p['target']:,.0f}円 ({(p['target'] / p['entry'] - 1) * 100:+.1f}%)\n")
        w(f"- リスクリワード: {p['rr']:.1f}倍\n")
        w(f"- 根拠の線: {p['level_price']:,.0f}円 (強さ {p['level_score']:.0f}, "
          f"{p['level'].count if p['level'] else 0}回タッチ)\n")
        w(f"- {p['note']}\n\n")

    w("---\n\n")
    w("本レポートは日足データからの機械的な抽出であり、投資判断を推奨するものではありません。\n")
    w("売買は自己責任で行ってください。\n")
    return out.getvalue()


def format_scan(picks: list[dict], stats: dict) -> str:
    now = datetime.now(JST)
    out = StringIO()
    w = out.write

    w(f"# サポート/レジスタンス エントリー候補 {now:%Y-%m-%d (%a)}\n\n")
    w(f"生成時刻: {now:%H:%M} JST / 参照データ: {stats['data_date']} 終値ベース\n\n")
    w(f"対象 {stats['analyzed']} 銘柄 → サイン発生 {stats['passed']} 銘柄 → "
      f"上位 {len(picks)} 銘柄を掲載\n\n")

    if not picks:
        w("サインの出た銘柄がありませんでした。--min-rr を下げるか --min-touches を "
          "緩めて再実行してください。\n")
        return out.getvalue()

    w("| # | コード | 銘柄 | 終値 | サイン | 線 | IN | 損切り | 目標 | RR | 強さ | 点 |\n")
    w("|---|--------|------|------|--------|----|----|--------|------|----|------|----|\n")
    for i, m in enumerate(picks, 1):
        p = m["signals"][0]
        side = "買" if p["kind"] == "long" else "売"
        w(f"| {i} | {m['code']} | {m['name']} | {m['close']:,.0f} | {p['name']}{side} | "
          f"{p['level_price']:,.0f} | {p['entry']:,.0f} | {p['stop']:,.0f} | "
          f"{p['target']:,.0f} | {p['rr']:.1f} | {p['level_score']:.0f} | "
          f"{p['score']:.0f} |\n")

    w("\n## 銘柄メモ\n\n")
    for i, m in enumerate(picks, 1):
        p = m["signals"][0]
        side = "買い" if p["kind"] == "long" else "売り"
        w(f"### {i}. {m['code']} {m['name']} — {p['name']} ({side})  スコア {p['score']:.0f}\n\n")
        w(f"- 終値 {m['close']:,.0f}円 ({m['ret1']:+.1f}%) / 出来高 {m['vol_ratio']:.1f}x / "
          f"ATR(14) {m['atr']:,.0f}円\n")
        w(f"- エントリー {p['entry']:,.0f}円 / 損切り {p['stop']:,.0f}円 "
          f"({(p['stop'] / p['entry'] - 1) * 100:+.1f}%) / 目標 {p['target']:,.0f}円 "
          f"({(p['target'] / p['entry'] - 1) * 100:+.1f}%) / RR {p['rr']:.1f}倍\n")
        w(f"- 根拠の線 {p['level_price']:,.0f}円 (強さ {p['level_score']:.0f}, "
          f"{p['level'].count if p['level'] else 0}回タッチ)\n")
        s1, r1 = m["s1"], m["r1"]
        w(f"- 直近の支持線 {s1.price:,.0f}円 / 抵抗線 {r1.price:,.0f}円\n"
          if s1 and r1 else
          f"- 直近の支持線 {s1.price:,.0f}円\n" if s1 else
          f"- 直近の抵抗線 {r1.price:,.0f}円\n" if r1 else "")
        w(f"- {p['note']}\n\n")

    w("---\n\n")
    w("本レポートは日足データからの機械的な抽出であり、投資判断を推奨するものではありません。\n")
    w("売買は自己責任で行ってください。\n")
    return out.getvalue()


def print_scan(picks: list[dict], stats: dict) -> None:
    now = datetime.now(JST)
    print()
    print("=" * 92)
    print(f" サポート/レジスタンス エントリー候補  {now:%Y-%m-%d (%a) %H:%M} JST  "
          f"({stats['data_date']} 終値ベース)")
    print("=" * 92)

    if not picks:
        print("\nサインの出た銘柄がありませんでした。--min-rr / --min-touches を緩めてください。\n")
        return

    print(f"{rpad('#', 2)} {pad('コード', 6)} {pad('銘柄', 14)}{rpad('終値', 8)} "
          f"{pad('サイン', 18)}{rpad('IN', 8)} {rpad('損切り', 8)} {rpad('目標', 8)} "
          f"{rpad('RR', 6)} {rpad('点', 4)}")
    print("-" * 92)
    for i, m in enumerate(picks, 1):
        p = m["signals"][0]
        side = "買" if p["kind"] == "long" else "売"
        print(f"{i:>2} {m['code']:<6} {pad(m['name'][:12], 14)}{m['close']:>8,.0f} "
              f"{pad(p['name'] + side, 18)}{p['entry']:>8,.0f} {p['stop']:>8,.0f} "
              f"{p['target']:>8,.0f} {p['rr']:>4.1f}倍 {p['score']:>4.0f}")
    print("-" * 92)
    for i, m in enumerate(picks, 1):
        print(f"{i:>2}. {m['code']} {m['name']}: {m['signals'][0]['note']}")
    print()


# --------------------------------------------------------------------------
# 実行
# --------------------------------------------------------------------------

def save_outputs(stem: str, report: str, payload: dict) -> None:
    with open(f"{stem}.md", "w", encoding="utf-8") as f:
        f.write(report)
    # 後から「そのラインは効いたか」を検証できるよう、数値も残す。
    with open(f"{stem}.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"レポートを保存しました: {stem}.md / {stem}.json")


def plan_json(p: dict) -> dict:
    return {
        "kind": p["kind"], "name": p["name"], "status": p["status"],
        "level": round(p["level_price"], 2), "level_score": round(p["level_score"], 1),
        "entry": round(p["entry"], 2), "stop": round(p["stop"], 2),
        "target": round(p["target"], 2), "rr": round(p["rr"], 2),
        "score": round(p["score"], 1), "note": p["note"],
    }


def level_json(lv: Level) -> dict:
    return {
        "kind": lv.kind, "price": round(lv.price, 2), "touches": lv.count,
        "last_touch": str(lv.last_date), "reaction_atr": round(lv.reaction, 2),
        "score": round(lv.score, 1),
    }


def run_single(args: argparse.Namespace) -> None:
    symbol = args.symbol
    code, name = symbol, ""
    try:
        if symbol.isdigit() and len(symbol) == 4:
            if os.path.exists(args.codes_file):
                name = dict(load_codes(args.codes_file)).get(code, "")
            bars = fetch_bars(code, FETCH_RANGE)
        else:
            # ^N225 や USDJPY=X など、Yahoo のシンボルをそのまま渡せるようにする。
            bars = fetch_symbol(symbol, FETCH_RANGE)
    except Exception as e:
        sys.exit(f"{symbol} の株価データを取得できませんでした ({e})。コードを確認してください。")

    if not bars:
        sys.exit(f"{symbol} の株価データを取得できませんでした。コードを確認してください。")
    m = analyze(code, name, bars, args)
    if m is None:
        sys.exit(f"日足が {MIN_BARS} 本に足りません ({len(bars)}本)。上場直後の銘柄かもしれません。")

    print_console(m)
    report = format_single(m)

    if args.svg:
        with open(args.svg, "w", encoding="utf-8") as f:
            f.write(svg_chart(m, bars, show_bars=args.chart_bars))
        print(f"チャートを保存しました: {args.svg}")

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        stem = os.path.join(args.out_dir, f"{code}-{datetime.now(JST):%Y-%m-%d}")
        save_outputs(stem, report, {
            "generated": datetime.now(JST).isoformat(timespec="seconds"),
            "code": code, "name": name, "data_date": str(m["date"]),
            "close": m["close"], "atr": round(m["atr"], 2), "tol": round(m["tol"], 2),
            "levels": [level_json(lv) for lv in sorted(m["levels"], key=lambda x: -x.price)],
            "plans": [plan_json(p) for p in m["plans"]],
        })

    if args.gmail_from:
        subject = f"【S/R {code} {name}】{datetime.now(JST):%m/%d}"
        if not send_gmail(args.gmail_from, args.gmail_to, args.gmail_password, subject, report):
            sys.exit("メール送信に失敗しました。")


def run_scan(args: argparse.Namespace) -> None:
    codes = load_codes(args.codes_file)
    print(f"{len(codes)} 銘柄の株価を取得中...")

    results: list[dict] = []
    failures = 0

    def work(item: tuple[str, str]) -> dict | None:
        code, name = item
        try:
            return analyze(code, name, fetch_bars(code, FETCH_RANGE), args, with_plans=False)
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

    # 大半が取得できていない状態のランキングは母集団が偏るため、出さずに落とす。
    success_rate = len(results) / len(codes)
    if success_rate < args.min_success_rate:
        sys.exit(
            f"取得成功率 {success_rate:.0%} が下限 {args.min_success_rate:.0%} を下回りました "
            f"({len(results)}/{len(codes)} 銘柄)。レート制限の可能性があります。"
            "--workers を下げるか、時間をおいて再実行してください。"
        )

    data_date = max(m["date"] for m in results)
    if args.require_fresh and data_date != datetime.now(JST).date():
        print(f"最新データが {data_date} のため休場日と判断し、何もせず終了します。")
        return

    passed = [
        m for m in results
        if m["signals"]
        and m["turnover"] >= args.min_turnover * 100_000_000
        and m["close"] >= args.min_price
        and (args.max_price <= 0 or m["close"] <= args.max_price)
    ]
    passed.sort(key=lambda m: m["score"], reverse=True)
    picks = passed[: args.top]

    stats = {
        "analyzed": len(results),
        "passed": len(passed),
        "data_date": data_date.strftime("%Y-%m-%d"),
    }

    print_scan(picks, stats)
    report = format_scan(picks, stats)

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        stem = os.path.join(args.out_dir, f"{datetime.now(JST):%Y-%m-%d}")
        save_outputs(stem, report, {
            "generated": datetime.now(JST).isoformat(timespec="seconds"),
            "data_date": stats["data_date"],
            "analyzed": stats["analyzed"],
            "passed": stats["passed"],
            "picks": [
                {
                    "code": m["code"], "name": m["name"], "close": m["close"],
                    "ret1": round(m["ret1"], 2), "vol_ratio": round(m["vol_ratio"], 2),
                    "atr": round(m["atr"], 2), "score": round(m["score"], 1),
                    "signal": plan_json(m["signals"][0]),
                    "support": level_json(m["s1"]) if m["s1"] else None,
                    "resistance": level_json(m["r1"]) if m["r1"] else None,
                }
                for m in picks
            ],
        })

    if args.gmail_from:
        subject = f"【S/R エントリー候補】{datetime.now(JST):%m/%d} {len(picks)}銘柄"
        if not send_gmail(args.gmail_from, args.gmail_to, args.gmail_password, subject, report):
            # 自動実行では送信失敗が唯一の異常サインになるため、黙って成功扱いにしない。
            sys.exit("メール送信に失敗しました。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="サポート/レジスタンス インジケーター",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  python support_resistance.py 7203\n"
            "  python support_resistance.py 7203 --svg chart.svg --out-dir reports/sr\n"
            "  python support_resistance.py --scan --top 15 --out-dir reports/sr\n"
        ),
    )
    parser.add_argument("symbol", nargs="?", help="証券コード4桁、または Yahoo のシンボル (^N225 など)")
    parser.add_argument("--scan", action="store_true", help="日経225全銘柄からサインの出た銘柄を抽出する")
    parser.add_argument("--top", type=int, default=10, metavar="件数", help="--scan で表示する上位銘柄数 (デフォルト: 10)")
    parser.add_argument("--pivot", type=int, default=3, metavar="本数", help="転換点とみなす前後の本数 (デフォルト: 3)")
    parser.add_argument("--min-touches", type=int, default=2, metavar="回数", help="ラインとして採用する最低タッチ回数 (デフォルト: 2)")
    parser.add_argument("--tol-atr", type=float, default=0.5, metavar="倍", help="同じラインとみなす価格差 (ATRの倍数, デフォルト: 0.5)")
    parser.add_argument("--tol-pct", type=float, default=0.4, metavar="パーセント", help="同じラインとみなす価格差の下限 (デフォルト: 0.4%%)")
    parser.add_argument("--near-atr", type=float, default=1.0, metavar="倍", help="ラインへの接近をサインとみなす距離 (ATRの倍数, デフォルト: 1.0)")
    parser.add_argument("--break-atr", type=float, default=0.15, metavar="倍", help="ブレイクと認める終値の抜け幅 (ATRの倍数, デフォルト: 0.15)")
    parser.add_argument("--stop-atr", type=float, default=0.5, metavar="倍", help="損切りをラインから何ATR離すか (デフォルト: 0.5)")
    parser.add_argument("--max-target-atr", type=float, default=4.0, metavar="倍", help="目標までの値幅の上限 (ATRの倍数, デフォルト: 4.0)")
    parser.add_argument("--min-rr", type=float, default=1.0, metavar="倍", help="採用するリスクリワードの下限 (デフォルト: 1.0)")
    parser.add_argument("--long-only", action="store_true", help="買いのエントリーだけを表示する")
    parser.add_argument("--chart-bars", type=int, default=120, metavar="本数", help="SVGに描くローソク足の本数 (デフォルト: 120)")
    parser.add_argument("--svg", metavar="パス", help="SVGチャートの保存先 (単一銘柄のみ)")
    parser.add_argument("--min-turnover", type=float, default=10, metavar="億円", help="--scan の20日平均売買代金の下限 (デフォルト: 10億円)")
    parser.add_argument("--min-price", type=float, default=300, metavar="円", help="--scan の株価下限 (デフォルト: 300円)")
    parser.add_argument("--max-price", type=float, default=0, metavar="円", help="--scan の株価上限 (0で無制限)")
    parser.add_argument("--codes-file", default=DEFAULT_LIST, metavar="パス", help="銘柄リストCSV (code,name)")
    parser.add_argument("--out-dir", metavar="パス", help="レポートの保存先ディレクトリ")
    parser.add_argument("--workers", type=int, default=6, metavar="数", help="並列取得数 (デフォルト: 6)")
    parser.add_argument("--min-success-rate", type=float, default=0.6, metavar="割合", help="許容する最低取得成功率 (デフォルト: 0.6)")
    parser.add_argument("--require-fresh", action="store_true", help="最新データが当日でなければ（休場日）何もせず終了する")
    parser.add_argument("--gmail-from", default=os.environ.get("GMAIL_FROM"), metavar="アドレス", help="送信元Gmailアドレス (環境変数 GMAIL_FROM)")
    parser.add_argument("--gmail-to", default=os.environ.get("GMAIL_TO"), metavar="アドレス", help="送信先メールアドレス (環境変数 GMAIL_TO)")
    parser.add_argument("--gmail-password", default=os.environ.get("GMAIL_PASSWORD"), metavar="パスワード", help="Gmailアプリパスワード (環境変数 GMAIL_PASSWORD)")
    args = parser.parse_args()

    if not args.scan and not args.symbol:
        parser.error("証券コードを指定するか、--scan を付けてください。")
    if args.scan and args.svg:
        parser.error("--svg は単一銘柄のときだけ使えます。")

    if args.gmail_from or args.gmail_to or args.gmail_password:
        if not all([args.gmail_from, args.gmail_to, args.gmail_password]):
            sys.exit("Gmail通知には --gmail-from / --gmail-to / --gmail-password をすべて指定してください。")

    try:
        run_scan(args) if args.scan else run_single(args)
    except KeyboardInterrupt:
        print("\n中断しました。")


if __name__ == "__main__":
    main()
