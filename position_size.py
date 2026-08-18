#!/usr/bin/env python3
"""資金100万円で、実際に何株買えばいいかを決める。

holy_grail.py の §4 は「ケリー比率の半分以下で賭けろ」で終わっている。
だがそれは無限に細かく賭け金を刻めるという前提の話で、日本株には
単元株100株という壁がある。100万円だと、この壁が支配的になる。

  ・ 日経225の1単元は中央値で約46万円。100万円では2銘柄しか持てない。
  ・ 2銘柄に集中したポートフォリオのボラは、分散が効かず単元銘柄に近い。
  ・ その結果、現物フルインベスト(レバレッジ1.0倍)が、
    ケリー比率を「超えて」しまうことがある。信用取引を使う以前の問題。

つまり100万円の資金管理で最初に決めるべきは、レバレッジではなく現金比率。
このツールはそれを計算する。

    python position_size.py                      # 100万円の設計図を全部出す
    python position_size.py --capital 3000000    # 資金を変える
    python position_size.py --unit 1             # 単元未満株(S株等)で計算する
    python position_size.py --order              # reports/elliott の候補を発注表にする
    python position_size.py --code 7532 --entry 984.5 --stop 866.4
"""

import argparse
import glob
import json
import math
import os
import random

from holy_grail import TRADING_DAYS, growth_rate, kelly_fraction

UNIT = 100  # 日本株の売買単位


# --------------------------------------------------------------------------
# §1 単元株の壁 — 100万円で何銘柄持てるか
# --------------------------------------------------------------------------

def portfolio_vol(single_sd: float, k: int, rho: float) -> float:
    """k銘柄を等金額で持った時のポートフォリオのボラ。"""
    if k < 1:
        return single_sd
    return single_sd * math.sqrt((1.0 + (k - 1) * rho) / k)


