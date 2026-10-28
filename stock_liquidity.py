#!/usr/bin/env python3
"""
1銘柄の流動性マップとエントリー候補（デイトレ用、既定はフジクラ 5803）

前営業日の5分足・日足から、注文が溜まりやすい価格帯（前日高値/安値、前日POC、
オーダーブロック、スイング高値/安値、同値の高値/安値、キリ番）を集め、
理由が重なる価格帯を「本命」として、条件付きのエントリー候補を出します。
寄り付き前 (8:00頃) の実行を想定しています。

Usage: python stock_liquidity.py
       python stock_liquidity.py --code 9984 --out-dir reports/liquidity
"""

import os
import json
import argparse
from datetime import datetime

from nikkei_daytrade import JST, Bar, http_get, send_gmail

CHART_API = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval={interval}"

# 価格帯ごとの重み。理由が重なるほど止まりやすいとみなす。
WEIGHTS = {
    "前日高値": 2, "前日安値": 2, "前日POC": 2,
    "前日VAH": 1, "前日VAL": 1, "2日前高値": 1, "2日前安値": 1,
    "買いOB": 2, "売りOB": 2, "買いOB(接触済)": 1, "売りOB(接触済)": 1,
    "EQH": 2, "EQL": 2, "スイング高値": 1, "スイング安値": 1,
    "日足スイング高値": 2, "日足スイング安値": 2, "キリ番": 1,
}


# --------------------------------------------------------------------------
# データ取得
# --------------------------------------------------------------------------

def fetch(symbol: str, range_: str, interval: str) -> list[Bar]:
    """Bar.date には足の開始時刻 (JST の datetime) を入れる。"""
    data = json.loads(http_get(CHART_API.format(symbol=symbol, range=range_, interval=interval)))
    result = data.get("chart", {}).get("result")
    if not result:
        return []
    res = result[0]
    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
    bars = []
    for i, ts in enumerate(res.get("timestamp") or []):
        o, h, l, c = quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i]
        if None in (o, h, l, c) or (interval != "1d" and not quote["volume"][i]):
            continue
        bars.append(Bar(datetime.fromtimestamp(ts, JST), o, h, l, c, quote["volume"][i] or 0))
    return bars


def fetch_name(symbol: str) -> str:
    data = json.loads(http_get(CHART_API.format(symbol=symbol, range="1d", interval="1d")))
    meta = (data.get("chart", {}).get("result") or [{}])[0].get("meta", {})
    return meta.get("shortName") or meta.get("longName") or symbol


# --------------------------------------------------------------------------
# 指標
# --------------------------------------------------------------------------

def atr(bars: list[Bar], period: int = 14) -> float:
    trs = [max(c.h - c.l, abs(c.h - p.c), abs(c.l - p.c)) for p, c in zip(bars[-period - 1:-1], bars[-period:])]
    return sum(trs) / len(trs) if trs else 0.0


def ema(values: list[float], period: int) -> float:
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def profile(bars: list[Bar], rows: int = 50, va_pct: float = 0.7) -> tuple[float, float, float]:
    """各足の hlc3 に出来高を割り当てた価格帯別出来高から POC / VAH / VAL を返す。
    TradingView 用の liquidity_map.pine と同じ計算。"""
    hi, lo = max(b.h for b in bars), min(b.l for b in bars)
    step = (hi - lo) / rows
    bins = [0.0] * rows
    for b in bars:
        idx = min(rows - 1, max(0, int(((b.h + b.l + b.c) / 3 - lo) / step)))
        bins[idx] += b.v
    poc = bins.index(max(bins))
    up = dn = poc
    acc, total = bins[poc], sum(bins)
    while acc < total * va_pct and (up < rows - 1 or dn > 0):
        v_up = bins[up + 1] if up < rows - 1 else -1
        v_dn = bins[dn - 1] if dn > 0 else -1
        if v_up >= v_dn:
            up += 1
            acc += v_up
        else:
            dn -= 1
            acc += v_dn
    return lo + step * (poc + 0.5), lo + step * (up + 1), lo + step * dn


def pivots(bars: list[Bar], n: int) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """左右 n 本より高い高値 / 低い安値。"""
    highs, lows = [], []
    for i in range(n, len(bars) - n):
        window = bars[i - n:i + n + 1]
        if bars[i].h == max(b.h for b in window):
            highs.append((i, bars[i].h))
        if bars[i].l == min(b.l for b in window):
            lows.append((i, bars[i].l))
    return highs, lows


