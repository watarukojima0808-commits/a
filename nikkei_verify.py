#!/usr/bin/env python3
"""過去に選んだ候補で実際に売買したらどうなったかを検証する。

reports/ に溜まった選定結果を読み、各銘柄を「翌営業日の寄付で買って引けで売る」
という単純なルールで評価し、日経平均を同じルールで売買した場合と比較する。

    python nikkei_verify.py
    python nikkei_verify.py --top 3 --cost-pct 0.2
    python nikkei_verify.py --detail
"""

import argparse
import glob
import json
import os
import re
import sys
from datetime import date, datetime

from nikkei_daytrade import JST, Bar, fetch_bars, fetch_symbol

NIKKEI_SYMBOL = "^N225"


# --------------------------------------------------------------------------
# 選定結果の読み込み
# --------------------------------------------------------------------------

# Markdown表の行:  | 1 | 5802 | 住友電気工業 | 2,184 | +3.5% | ...
MD_ROW = re.compile(r"^\|\s*\d+\s*\|\s*(\d{4})\s*\|\s*([^|]+?)\s*\|\s*([\d,]+)\s*\|")
MD_DATE = re.compile(r"参照データ:\s*(\d{4}-\d{2}-\d{2})")


def load_markdown(path: str) -> dict | None:
    """JSONが無い古いレポート用のフォールバック。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()

    m = MD_DATE.search(text)
    if not m:
        return None

    picks = []
    for line in text.splitlines():
        row = MD_ROW.match(line)
        if row:
            picks.append({
                "code": row.group(1),
                "name": row.group(2),
                "close": float(row.group(3).replace(",", "")),
            })
    return {"data_date": m.group(1), "picks": picks} if picks else None


def load_reports(out_dir: str) -> list[dict]:
    """同じ日にJSONとMarkdownが両方あればJSONを優先する。"""
    reports: dict[str, dict] = {}

    for path in sorted(glob.glob(os.path.join(out_dir, "*.md"))):
        parsed = load_markdown(path)
        if parsed:
            reports[parsed["data_date"]] = parsed

    for path in sorted(glob.glob(os.path.join(out_dir, "*.json"))):
        with open(path, encoding="utf-8") as f:
            parsed = json.load(f)
        if parsed.get("picks"):
            reports[parsed["data_date"]] = parsed

    return [reports[k] for k in sorted(reports)]


# --------------------------------------------------------------------------
# 損益計算
# --------------------------------------------------------------------------

def next_bar(bars: list[Bar], after: date) -> Bar | None:
    """基準日の次に来る営業日の足。祝日や連休はそのまま飛ばされる。"""
    for b in bars:
        if b.date > after:
            return b
    return None


def evaluate(pick: dict, bars: list[Bar], base: date, cost_pct: float) -> dict | None:
    bar = next_bar(bars, base)
    if bar is None or bar.o <= 0:
        return None

    # 寄付で全額買い、引けで全部売る。日をまたがないので最も単純で再現しやすい。
    gross = (bar.c / bar.o - 1) * 100
    return {
        "code": pick["code"],
        "name": pick["name"],
        "trade_date": bar.date,
        "open": bar.o,
        "close": bar.c,
        "ret": gross - cost_pct,
        "gap": (bar.o / pick["close"] - 1) * 100 if pick.get("close") else 0.0,
        "max_up": (bar.h / bar.o - 1) * 100,
        "max_dn": (bar.l / bar.o - 1) * 100,
    }


def summarize(trades: list[dict]) -> dict:
    rets = [t["ret"] for t in trades]
    wins = [r for r in rets if r > 0]
    return {
        "n": len(rets),
        "win_rate": len(wins) / len(rets) * 100 if rets else 0.0,
        "wins": len(wins),
        "losses": len(rets) - len(wins),
        "avg": sum(rets) / len(rets) if rets else 0.0,
        "best": max(rets) if rets else 0.0,
        "worst": min(rets) if rets else 0.0,
    }


# --------------------------------------------------------------------------
# 表示
# --------------------------------------------------------------------------

def pad(text: str, width: int) -> str:
    used = sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
    return text + " " * max(width - used, 0)


def print_results(by_date: list[tuple[str, list[dict], float]], cost_pct: float, detail: bool) -> None:
    all_trades = [t for _, ts, _ in by_date for t in ts]
    print()
    print("=" * 78)
    print(f" 検証結果  レポート {len(by_date)} 日分 / 延べ {len(all_trades)} 取引"
          f"  (往復コスト {cost_pct:.2f}% 控除後)")
    print("=" * 78)
    print(" ルール: 選定翌営業日の寄付で等金額買い、その日の引けで売る")
    print()

    if detail:
        print(f"{'売買日':<11} {'コード':<6} {'銘柄':<14} {'寄付':>8} {'引け':>8} "
              f"{'ｷﾞｬｯﾌﾟ':>8} {'損益':>7} {'最大益':>7} {'最大損':>7}")
        print("-" * 86)
        for _, trades, _ in by_date:
            for t in trades:
                print(
                    f"{t['trade_date']:%Y-%m-%d}  {t['code']:<6} {pad(t['name'][:12], 14)}"
                    f"{t['open']:>8,.0f} {t['close']:>8,.0f} {t['gap']:>+7.2f}% "
                    f"{t['ret']:>+6.2f}% {t['max_up']:>+6.2f}% {t['max_dn']:>+6.2f}%"
                )
        print("-" * 86)
        print("  ｷﾞｬｯﾌﾟ = 選定時の終値から寄付までの変動。ここが大きい日は美味しい所を寄前に取られている。")
        print()

    print(f"{'売買日':<12} {'銘柄数':>6} {'平均損益':>9} {'勝率':>7} {'日経平均':>9} {'差':>8}")
    print("-" * 78)
    for _, trades, bench in by_date:
        s = summarize(trades)
        diff = s["avg"] - bench
        day = f"{trades[0]['trade_date']:%Y-%m-%d}"
        print(f"{day:<12} {s['n']:>6} {s['avg']:>+8.2f}% {s['win_rate']:>6.0f}% "
              f"{bench:>+8.2f}% {diff:>+7.2f}pt")
    print("-" * 78)

    s = summarize(all_trades)
    avg_bench = sum(b for _, _, b in by_date) / len(by_date)
    print()
    print("■ 全体")
    print(f"  取引数      : {s['n']} 件 ({s['wins']}勝 {s['losses']}敗)")
    print(f"  勝率        : {s['win_rate']:.1f}%")
    print(f"  平均損益    : {s['avg']:+.3f}% / 取引")
    print(f"  最大益/最大損: {s['best']:+.2f}% / {s['worst']:+.2f}%")
    print()
    print("■ 日経平均を同じルールで売買した場合との比較")
    print(f"  日経平均    : {avg_bench:+.3f}% / 日")
    print(f"  差          : {s['avg'] - avg_bench:+.3f}pt")
    print()

    verdict(s, avg_bench, len(by_date))


def verdict(s: dict, bench: float, days: int) -> None:
    """判断材料としてどこまで信用してよいかを明示する。"""
    edge = s["avg"] - bench
    print("■ 判定")

    if days < 20:
        print(f"  【判断保留】検証日数が {days} 日しかありません。")
        print("  20日、できれば60日ためてから判断してください。")
        print("  今の数字は運の影響が大きく、良くても悪くても意味がありません。")
    elif edge <= 0:
        print(f"  【見送り】日経平均を {abs(edge):.3f}pt 下回っています。")
        print("  このスコアで銘柄を選ぶ意味は今のところ見えません。")
    elif s["avg"] <= 0:
        print("  【見送り】日経平均には勝っていますが、平均損益がマイナスです。")
        print("  相場が下げた日に損が小さかっただけで、儲かってはいません。")
    else:
        print(f"  【継続検証】平均 {s['avg']:+.3f}% / 日経平均比 {edge:+.3f}pt。")
        print("  ただし損切りルールが無い状態の数字です。実弾を入れる前に、")
        print("  最大損 の列を見て許容できる下げ幅か必ず確認してください。")
    print()


# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="過去の選定結果を実際の株価で検証する",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--reports", default="reports", metavar="ディレクトリ", help="レポートの保存先 (デフォルト: reports)")
    parser.add_argument("--top", type=int, default=0, metavar="件数", help="各日の上位N銘柄だけ検証する (デフォルト: 全件)")
    parser.add_argument("--cost-pct", type=float, default=0.1, metavar="率", help="往復の手数料+スリッページ%% (デフォルト: 0.1)")
    parser.add_argument("--detail", action="store_true", help="1取引ずつ明細を表示する")
    args = parser.parse_args()

    reports = load_reports(args.reports)
    if not reports:
        sys.exit(f"{args.reports}/ に選定結果が見つかりません。先に nikkei_daytrade.py を実行してください。")

    codes = sorted({p["code"] for r in reports for p in r["picks"][: args.top or None]})
    print(f"レポート {len(reports)} 日分 / {len(codes)} 銘柄の株価を照合中...")

    bars_by_code: dict[str, list[Bar]] = {}
    for i, code in enumerate(codes, 1):
        try:
            bars_by_code[code] = fetch_bars(code)
        except Exception:
            bars_by_code[code] = []
        if i % 10 == 0 or i == len(codes):
            print(f"  {i}/{len(codes)} 完了", end="\r", flush=True)
    print()

    try:
        nikkei = fetch_symbol(NIKKEI_SYMBOL)
    except Exception:
        nikkei = []
    if not nikkei:
        sys.exit("日経平均の株価を取得できませんでした。比較対象なしでは判断できないため中止します。")

    by_date = []
    for rep in reports:
        base = datetime.strptime(rep["data_date"], "%Y-%m-%d").date()
        trades = []
        for pick in rep["picks"][: args.top or None]:
            bars = bars_by_code.get(pick["code"])
            if not bars:
                continue
            t = evaluate(pick, bars, base, args.cost_pct)
            if t:
                trades.append(t)

        bench_bar = next_bar(nikkei, base)
        # 指数はコスト控除しない。銘柄選定に意味があるかだけを見たいため。
        bench = (bench_bar.c / bench_bar.o - 1) * 100 if bench_bar and bench_bar.o else 0.0
        if trades:
            by_date.append((rep["data_date"], trades, bench))

    if not by_date:
        # レポートを出した当日など、まだ翌営業日が来ていないだけの状態。
        # 異常ではないので、赤い失敗にはせず理由だけ伝えて終わる。
        latest = reports[-1]["data_date"]
        print()
        print("まだ検証できる取引がありません。")
        print(f"  最新レポートの基準日 : {latest}")
        print("  この翌営業日の取引が終わってから、もう一度実行してください。")
        print(f"  溜まっているレポート : {len(reports)} 日分 (判定には20日分が必要です)")
        print()
        return

    print_results(by_date, args.cost_pct, args.detail)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n中断しました。")