def cmd_wall(args: argparse.Namespace) -> None:
    cap = args.capital
    unit = args.unit
    print()
    print("=" * 78)
    print(f" §1 単元株の壁 — 資金 {cap:,}円 で何銘柄持てるか")
    print("=" * 78)
    print(f" 売買単位 {unit}株。株価×{unit} が1銘柄の最小投資額になる。")
    print()
    print(f"{'株価':>10} {'1単元':>12} {'資金比':>8} {'買える単元数':>13} {'最大分散':>10} {'判定':>14}")
    print("-" * 78)
    for price in (500, 1000, 1500, 2000, 3000, 5000, 8000, 15000, 30000):
        lot = price * unit
        share = lot / cap * 100
        lots = int(cap // lot)
        if lots == 0:
            verdict, k = "1単元も買えない", 0
        elif share > 50:
            verdict, k = "1銘柄で資金の半分", 1
        elif share > 20:
            verdict, k = "集中しすぎ", lots
        elif share > 10:
            verdict, k = "許容範囲", lots
        else:
            verdict, k = "分散できる", lots
        print(f"{price:>9,}円 {lot:>11,}円 {share:>7.1f}% {lots:>12}単元 "
              f"{k:>9}銘柄 {verdict:>14}")
    print("-" * 78)
    print()

    prices = load_universe_prices()
    if prices:
        lots = [p * unit for p in prices]
        buyable = sum(1 for x in lots if x <= cap)
        under20 = sum(1 for x in lots if x <= cap * 0.20)
        lots.sort()
        med = lots[len(lots) // 2]
        print("■ 実際の銘柄で数えると (reports/ に出た銘柄の株価を使用)")
        print(f"  対象銘柄        : {len(lots)} 銘柄")
        print(f"  1単元の中央値   : {med:,.0f}円")
        print(f"  1単元でも買える : {buyable} / {len(lots)} 銘柄 ({buyable / len(lots) * 100:.0f}%)")
        print(f"  資金の20%以内   : {under20} / {len(lots)} 銘柄 ({under20 / len(lots) * 100:.0f}%)")
        print(f"                    ← 5銘柄に分散するにはここに入る必要がある")
        print()

    print("■ 結論")
    if unit == 1:
        print("  単元未満株なら株価に関係なく1株から買えるため、この壁は消える。")
        print("  100万円でも10銘柄への均等分散が可能になる。")
    else:
        print(f"  {cap:,}円では、まともに分散できるのは株価2,000円以下の銘柄に限られる。")
        print("  日経225の主力どころは1単元が資金の30〜50%を占めてしまう。")
        print("  対策は3つ。")
        print("    (a) 株価の低い銘柄だけを対象にする  → 銘柄選択の自由度を失う")
        print("    (b) 単元未満株(S株/かぶミニ/ワン株)を使う → --unit 1 で再計算")
        print("    (c) 資金を増やす                      → 分散には最低500万円欲しい")
        print("  100万円なら (b) が現実解。1株単位で買えるので、次の§2の")
        print("  リスク計算がそのまま発注数量になる。")
    print()


def load_universe_prices() -> list[float]:
    """reports/ に記録された終値から、銘柄ごとの最新株価を拾う。"""
    latest: dict[str, tuple[str, float]] = {}
    for path in sorted(glob.glob("reports/*.json") + glob.glob("reports/elliott/*.json")):
        try:
            rep = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        d = rep.get("data_date", "")
        for p in rep.get("picks", []):
            if p.get("close") and (p["code"] not in latest or latest[p["code"]][0] < d):
                latest[p["code"]] = (d, float(p["close"]))
    return [v[1] for v in latest.values()]


# --------------------------------------------------------------------------
# §2 現金比率 — 100万円で最初に決めるべきはレバレッジではない
# --------------------------------------------------------------------------

def cmd_cash(args: argparse.Namespace) -> None:
    mu = args.mu / 100.0
    sd_single = args.sd / 100.0
    cap = args.capital

    print()
    print("=" * 78)
    print(f" §2 現金比率 — 集中したポートフォリオではフルインベストが既に賭けすぎ")
    print("=" * 78)
    print(f" 前提: 1銘柄1日あたり 平均 {args.mu:+.3f}% / 標準偏差 {args.sd:.2f}%")
    print(f"       銘柄間の相関 {args.rho:.1f} (同じ日本株なのでこれくらいは効く)")
    print()
    print(f"{'保有銘柄数':>11} {'ﾎﾟｰﾄﾌｫﾘｵ':>10} {'ｹﾘｰ比率':>10} {'ﾊｰﾌｹﾘｰ':>10} "
          f"{'推奨投資額':>13} {'現金':>9} {'年成長率':>10}")
    print(f"{'':>11} {'ﾎﾞﾗ':>10} {'f*':>10} {'f*/2':>10} {'(ﾊｰﾌｹﾘｰ)':>13} {'':>9} {'':>10}")
    print("-" * 78)

    for k in (1, 2, 3, 5, 8, 10):
        sd_p = portfolio_vol(sd_single, k, args.rho)
        f = kelly_fraction(mu, sd_p)
        half = f / 2.0
        invest = min(half, 1.0) * cap
        cash = cap - invest
        g = growth_rate(mu, sd_p, min(half, 1.0))
        ann = (math.exp(g * TRADING_DAYS) - 1) * 100
        flag = "" if half <= 1.0 else "  ←現物では届かない"
        print(f"{k:>10}銘柄 {sd_p * 100:>9.2f}% {f:>9.2f}x {half:>9.2f}x "
              f"{invest:>12,.0f}円 {cash / cap * 100:>8.0f}% {ann:>9.1f}%{flag}")
    print("-" * 78)
    print()

    k_real = max(1, int(args.capital // (args.lot_cost or 460000)))
    sd_p = portfolio_vol(sd_single, k_real, args.rho)
    f = kelly_fraction(mu, sd_p)
    print("■ 100万円の現実")
    print(f"  1単元の中央値を約{args.lot_cost or 460000:,}円とすると、現物で持てるのは {k_real} 銘柄。")
    print(f"  その時のポートフォリオボラは {sd_p * 100:.2f}%/日、ケリー比率は {f:.2f}倍。")
    if f / 2 < 1.0:
        print(f"  ハーフケリーは {f / 2:.2f}倍 = {f / 2 * cap:,.0f}円。")
        print(f"  つまり{cap:,}円のうち {cap - f / 2 * cap:,.0f}円は現金で置くのが正しい。")
        print("  「余力を残す」ではなく、賭けすぎを避けるための必須の配分である。")
    else:
        print(f"  ハーフケリーが {f / 2:.2f}倍。現物フルインベストでもまだ賭け足りない水準。")
        print("  この場合の制約はリスクではなく資金量。分散を増やす方が効く。")
    print()
    print("■ なぜ集中するとケリー比率が下がるのか")
    print("  f* = μ/σ² なので、σ が2倍になると f* は1/4になる。")
    print("  分散が効かない = σ が大きい = 賭けてよい金額が急激に減る。")
    print(f"  10銘柄なら σ={portfolio_vol(sd_single, 10, args.rho) * 100:.2f}% で f*={kelly_fraction(mu, portfolio_vol(sd_single, 10, args.rho)):.2f}倍、")
    print(f"  2銘柄だと σ={portfolio_vol(sd_single, 2, args.rho) * 100:.2f}% で f*={kelly_fraction(mu, portfolio_vol(sd_single, 2, args.rho)):.2f}倍。")
    print("  分散不足は「儲けが減る」のではなく「賭けられる額が減る」形で罰を受ける。")
    print()

# --------------------------------------------------------------------------
# §3 発注数量 — 損切り幅から株数を逆算する
#
# 株数を決める制約は2つあり、小さい方が勝つ。
#   リスク制約: 損切りに当たった時の損失を、資金の r% に収める
#   資金制約  : そもそも買える金額に収める (§2 の投資枠)
# 100万円だと、株価の高い銘柄では資金制約が先に効く。
# --------------------------------------------------------------------------

def size_position(capital: float, risk_pct: float, entry: float, stop: float,
                  budget: float, unit: int = UNIT) -> dict:
    """損切り幅と投資枠から発注株数を決める。単元に切り下げる。"""
    per_share = entry - stop
    if per_share <= 0:
        return {"error": "損切り価格がエントリー以上です"}

    risk_yen = capital * risk_pct / 100.0
    by_risk = risk_yen / per_share
    by_budget = budget / entry
    raw = min(by_risk, by_budget)
    shares = int(raw // unit) * unit

    if shares <= 0:
        # 1単元すら置けない。この銘柄はこの資金では扱えない。
        min_risk = unit * per_share / capital * 100
        return {
            "shares": 0, "binding": "単元",
            "min_risk_pct": min_risk,
            "min_cost": unit * entry,
        }

    cost = shares * entry
    loss = shares * per_share
    return {
        "shares": shares,
        "cost": cost,
        "loss": loss,
        "risk_pct": loss / capital * 100,
        "weight": cost / capital * 100,
        "stop_pct": per_share / entry * 100,
        "binding": "リスク" if by_risk <= by_budget else "資金",
        "waste": (raw - shares) / raw * 100 if raw > 0 else 0.0,
    }


def cmd_size(args: argparse.Namespace) -> None:
    cap = args.capital
    budget = cap * args.invest_pct / 100.0
    print()
    print("=" * 78)
    print(f" §3 発注数量 — 損切り幅から株数を逆算する")
    print("=" * 78)
    print(f" 資金 {cap:,}円 / 1トレードのリスク {args.risk:.2f}% (= {cap * args.risk / 100:,.0f}円)")
    print(f" 投資枠 {args.invest_pct:.0f}% (= {budget:,.0f}円) / 売買単位 {args.unit}株")
    print()
    print(" 株数 = min( リスク許容額 ÷ 損切り幅 , 投資枠 ÷ 株価 ) を単元に切り下げ")
    print()
    print(f"{'株価':>8} {'損切り幅':>9} {'理論株数':>9} {'発注株数':>9} {'投資額':>11} "
          f"{'実ﾘｽｸ':>8} {'比重':>7} {'制約':>7}")
    print("-" * 78)

    for price in (800, 1500, 2500, 4000, 6000, 10000):
        for stop_pct in (5.0, 10.0):
            entry = float(price)
            stop = entry * (1 - stop_pct / 100)
            r = size_position(cap, args.risk, entry, stop, budget, args.unit)
            per_share = entry - stop
            raw = min(cap * args.risk / 100 / per_share, budget / entry)
            if r["shares"] == 0:
                print(f"{price:>7,}円 {stop_pct:>8.0f}% {raw:>9.0f} {'発注不可':>9} "
                      f"{'—':>11} {'—':>8} {'—':>7} {'単元':>7}")
                continue
            print(f"{price:>7,}円 {stop_pct:>8.0f}% {raw:>9.0f} {r['shares']:>8,}株 "
                  f"{r['cost']:>10,.0f}円 {r['risk_pct']:>7.2f}% {r['weight']:>6.1f}% "
                  f"{r['binding']:>7}")
    print("-" * 78)
    print()
    print("■ 読み方")
    if args.unit > 1:
        print(f"  ・ 「発注不可」は理論株数が{args.unit}株に届かない場合。損切りを{args.risk:.1f}%の")
        print("    リスクに収めようとすると1単元が大きすぎる、という状態である。")
        print("  ・ 制約が「資金」の行は、リスクを使い切る前に投資枠が尽きている。")
        print("    この銘柄はリスク管理ではなく資金量で頭打ちになっている。")
        print(f"  ・ 理論株数と発注株数の差が単元の切り下げロス。100万円規模では")
        print("    ここで狙ったリスクの半分以下になることも珍しくない。")
    else:
        print("  ・ 単元未満株なので理論株数がそのまま発注株数になる。")
        print("    狙ったリスク%を正確に実現できるのが最大の利点。")
        print("  ・ 制約が「資金」の行は、リスクを使い切る前に投資枠が尽きている。")
    print()
    print("■ 100万円での実務ルール")
    print(f"  損切り幅が広い(10%)ほど株数は減る。エリオットの loss cut のように")
    print(f"  損切りが遠い手法は、100万円では株価の低い銘柄でしか成立しない。")
    print(f"  逆にデイトレのように損切りが近い(1〜2%)手法は、株数を大きく取れる。")
    print(f"  「100万円だから細かく刻む」ではなく「損切りが近い手法を選ぶ」が正解。")
    print()


# --------------------------------------------------------------------------
# §4 連敗耐性 — 定率リスクは幾何的に減るのでゼロにならない
# --------------------------------------------------------------------------

def max_streak_mc(p_win: float, trades: int, paths: int, seed: int) -> dict:
    """N取引の間に起きる最大連敗数の分布を実測する。"""
    rng = random.Random(seed)
    streaks = []
    for _ in range(paths):
        cur = best = 0
        for _ in range(trades):
            if rng.random() < p_win:
                cur = 0
            else:
                cur += 1
                best = max(best, cur)
        streaks.append(best)
    streaks.sort()
    q = lambda pp: streaks[min(int(pp * len(streaks)), len(streaks) - 1)]
    return {"median": q(0.5), "p90": q(0.9), "p99": q(0.99), "max": streaks[-1]}


def cmd_streak(args: argparse.Namespace) -> None:
    cap = args.capital
    print()
    print("=" * 78)
    print(" §4 連敗耐性 — 何連敗まで生き残れるか")
    print("=" * 78)
    print(f" 資金 {cap:,}円。1トレードのリスクを資金の定率にした場合、")
    print(" 負けるほど賭け金も減るため、資産は幾何級数的にしか減らない。")
    print(" これが「破産しない」の実装である。")
    print()
    print(f"{'ﾘｽｸ/回':>8} {'5連敗':>12} {'10連敗':>12} {'20連敗':>12} {'30連敗':>12} {'判定':>12}")
    print("-" * 78)
    for r in (0.5, 1.0, 2.0, 3.0, 5.0, 10.0):
        f = 1 - r / 100
        cells = "".join(f"{f ** k * cap:>11,.0f}円" for k in (5, 10, 20, 30))
        dd30 = (1 - f ** 30) * 100
        if dd30 < 20:
            v = "余裕"
        elif dd30 < 40:
            v = "耐えられる"
        elif dd30 < 60:
            v = "心が折れる"
        else:
            v = "再起不能"
        print(f"{r:>7.1f}% {cells} {v:>12}")
    print("-" * 78)
    print()

    print(f"■ 実際に何連敗するのか (勝率別 / {args.trades}取引の間の最大連敗数)")
    print(f"{'勝率':>8} {'中央値':>10} {'上位10%':>10} {'上位1%':>10} {'最悪':>8}")
    print("-" * 78)
    for p in (0.30, 0.40, 0.50, 0.55, 0.60):
        s = max_streak_mc(p, args.trades, args.paths, args.seed)
        print(f"{p * 100:>7.0f}% {s['median']:>9}連 {s['p90']:>9}連 {s['p99']:>9}連 {s['max']:>7}連")
    print("-" * 78)
    print()
    print("■ 読み方")
    print(f"  ・ 勝率40%の手法を{args.trades}回まわすと、10連敗前後は普通に起きる。")
    print("    「10連敗したから手法が壊れた」は誤り。設計通りの挙動である。")
    print("  ・ リスク1%なら10連敗しても資産は約90%残る。5%なら60%まで落ちる。")
    print("    差が出るのは勝率でも手法でもなく、1回あたりのリスクだけ。")
    print("  ・ 定率リスクは掛け算なので数学的にゼロにならない。")
    print("    ゼロになるのは、負けを取り返そうと金額を増やした時だけである。")
    print()
    print("■ 100万円の推奨値")
    print("  1トレードのリスクは 0.5〜1.0% (5,000〜10,000円)。")
    print("  理由は連敗耐性ではなく、§3で見た単元の切り下げロス。")
    print("  100万円でリスクを絞りすぎると発注株数が0になるため、")
    print("  単元株なら 1.0%、単元未満株なら 0.5% が下限の目安になる。")
    print()

# --------------------------------------------------------------------------
# §5 発注表 — reports/elliott の候補を実際の株数に変換する
# --------------------------------------------------------------------------

def clip(text: str, width: int) -> str:
    """全角を2桁と数えて幅を揃える。半角換算でないと表が崩れる。"""
    out, used = "", 0
    for ch in text:
        w = 2 if ord(ch) > 0x2E80 else 1
        if used + w > width:
            break
        out += ch
        used += w
    return out + " " * (width - used)


def latest_elliott(path: str) -> dict | None:
    files = sorted(glob.glob(os.path.join(path, "*.json")))
    if not files:
        return None
    return json.load(open(files[-1], encoding="utf-8"))


def cmd_order(args: argparse.Namespace) -> None:
    rep = latest_elliott(args.elliott)
    if not rep:
        print(f"\n{args.elliott}/ に候補がありません。先に elliott_wave3.py を実行してください。\n")
        return

    cap = args.capital
    budget = cap * args.invest_pct / 100.0
    picks = [p for p in rep["picks"] if p.get("entry_trigger") and p.get("stop")]

    print()
    print("=" * 78)
    print(f" §5 発注表 — {rep['data_date']} の候補を {cap:,}円 に落とし込む")
    print("=" * 78)
    print(f" リスク {args.risk:.2f}%/回 (= {cap * args.risk / 100:,.0f}円) / "
          f"投資枠 {budget:,.0f}円 / 単位 {args.unit}株")
    print()
    print(f"{'ｺｰﾄﾞ':>6} {'銘柄':<12} {'ｴﾝﾄﾘｰ':>9} {'損切り':>9} {'幅':>6} "
          f"{'株数':>8} {'投資額':>11} {'ﾘｽｸ':>7} {'制約':>6}")
    print("-" * 78)

    total_cost = total_risk = 0.0
    placed = 0
    for p in picks:
        entry, stop = float(p["entry_trigger"]), float(p["stop"])
        remaining = budget - total_cost
        r = size_position(cap, args.risk, entry, stop, max(remaining, 0), args.unit)
        name = clip(p["name"], 12)
        stop_pct = (entry - stop) / entry * 100
        if r.get("shares", 0) <= 0:
            reason = f"要{r.get('min_risk_pct', 0):.1f}%" if "min_risk_pct" in r else "枠なし"
            print(f"{p['code']:>6} {name} {entry:>8,.0f}円 {stop:>8,.0f}円 "
                  f"{stop_pct:>5.1f}% {'—':>8} {'—':>11} {'—':>7} {reason:>6}")
            continue
        total_cost += r["cost"]
        total_risk += r["loss"]
        placed += 1
        print(f"{p['code']:>6} {name} {entry:>8,.0f}円 {stop:>8,.0f}円 "
              f"{stop_pct:>5.1f}% {r['shares']:>7,}株 {r['cost']:>10,.0f}円 "
              f"{r['risk_pct']:>6.2f}% {r['binding']:>6}")

    print("-" * 78)
    print(f"{'合計':>6} {placed:>2}銘柄{'':<5} {'':>9} {'':>9} {'':>6} "
          f"{'':>8} {total_cost:>10,.0f}円 {total_risk / cap * 100:>6.2f}%")
    print()
    print(f"  投資額     : {total_cost:,.0f}円 ({total_cost / cap * 100:.1f}% / 枠 {args.invest_pct:.0f}%)")
    print(f"  現金       : {cap - total_cost:,.0f}円 ({(cap - total_cost) / cap * 100:.1f}%)")
    print(f"  全部損切り : -{total_risk:,.0f}円 ({total_risk / cap * 100:.2f}%)")
    print()
    print("  ※ エントリーは1波高値のブレイク待ち。到達しなければ発注しない。")
    print("  ※ 同じ日に全部が損切りに当たる前提で合計リスクを見ること。")
    print("     銘柄間の相関が高いので、これは悲観シナリオではなく普通に起きる。")
    if placed < 3:
        print(f"  ※ 発注できたのは {placed} 銘柄のみ。単元株の壁である。")
        print("     --unit 1 (単元未満株) で再計算すると全候補に分散できる。")
    print()


def cmd_single(args: argparse.Namespace) -> None:
    cap = args.capital
    budget = cap * args.invest_pct / 100.0
    r = size_position(cap, args.risk, args.entry, args.stop, budget, args.unit)
    print()
    print("=" * 78)
    print(f" 発注計算  {args.code or ''}  エントリー {args.entry:,.1f}円 / 損切り {args.stop:,.1f}円")
    print("=" * 78)
    if r.get("error"):
        print(f" {r['error']}")
        print()
        return
    if r["shares"] <= 0:
        print(f" 発注不可。1単元({args.unit}株)で {r['min_cost']:,.0f}円、")
        print(f" その時の損失は資金の {r['min_risk_pct']:.2f}% になり、狙った {args.risk:.2f}% を超える。")
        print()
        print(" 選択肢:")
        print(f"   ・ リスクを {r['min_risk_pct']:.2f}% まで許容する")
        print(f"   ・ 単元未満株で {int(cap * args.risk / 100 / (args.entry - args.stop))}株 だけ買う (--unit 1)")
        print("   ・ この銘柄を見送る")
        print()
        return
    print(f" 発注株数   : {r['shares']:,}株")
    print(f" 投資額     : {r['cost']:,.0f}円  (資金の {r['weight']:.1f}%)")
    print(f" 損切り時   : -{r['loss']:,.0f}円  (資金の {r['risk_pct']:.2f}%)")
    print(f" 損切り幅   : {r['stop_pct']:.2f}%")
    print(f" 効いた制約 : {r['binding']}")
    if r["waste"] > 5:
        print(f" 切り下げロス: {r['waste']:.1f}%  (単元に丸めた分、狙ったリスクより小さくなっている)")
    print()


def cmd_plan(args: argparse.Namespace) -> None:
    cap = args.capital
    print("=" * 78)
    print(f" 結論 — 資金 {cap:,}円 の運用設計")
    print("=" * 78)
    print()
    print(" 1. 現金比率を先に決める")
    print(f"    現物で持てるのは2銘柄程度。集中するとポートフォリオのボラが下がらず、")
    print(f"    ケリー比率は1.1倍前後にしかならない。ハーフケリーで約53%。")
    print(f"      → 投資枠 {cap * 0.5:,.0f}円 / 現金 {cap * 0.5:,.0f}円 から始める")
    print("      これは臆病なのではなく、賭けすぎを避けるための計算結果である。")
    print()
    print(" 2. 単元未満株を使う")
    print("    単元株100株だと、株価1,500円を超えた時点でリスク1%の発注ができない。")
    print("    S株/かぶミニ/ワン株なら1株単位なので、狙ったリスクを正確に置ける。")
    print(f"      → {cap:,}円で最も効く一手はこれ。手数料差より制約解除の価値が大きい")
    print()
    print(" 3. 1トレードのリスクは 0.5〜1.0%")
    print(f"      → 1回の損切りで {cap * 0.005:,.0f}〜{cap * 0.01:,.0f}円")
    print("    勝率40%なら10連敗は普通に起きる。1%なら資産は90%残り、")
    print("    5%だと60%まで落ちて手法を続けられなくなる。差は手法ではなくここ。")
    print()
    print(" 4. 損切りの近い手法を選ぶ")
    print("    株数 = リスク許容額 ÷ 損切り幅。損切りが遠い手法は株数が取れず、")
    print(f"    {cap:,}円では成立しない。エリオット(損切り10%前後)より")
    print("    デイトレ(損切り2%前後)の方が、この資金量とは相性が良い。")
    print()
    print(" 5. 増やすべきは資金ではなく銘柄数")
    print("    §2の表の通り、10銘柄まで分散すると投資枠は73%まで上げられる。")
    print(f"    同じ{cap:,}円でも、2銘柄集中と10銘柄分散では")
    print("    賭けてよい金額が533,333円と727,273円で1.4倍違う。")
    print()
    print(" ── 注意 ──")
    print(" 上の数字は全て「優位性が実在する」という前提の上に乗っている。")
    print(" holy_grail.py --edge の判定は現時点で【判断不能】(データ9日分)。")
    print(" 資金管理はマイナスの期待値をプラスには変えられない。破産までの")
    print(" 時間を延ばすだけである。まず優位性の検定を通すこと。")
    print()
    print("=" * 78)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="資金100万円で実際に何株買うかを決める",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="引数なしで §1〜§4 と結論をまとめて表示します。",
    )
    parser.add_argument("--capital", type=int, default=1_000_000, metavar="円", help="運用資金 (デフォルト: 1000000)")
    parser.add_argument("--risk", type=float, default=1.0, metavar="%%", help="1トレードのリスク%% (デフォルト: 1.0)")
    parser.add_argument("--invest-pct", type=float, default=53.0, metavar="%%", help="投資枠%% (デフォルト: 53 = 2銘柄時のハーフケリー)")
    parser.add_argument("--unit", type=int, default=UNIT, metavar="株", help="売買単位。単元未満株なら 1 (デフォルト: 100)")

    parser.add_argument("--wall", action="store_true", help="§1 単元株の壁")
    parser.add_argument("--cash", action="store_true", help="§2 現金比率")
    parser.add_argument("--size", action="store_true", help="§3 発注数量の早見表")
    parser.add_argument("--streak", action="store_true", help="§4 連敗耐性")
    parser.add_argument("--order", action="store_true", help="§5 reports/elliott の候補を発注表にする")

    parser.add_argument("--code", metavar="ｺｰﾄﾞ", help="単発計算する銘柄コード")
    parser.add_argument("--entry", type=float, metavar="円", help="単発計算のエントリー価格")
    parser.add_argument("--stop", type=float, metavar="円", help="単発計算の損切り価格")

    parser.add_argument("--mu", type=float, default=0.05, metavar="%%", help="§2 1銘柄1日の平均リターン%%")
    parser.add_argument("--sd", type=float, default=2.5, metavar="%%", help="§2 1銘柄1日の標準偏差%%")
    parser.add_argument("--rho", type=float, default=0.5, metavar="値", help="§2 銘柄間の相関")
    parser.add_argument("--lot-cost", type=int, default=461_000, metavar="円", help="§2 1単元の中央値")

    parser.add_argument("--trades", type=int, default=200, metavar="回数", help="§4 連敗を数える取引数")
    parser.add_argument("--paths", type=int, default=3000, metavar="本数", help="§4 モンテカルロ本数")
    parser.add_argument("--elliott", default="reports/elliott", metavar="ﾃﾞｨﾚｸﾄﾘ", help="§5 候補の保存先")
    parser.add_argument("--seed", type=int, default=42, metavar="値", help="乱数シード")
    args = parser.parse_args()

    if args.entry and args.stop:
        cmd_single(args)
        return

    picked = args.wall or args.cash or args.size or args.streak or args.order
    if not picked:
        args.wall = args.cash = args.size = args.streak = True

    if args.wall:
        cmd_wall(args)
    if args.cash:
        cmd_cash(args)
    if args.size:
        cmd_size(args)
    if args.streak:
        cmd_streak(args)
    if args.order:
        cmd_order(args)
    if not picked:
        cmd_plan(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n中断しました。")