def daily_trend(daily: list[Bar]) -> tuple[str, list[str]]:
    """日足の大きな山・谷を前と比べて HH/HL/LH/LL を付け、トレンドを判定する。
    確定した山・谷のあとに直近の谷（山）を割り込んでいれば、それを優先する（CHoCH）。"""
    highs, lows = pivots(daily, 10)
    if len(highs) < 2 or len(lows) < 2:
        return "判断材料不足", []
    (hi_i, hi_v), (lo_i, lo_v) = highs[-1], lows[-1]
    h_tag = "HH" if hi_v > highs[-2][1] else "LH"
    l_tag = "HL" if lo_v > lows[-2][1] else "LL"
    notes = [f"直近の大きな山 {hi_v:,.0f}円（{daily[hi_i].date:%m/%d}）= {h_tag}（ひとつ前 {highs[-2][1]:,.0f}円）",
             f"直近の大きな谷 {lo_v:,.0f}円（{daily[lo_i].date:%m/%d}）= {l_tag}（ひとつ前 {lows[-2][1]:,.0f}円）"]

    after = daily[max(hi_i, lo_i) + 1:]
    low_after = min(after, key=lambda b: b.l) if after else None
    high_after = max(after, key=lambda b: b.h) if after else None
    if low_after and low_after.l < lo_v:
        trend = "下降"
        notes.append(f"その後 {lo_v:,.0f}円の谷を割り込み、{low_after.l:,.0f}円（{low_after.date:%m/%d}）まで下落 = 下向きのCHoCH")
    elif high_after and high_after.h > hi_v:
        trend = "上昇"
        notes.append(f"その後 {hi_v:,.0f}円の山を上抜け、{high_after.h:,.0f}円（{high_after.date:%m/%d}）まで上昇 = 上向きのCHoCH")
    elif h_tag == "HH" and l_tag == "HL":
        trend = "上昇"
    elif h_tag == "LH" and l_tag == "LL":
        trend = "下降"
    else:
        trend = "もみ合い（山と谷の向きが不一致）"

    close = daily[-1].c
    below = [(i, v) for i, v in lows if v < close]
    if below:
        i, v = below[-1]
        notes.append(f"下にある直近の大きな谷：{v:,.0f}円（{daily[i].date:%m/%d}）。割ると下降がさらにはっきりする")
    return trend, notes


def order_blocks(bars: list[Bar], unit: float) -> list[dict]:
    """強い動きの直前にあった最後の逆向きの足。反対側を抜けられたものは除外する。"""
    obs = []
    for i in range(len(bars) - 3):
        b = bars[i]
        move = bars[i + 3].c
        if b.c < b.o and move - b.h > 2 * unit:
            obs.append({"kind": "買い", "top": b.h, "bottom": b.l, "i": i})
        elif b.c > b.o and b.l - move > 2 * unit:
            obs.append({"kind": "売り", "top": b.h, "bottom": b.l, "i": i})
    alive = []
    for ob in obs:
        later = bars[ob["i"] + 4:]
        if ob["kind"] == "買い":
            if any(x.l < ob["bottom"] for x in later):
                continue
            ob["touched"] = any(x.l <= ob["top"] for x in later)
        else:
            if any(x.h > ob["top"] for x in later):
                continue
            ob["touched"] = any(x.h >= ob["bottom"] for x in later)
        alive.append(ob)
    return alive


# --------------------------------------------------------------------------
# 価格帯の集約とシナリオ
# --------------------------------------------------------------------------

def collect_levels(intra: list[Bar], daily: list[Bar], day_atr: float) -> tuple[list[tuple[float, str]], dict]:
    days = sorted({b.date.date() for b in intra})
    base, prev = days[-1], (days[-2] if len(days) > 1 else None)
    today = [b for b in intra if b.date.date() == base]
    recent = [b for b in intra if b.date.date() in (prev, base)]

    by_day = {b.date.date(): b for b in daily}
    d0 = by_day.get(base)
    info = {"base": base, "close": d0.c if d0 else today[-1].c,
            "high": d0.h if d0 else max(b.h for b in today), "low": d0.l if d0 else min(b.l for b in today)}
    poc, vah, val = profile(today)
    info.update(poc=poc, vah=vah, val=val)

    levels = [(info["high"], "前日高値"), (info["low"], "前日安値"), (poc, "前日POC"), (vah, "前日VAH"), (val, "前日VAL")]
    if prev in by_day:
        levels += [(by_day[prev].h, "2日前高値"), (by_day[prev].l, "2日前安値")]

    # 5分足のスイング：まだ取られていない（その後に抜けられていない）ものだけ残す
    highs, lows = pivots(recent, 3)
    live_h = [(i, v) for i, v in highs if all(b.h <= v for b in recent[i + 1:])]
    live_l = [(i, v) for i, v in lows if all(b.l >= v for b in recent[i + 1:])]
    tol = max(day_atr * 0.03, 1)
    for group, eq, sw in ((live_h, "EQH", "スイング高値"), (live_l, "EQL", "スイング安値")):
        for k, (_, v) in enumerate(group):
            same = any(abs(v - w) <= tol for j, (_, w) in enumerate(group) if j != k)
            levels.append((v, eq if same else sw))

    unit = sum(b.h - b.l for b in recent) / len(recent)
    for ob in order_blocks(recent, unit):
        name = ob["kind"] + "OB" + ("(接触済)" if ob["touched"] else "")
        levels.append(((ob["top"] + ob["bottom"]) / 2, name))

    # 日足の大きな山・谷（ATR3つ分以内のもの）
    d_highs, d_lows = pivots(daily, 5)
    close = info["close"]
    for _, v in d_highs[-4:]:
        if abs(v - close) <= day_atr * 3:
            levels.append((v, "日足スイング高値"))
    for _, v in d_lows[-4:]:
        if abs(v - close) <= day_atr * 3:
            levels.append((v, "日足スイング安値"))

    # キリ番（株価の桁に合わせた刻み）
    step = 100 if close >= 2000 else 50 if close >= 500 else 10
    r = (close // step) * step
    for k in range(-3, 4):
        levels.append((r + k * step, "キリ番"))
    return levels, info


def cluster(levels: list[tuple[float, str]], tol: float) -> list[dict]:
    """近い価格をまとめて、理由の数で重み付けしたゾーンにする。"""
    zones = []
    for price, name in sorted(levels):
        if zones and price - zones[-1]["hi"] <= tol:
            z = zones[-1]
            z["hi"] = price
            z["names"].append(name)
        else:
            zones.append({"lo": price, "hi": price, "names": [name]})
    for z in zones:
        uniq = list(dict.fromkeys(z["names"]))
        z["names"] = uniq
        z["score"] = sum(WEIGHTS.get(n, 1) for n in uniq)
        z["mid"] = (z["lo"] + z["hi"]) / 2
    return zones


def rank(score: int) -> str:
    return "◎本命" if score >= 4 else "○候補" if score >= 2 else "△参考"


def yen(v: float) -> str:
    return f"{v:,.0f}"


def zone_text(z: dict) -> str:
    rng = yen(z["lo"]) if round(z["lo"]) == round(z["hi"]) else f"{yen(z['lo'])}〜{yen(z['hi'])}"
    return f"{rng}円"


def plan(z: dict, side: str, zones: list[dict], day_atr: float, trend: str) -> dict:
    """ゾーンでの「抜けて戻る」エントリー案。損切りはゾーンの外、利確は次の重要ゾーン。"""
    buf = day_atr * 0.05
    min_stop = day_atr * 0.15
    if side == "long":
        entry = z["hi"]
        stop = min(z["lo"] - buf, entry - min_stop)
        targets = [t for t in zones if t["lo"] > entry + min_stop and t["score"] >= 2]
        aligned = trend == "上昇"
    else:
        entry = z["lo"]
        stop = max(z["hi"] + buf, entry + min_stop)
        targets = [t for t in reversed(zones) if t["hi"] < entry - min_stop and t["score"] >= 2]
        aligned = trend == "下降"
    # 1回の利確は ATR の半分程度までに収める。途中の小さな壁は越えて、1.5R 以上取れる価格帯を優先する
    risk = abs(entry - stop)
    cap = day_atr * 0.6
    tps = [t["lo"] if side == "long" else t["hi"] for t in targets]
    tps = [tp for tp in tps if abs(tp - entry) <= cap]
    far = [tp for tp in tps if abs(tp - entry) >= risk * 1.5]
    if far:
        tps = tps[tps.index(far[0]):]
    tps = tps[:2] or [entry + cap * 0.8 if side == "long" else entry - cap * 0.8]
    rr = abs(tps[0] - entry) / risk if risk else 0
    return {"zone": z, "side": side, "entry": entry, "stop": stop, "tps": tps, "rr": rr, "aligned": aligned}


def plan_text(p: dict) -> list[str]:
    z = p["zone"]
    if p["side"] == "long":
        title = f"ロング｜{zone_text(z)}（{' / '.join(z['names'])}）{rank(z['score'])}"
        cond = (f"{zone_text(z)}まで下げ、{yen(z['lo'])}円を一度下に抜けてから"
                f"5分足が{yen(z['hi'])}円の上に戻って確定したらロングを検討")
    else:
        title = f"ショート｜{zone_text(z)}（{' / '.join(z['names'])}）{rank(z['score'])}"
        cond = (f"{zone_text(z)}まで上げ、{yen(z['hi'])}円を一度上に抜けてから"
                f"5分足が{yen(z['lo'])}円の下に戻って確定したらショートを検討")
    lines = [f"### {title}", f"- 条件：{cond}",
             f"- 損切り：{yen(p['stop'])}円（{abs(p['entry'] - p['stop']):,.0f}円）",
             f"- 利確：{' → '.join(yen(t) + '円' for t in p['tps'])}",
             f"- リスクリワード：約1:{p['rr']:.1f}"]
    if p["rr"] < 1.5:
        lines.append("- ⚠ リスクリワードが1:1.5未満。条件がそろっても見送り寄り")
    lines.append("- 日足と同じ向き：◎" if p["aligned"] else "- 日足と逆向き：△（利確は近め、株数は少なめ）")
    return lines


# --------------------------------------------------------------------------

def build_report(code: str, name: str, intra: list[Bar], daily: list[Bar]) -> tuple[str, dict]:
    day_atr = atr(daily)
    levels, info = collect_levels(intra, daily, day_atr)
    close = info["close"]
    trend, trend_notes = daily_trend(daily)
    closes = [b.c for b in daily]
    ema9, sma25 = ema(closes[-60:], 9), sum(closes[-25:]) / 25

    zones = cluster(levels, max(day_atr * 0.04, 2))
    near = [z for z in zones if abs(z["mid"] - close) <= day_atr]
    # キリ番だけの価格帯は候補にしない
    above = [z for z in near if z["lo"] > close and z["names"] != ["キリ番"]]
    below = [z for z in near if z["hi"] < close and z["names"] != ["キリ番"]]
    res = sorted(above, key=lambda z: (-z["score"], z["lo"]))[:2]
    sup = sorted(below, key=lambda z: (-z["score"], -z["hi"]))[:2]

    plans = [plan(z, "short", zones, day_atr, trend) for z in sorted(res, key=lambda z: z["lo"])]
    plans += [plan(z, "long", zones, day_atr, trend) for z in sorted(sup, key=lambda z: -z["hi"])]
    order = {"下降": "short", "上昇": "long"}.get(trend)
    plans.sort(key=lambda p: (p["side"] != order, -p["zone"]["score"], -p["rr"]))

    base = info["base"]
    out = [f"# {name}（{code}）流動性マップ {base:%Y-%m-%d} 引け時点", ""]
    out += ["## 前提", f"- 前日終値：{yen(close)}円（高値 {yen(info['high'])} / 安値 {yen(info['low'])}）",
            f"- 日足ATR(14)：{yen(day_atr)}円 → 今日の想定値幅 約{yen(day_atr * 0.7)}〜{yen(day_atr)}円",
            f"- 片側に伸びた場合の目安：上 {yen(close + day_atr)}円 / 下 {yen(close - day_atr)}円",
            f"- 1回の利確目安 約{yen(day_atr * 0.5)}円、損切り目安 約{yen(day_atr * 0.15)}〜{yen(day_atr * 0.25)}円",
            f"- 日足トレンド：**{trend}**（EMA9 {yen(ema9)} / SMA25 {yen(sma25)}、終値は"
            f"{'EMA9の上' if close > ema9 else 'EMA9の下'}・{'SMA25の上' if close > sma25 else 'SMA25の下'}）"]
    out += [f"  - {n}" for n in trend_notes]
    poc_pos = "上" if close > info["poc"] else "下"
    out += [f"- 前日POC {yen(info['poc'])}円 / VAH {yen(info['vah'])}円 / VAL {yen(info['val'])}円"
            f"（終値はPOCの{poc_pos}）", ""]

    out += ["## エントリー候補（優先順）", "日足と同じ向き、理由の重なり、リスクリワードの順に並べています。", ""]
    if not plans:
        out += ["ATRの範囲内に重要な価格帯がありません。判断材料不足のため、寄り付き後の動きを見てから判断してください。", ""]
    for p in plans:
        out += plan_text(p) + [""]

    out += ["## 価格帯マップ（終値 ± ATR）", "| 価格帯 | 理由 | 評価 |", "|---|---|---|"]
    for z in sorted(near, key=lambda z: -z["mid"]):
        mark = "▲" if z["lo"] > close else "▼" if z["hi"] < close else "●"
        out.append(f"| {mark} {zone_text(z)} | {' / '.join(z['names'])} | {rank(z['score'])} |")
    out.append(f"| ― {yen(close)}円 | 前日終値 | |")
    out += ["", "## 見送った方がいい状況",
            "- 寄り付き直後の5〜10分（値が飛びやすい）",
            "- 上の候補と下の候補の間（どちらの価格帯にも届いていない）",
            f"- 窓を空けて候補の価格帯を飛び越えて始まった日（前日終値から±{yen(day_atr * 0.5)}円以上）",
            "- その日の値幅をATRの8割以上使ったあとの、同じ向きの新規エントリー",
            "- 決算発表の前後（日程は会社のIRで確認）、米ハイテク株が大きく動いた翌朝", ""]

    first = plans[0] if plans else None
    direction = f"日足は{trend}。前日はPOC（{yen(info['poc'])}円）の{poc_pos}で引けています。"
    wait = (f"{zone_text(first['zone'])}での{'ロング' if first['side'] == 'long' else 'ショート'}"
            "（抜けて戻るのを確認してから）" if first else "寄り付き後に価格帯ができるのを待つ")
    out += ["---", "", f"【現在の方向性】{direction}", f"【待つポイント】{wait}",
            f"【次に確認すること】寄り付きが前日終値（{yen(close)}円）とPOCの上か下か、"
            f"最初の30分の高値・安値（寄り後レンジ）がどの価格帯に当たるか", "",
            "※ 自動計算した候補です。売買の判断はご自身で行ってください。CVDや板は含んでいません。"]

    data = {"date": str(base), "code": code, "close": close, "atr": day_atr, "trend": trend,
            "poc": info["poc"], "vah": info["vah"], "val": info["val"],
            "plans": [{"side": p["side"], "zone": [p["zone"]["lo"], p["zone"]["hi"]], "reasons": p["zone"]["names"],
                       "entry": p["entry"], "stop": p["stop"], "targets": p["tps"], "rr": round(p["rr"], 2)} for p in plans]}
    return "\n".join(out), data


def main() -> None:
    parser = argparse.ArgumentParser(description="1銘柄の流動性マップとエントリー候補を作る")
    parser.add_argument("--code", default="5803", help="証券コード (デフォルト: 5803 フジクラ)")
    parser.add_argument("--out-dir", metavar="パス", help="レポートの保存先。同じ日のレポートがあれば何もしない（休場日の重複送信防止）")
    parser.add_argument("--gmail-from", default=os.environ.get("GMAIL_FROM"))
    parser.add_argument("--gmail-to", default=os.environ.get("GMAIL_TO"))
    parser.add_argument("--gmail-password", default=os.environ.get("GMAIL_PASSWORD"))
    args = parser.parse_args()

    symbol = f"{args.code}.T"
    intra = fetch(symbol, "5d", "5m")
    daily = fetch(symbol, "1y", "1d")
    if not intra or len(daily) < 30:
        raise SystemExit("株価データを取得できませんでした。")
    base = intra[-1].date.date()
    daily = [b for b in daily if b.date.date() <= base]
    name = fetch_name(symbol)

    report, data = build_report(args.code, name, intra, daily)
    print(report)

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        path = os.path.join(args.out_dir, f"{args.code}_{base}")
        if os.path.exists(path + ".md"):
            print(f"{path}.md は作成済みのため、保存と送信をスキップします。")
            return
        with open(path + ".md", "w", encoding="utf-8") as f:
            f.write(report + "\n")
        with open(path + ".json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    if args.gmail_from and args.gmail_to and args.gmail_password:
        send_gmail(args.gmail_from, args.gmail_to, args.gmail_password,
                   f"【流動性マップ】{name} {base:%m/%d}引け → 今日の候補", report)


if __name__ == "__main__":
    main()
