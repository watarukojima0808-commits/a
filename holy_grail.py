#!/usr/bin/env python3
"""投資の「聖杯」を数式で追い詰めるための検証エンジン。

このリポジトリのスクリーナーが吐いた reports/ を使い、
「その選定に本物の優位性(エッジ)があるか」を統計的に判定する。

結論を先に書いておく。確実に勝てる売買ルールは存在しない。理由は3つある。

  1. 見せかけの優位性はいくらでも作れる (多重検定)
     ノイズだけのデータでも200通り試せば、シャープレシオ2.7の
     「素晴らしいバックテスト」が必ず1本は出てくる。--trap で実演する。

  2. 本物の優位性は小さく、証明に必要な取引数が膨大 (検出力)
     1取引あたりシャープ0.05のエッジを95%の確度で証明するには
     約4300取引が要る。12日分のレポートで分かることは何もない。

  3. 優位性があっても賭け方を誤ると破産する (資金管理)
     期待値プラスでも、ケリー比率の2倍を賭けると資産は0に収束する。
     これは運ではなく確定した数学。--risk で実演する。

では何が聖杯か。この3つを裏返したものが聖杯であり、それは単一の売買ルール
ではなく「手順」である。

  A. 小さいエッジを、多重検定を補正した上で見分ける      → --edge / --trap
  B. 破産しない賭け金で、複利が最大になる所に置く         → --risk
  C. 相関の低いエッジを複数束ねる (唯一のタダ飯)          → --mix

    python holy_grail.py                # 全部まとめて実行 (--edge は要ネット)
    python holy_grail.py --edge         # reports/ の選定を統計検定する
    python holy_grail.py --trap         # 多重検定の罠を実演する (オフライン)
    python holy_grail.py --risk         # 破産確率とケリー比率 (オフライン)
    python holy_grail.py --mix          # 分散によるシャープ改善 (オフライン)
    python holy_grail.py --power        # 必要取引数の早見表 (オフライン)
"""

import argparse
import math
import random

# --------------------------------------------------------------------------
# 統計プリミティブ
#
# 標準ライブラリだけで、正規分布・t分布の分位点と裾確率を出せるようにする。
# scipy が使えないと Deflated Sharpe Ratio が計算できないため、ここは自前で持つ。
# --------------------------------------------------------------------------

EULER = 0.5772156649015329


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """標準正規分布の分位点 (Acklam の有理近似 + Halley 法で1回補正)。"""
    if not 0.0 < p < 1.0:
        raise ValueError("p は 0 と 1 の間である必要があります")

    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)

    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    elif p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    else:
        q = p - 0.5
        r = q * q
        x = (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)

    # 精度を 1e-15 まで上げる。DSR は裾を使うのでここをケチると数値が崩れる。
    e = 0.5 * math.erfc(-x / math.sqrt(2)) - p
    u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


def _betacf(a: float, b: float, x: float) -> float:
    """正則不完全ベータ関数の連分数展開 (Lentz 法)。"""
    maxit, eps, fpmin = 300, 3.0e-16, 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lb = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
          + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return math.exp(lb) * _betacf(a, b, x) / a
    return 1.0 - math.exp(lb) * _betacf(b, a, 1.0 - x) / b


def t_pvalue(t: float, df: float) -> float:
    """両側 p 値。df が小さい時に正規近似で誤魔化さないよう t 分布で厳密に出す。"""
    if df <= 0:
        return 1.0
    return betainc(df / 2.0, 0.5, df / (df + t * t))


def moments(xs: list[float]) -> tuple[float, float, float, float]:
    """平均・標準偏差(不偏)・歪度・尖度(生。正規分布なら3)。"""
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else 0.0), 0.0, 0.0, 3.0
    mu = sum(xs) / n
    m2 = sum((x - mu) ** 2 for x in xs) / n
    if m2 <= 0:
        return mu, 0.0, 0.0, 3.0
    m3 = sum((x - mu) ** 3 for x in xs) / n
    m4 = sum((x - mu) ** 4 for x in xs) / n
    sd = math.sqrt(m2 * n / (n - 1))
    return mu, sd, m3 / m2 ** 1.5, m4 / m2 ** 2


# --------------------------------------------------------------------------
# シャープレシオまわり (Bailey & Lopez de Prado)
#
# 生のシャープレシオは「何回試したか」を無視した数字なので、そのままでは
# 判断に使えない。試行回数と分布の歪みで割り引いたものが DSR。
# --------------------------------------------------------------------------

def sharpe(xs: list[float]) -> float:
    """1取引あたりのシャープレシオ (年率化しない生の値)。"""
    mu, sd, _, _ = moments(xs)
    return mu / sd if sd > 0 else 0.0


def sharpe_se(sr: float, n: int, skew: float, kurt: float) -> float:
    """シャープレシオ自体の標準誤差。歪度・尖度が効くのがミソ。"""
    if n < 2:
        return float("inf")
    var = (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) / (n - 1)
    return math.sqrt(max(var, 1e-18))


def psr(sr: float, sr_star: float, n: int, skew: float, kurt: float) -> float:
    """Probabilistic Sharpe Ratio。真のシャープが sr_star を超える確率。"""
    se = sharpe_se(sr, n, skew, kurt)
    if not math.isfinite(se) or se <= 0:
        return 0.5
    return norm_cdf((sr - sr_star) / se)


def expected_max_sharpe(trials: int, sr_sd: float) -> float:
    """エッジがゼロでも、N回試せばこれくらいのシャープが「出てしまう」という値。"""
    if trials < 2:
        return 0.0
    z1 = norm_ppf(1.0 - 1.0 / trials)
    z2 = norm_ppf(1.0 - 1.0 / (trials * math.e))
    return sr_sd * ((1.0 - EULER) * z1 + EULER * z2)


def deflated_sharpe(xs: list[float], trials: int) -> tuple[float, float]:
    """Deflated Sharpe Ratio と、その基準となる「まぐれの閾値」を返す。"""
    n = len(xs)
    sr = sharpe(xs)
    _, _, skew, kurt = moments(xs)
    # 試行間のシャープのばらつきは、1本の系列から推定するしかないので
    # 漸近標準誤差で代用する。控えめな見積もりになる点は注意。
    sr_sd = sharpe_se(sr, n, skew, kurt)
    threshold = expected_max_sharpe(trials, sr_sd)
    return psr(sr, threshold, n, skew, kurt), threshold


def min_track_record(sr: float, sr_star: float, skew: float, kurt: float,
                     conf: float = 0.95) -> float:
    """そのエッジを conf の確度で証明するのに必要な最小取引数。"""
    if sr <= sr_star:
        return float("inf")
    return 1.0 + (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) * \
        (norm_ppf(conf) / (sr - sr_star)) ** 2


def bootstrap_ci(xs: list[float], reps: int = 20000, conf: float = 0.95,
                 seed: int = 42) -> tuple[float, float]:
    """平均のブートストラップ信頼区間。正規性を仮定しないので裾が厚くても壊れない。"""
    if len(xs) < 2:
        return 0.0, 0.0
    rng = random.Random(seed)
    n = len(xs)
    means = []
    for _ in range(reps):
        means.append(sum(xs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int((1 - conf) / 2 * reps)]
    hi = means[min(int((1 + conf) / 2 * reps), reps - 1)]
    return lo, hi


# --------------------------------------------------------------------------
# §1 エッジ検定 — reports/ の選定に本物の優位性があるか
#
# 重要な注意: 同じ日に選んだ10銘柄は独立ではない。地合いが良ければ全部上がる。
# 「120取引あるから n=120」と数えると t 値が実力の3倍に膨らむ。
# ここでは1日1観測 (等金額バスケットの日次損益) を正とし、
# 取引ベースの数字は「そう数えるとどれだけ嘘が大きくなるか」の対比で出す。
# --------------------------------------------------------------------------

TRADING_DAYS = 245


def collect_series(reports_dir: str, top: int, cost_pct: float) -> dict:
    """reports/ と実際の株価から、日次のリターン系列を組み立てる。"""
    from datetime import datetime

    from nikkei_daytrade import Bar, fetch_bars, fetch_symbol
    from nikkei_verify import NIKKEI_SYMBOL, evaluate, load_reports, next_bar

    reports = load_reports(reports_dir)
    if not reports:
        raise SystemExit(f"{reports_dir}/ に選定結果がありません。")

    codes = sorted({p["code"] for r in reports for p in r["picks"][: top or None]})
    print(f"レポート {len(reports)} 日分 / {len(codes)} 銘柄を照合中...")

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
        raise SystemExit("日経平均を取得できませんでした。比較対象なしでは検定できません。")

    days, trades, excess = [], [], []
    for rep in reports:
        base = datetime.strptime(rep["data_date"], "%Y-%m-%d").date()
        day_trades = []
        for pick in rep["picks"][: top or None]:
            bars = bars_by_code.get(pick["code"])
            if not bars:
                continue
            t = evaluate(pick, bars, base, cost_pct)
            if t:
                day_trades.append(t)
        if not day_trades:
            continue
        bench_bar = next_bar(nikkei, base)
        bench = (bench_bar.c / bench_bar.o - 1) * 100 if bench_bar and bench_bar.o else 0.0
        basket = sum(t["ret"] for t in day_trades) / len(day_trades)
        days.append(basket)
        excess.append(basket - bench)
        trades.extend(t["ret"] for t in day_trades)

    if not days:
        raise SystemExit("まだ検証できる取引がありません。翌営業日の引け後に再実行してください。")

    return {"days": days, "trades": trades, "excess": excess, "n_reports": len(reports)}


def report_stat_block(label: str, xs: list[float], trials: int, unit: str = "日") -> dict:
    """1本の系列を、生の数字 → 検定 → 割引後の数字 の順に潰していく。"""
    n = len(xs)
    mu, sd, skew, kurt = moments(xs)
    sr = sharpe(xs)
    ann = sr * math.sqrt(TRADING_DAYS) if unit == "日" else sr
    t = mu / (sd / math.sqrt(n)) if sd > 0 and n > 1 else 0.0
    p = t_pvalue(t, n - 1) if n > 1 else 1.0
    lo, hi = bootstrap_ci(xs)
    dsr, thr = deflated_sharpe(xs, trials)
    trl = min_track_record(sr, thr, skew, kurt)

    print(f"■ {label}   n = {n} {unit}")
    print(f"  平均            : {mu:+.4f}% / {unit}")
    print(f"  標準偏差        : {sd:.4f}%")
    print(f"  歪度 / 尖度     : {skew:+.2f} / {kurt:.2f}   (正規なら 0.00 / 3.00)")
    print(f"  シャープ({unit})    : {sr:+.4f}" + (f"   年率換算 {ann:+.2f}" if unit == "日" else ""))
    print(f"  t 値            : {t:+.3f}")
    print(f"  p 値 (両側)     : {p:.4f}" + ("   ← 有意水準5%を満たす" if p < 0.05 else "   ← 有意ではない"))
    print(f"  平均の95%区間   : [{lo:+.4f}% , {hi:+.4f}%]"
          + ("   ← 0 をまたぐ" if lo <= 0 <= hi else ""))
    print()
    print(f"  ── 多重検定を {trials} 回として割り引くと ──")
    print(f"  まぐれの閾値    : シャープ {thr:+.4f} / {unit}  (エッジ0でもここまでは出る)")
    print(f"  DSR             : {dsr * 100:.1f}%   (本物である確率。95%未満は採用不可)")

    # まぐれの閾値は sqrt(n) で縮む。n が小さいうちは閾値の方が高くて
    # 「到達不能」になるが、これは戦略が駄目なのではなく検証量が足りないだけ。
    plain = min_track_record(sr, 0.0, skew, kurt)
    if math.isfinite(trl):
        print(f"  必要 {unit}数        : {trl:,.0f} {unit}  (多重検定を補正した上で95%の確度を得るのに要る量)")
    else:
        print(f"  必要 {unit}数        : 現時点では算出不能")
        print(f"                    実測シャープ {sr:+.4f} がまぐれの閾値 {thr:+.4f} を下回っている。")
        print(f"                    閾値は sqrt(n) で縮むので、{unit}数を増やせば下がる。")
    if math.isfinite(plain):
        print(f"  (参考) 補正なし  : {plain:,.0f} {unit}  (試行1回だったと仮定した場合の必要量)")
    print()
    return {"n": n, "mu": mu, "sr": sr, "t": t, "p": p, "dsr": dsr, "trl": trl,
            "lo": lo, "hi": hi, "sd": sd}


def cmd_edge(args: argparse.Namespace) -> None:
    print()
    print("=" * 78)
    print(" §1 エッジ検定 — この選定に本物の優位性はあるか")
    print("=" * 78)
    print(" ルール: 選定翌営業日の寄付で等金額買い、その日の引けで売る")
    print(f" 往復コスト {args.cost_pct:.2f}% 控除後 / 上位 {args.top or '全'}銘柄")
    print()

    s = collect_series(args.reports, args.top, args.cost_pct)
    print()

    a = report_stat_block("日次バスケット損益 (これが正しい数え方)", s["days"], args.trials)
    b = report_stat_block("日経平均に対する超過収益", s["excess"], args.trials)

    print("■ 数え方を間違えるとどうなるか")
    mu_t, sd_t, _, _ = moments(s["trades"])
    n_t = len(s["trades"])
    t_fake = mu_t / (sd_t / math.sqrt(n_t)) if sd_t > 0 and n_t > 1 else 0.0
    print(f"  取引を独立と誤って数えた場合 : n = {n_t} 取引, t = {t_fake:+.3f}, "
          f"p = {t_pvalue(t_fake, n_t - 1):.4f}")
    print(f"  正しく日単位で数えた場合     : n = {a['n']} 日, t = {a['t']:+.3f}, p = {a['p']:.4f}")
    print(f"  t 値が {abs(t_fake) / abs(a['t']) if a['t'] else float('inf'):.1f} 倍に水増しされている。")
    print("  同じ日の銘柄は地合いを共有するため独立ではない。ここを間違えた")
    print("  バックテストが、世に出回る「勝率90%の手法」の正体である。")
    print()

    print("■ 判定")
    n_days = a["n"]
    if n_days < 30:
        print(f"  【判断不能】{n_days} 日分では統計的に何も言えない。")
        print(f"  必要量の目安は上の「必要日数」を参照。今の勝ち負けは全て運の範囲。")
    elif b["dsr"] < 0.95:
        print(f"  【不採用】超過収益の DSR が {b['dsr'] * 100:.1f}% で、95% に届かない。")
        print("  まぐれで説明できてしまう水準。実弾を入れる根拠にならない。")
    elif b["mu"] <= 0:
        print("  【不採用】日経平均を上回っていない。指数を買った方がよい。")
    else:
        print(f"  【条件付き採用】超過収益 {b['mu']:+.4f}%/日, DSR {b['dsr'] * 100:.1f}%。")
        print("  多重検定を補正しても優位性が残っている。ただし次は資金管理の問題。")
        print("  --risk で、このエッジをどう賭ければ破産しないかを確認すること。")
    print()


# --------------------------------------------------------------------------
# §2 多重検定の罠 — エッジゼロでも美しいバックテストは必ず作れる
# --------------------------------------------------------------------------

def cmd_trap(args: argparse.Namespace) -> None:
    print()
    print("=" * 78)
    print(" §2 多重検定の罠 — 優位性ゼロのデータから「聖杯」を捏造する")
    print("=" * 78)
    print(" 完全なランダム(期待値ちょうど0)の売買を N 通り試し、")
    print(" 一番成績の良かった1本だけを取り出して、その見た目を確認する。")
    print()

    rng = random.Random(args.seed)
    n = args.periods
    print(f"{'試行数':>8} {'最高ｼｬｰﾌﾟ(年率)':>17} {'年率ﾘﾀｰﾝ':>12} {'t値':>8} {'p値':>9} {'DSR':>8}")
    print("-" * 78)

    for trials in args.trap_trials:
        srs, best_sr, best_series = [], -1e9, None
        for _ in range(trials):
            xs = [rng.gauss(0.0, 1.0) for _ in range(n)]
            sr = sharpe(xs)
            srs.append(sr)
            if sr > best_sr:
                best_sr, best_series = sr, xs
        mu, sd, skew, kurt = moments(best_series)
        ann_sr = best_sr * math.sqrt(TRADING_DAYS)
        ann_ret = mu * TRADING_DAYS
        t = mu / (sd / math.sqrt(n))
        p = t_pvalue(t, n - 1)
        # 試行間のシャープのばらつきは、ここでは全試行が手元にあるので実測できる。
        # 本番のバックテストでも、捨てた戦略の成績を残しておけば同じ計算ができる。
        sr_sd = moments(srs)[1] if len(srs) > 1 else sharpe_se(best_sr, n, skew, kurt)
        thr = expected_max_sharpe(trials, sr_sd)
        dsr = psr(best_sr, thr, n, skew, kurt)
        print(f"{trials:>8} {ann_sr:>17.2f} {ann_ret:>11.1f}% {t:>8.2f} {p:>9.4f} {dsr * 100:>7.1f}%")

    print("-" * 78)
    print(f"  ({n} 期間 / 1期間のボラを1%として年率換算)")
    print()
    print("■ 読み方")
    print("  左の列が増えるほど、右の「成績」は良くなる。中身は全部ただの乱数で、")
    print("  期待値は厳密に 0 である。それでも p 値は 0.01 を切り、シャープ2超えの")
    print("  右肩上がりの資産曲線が手に入る。")
    print()
    print("  一番右の DSR だけが、試行回数を知った上での正直な確率を返している。")
    print("  DSR は全ての行で低いまま。これが多重検定補正の効き目である。")
    print()
    print("■ ここから導かれる聖杯の第一条件")
    print("  「試した回数を数え、それで割り引いた後にも残る優位性だけを信じる」")
    print("  パラメータを変えて試した回数、諦めた戦略の数、全部が試行回数に入る。")
    print("  数えていないなら、そのバックテストの結果は解釈不能である。")
    print()


# --------------------------------------------------------------------------
# §3 検出力 — 本物のエッジを証明するのに必要な取引数
# --------------------------------------------------------------------------

def cmd_power(args: argparse.Namespace) -> None:
    print()
    print("=" * 78)
    print(" §3 検出力 — その優位性、証明するのに何取引必要か")
    print("=" * 78)
    print(" 有意水準5%(両側) / 検出力80% で必要な観測数。")
    print(" n = ((z(0.975) + z(0.80)) / シャープ)^2")
    print()

    z = norm_ppf(0.975) + norm_ppf(0.80)
    print(f"{'1取引シャープ':>14} {'年率シャープ':>13} {'必要取引数':>12} {'日1回なら':>12} {'現実味':>10}")
    print("-" * 78)
    for sr in (0.01, 0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50):
        n = (z / sr) ** 2
        years = n / TRADING_DAYS
        if years > 20:
            verdict = "検証不能"
        elif years > 5:
            verdict = "一生かかる"
        elif years > 1:
            verdict = "数年"
        else:
            verdict = "現実的"
        print(f"{sr:>14.2f} {sr * math.sqrt(TRADING_DAYS):>13.2f} {n:>12,.0f} "
              f"{years:>11.1f}年 {verdict:>10}")
    print("-" * 78)
    print()
    print("■ 読み方")
    print("  実在する優位性のほとんどは1取引シャープ 0.02〜0.05 の領域にある。")
    print("  つまり証明には数千取引、日次なら数十年分のデータが要る。")
    print("  逆に「3ヶ月で確認できた優位性」は、大きすぎて実在を疑うべき水準にある。")
    print()
    print("■ ここから導かれる聖杯の第二条件")
    print("  「取引頻度を上げる」。1取引の優位性は増やせないが、回数は増やせる。")
    print("  年率シャープは 1取引シャープ × sqrt(取引回数) で伸びる。")
    print("  同じ 0.02 のエッジでも、年12回なら年率0.07、年2450回なら年率0.99になる。")
    print()

# --------------------------------------------------------------------------
# §4 資金管理 — 優位性があっても賭け方を誤れば0になる
#
# ここが本当の聖杯の在り処。エッジの大きさは市場が決めるが、
# 賭け金の大きさは自分で決められる。唯一こちらが完全に支配できる変数である。
# --------------------------------------------------------------------------

def kelly_fraction(mu: float, sd: float) -> float:
    """連続リターン近似のケリー比率 f* = μ / σ^2 (μ, σ は小数)。"""
    return mu / (sd * sd) if sd > 0 else 0.0


def growth_rate(mu: float, sd: float, f: float) -> float:
    """対数資産の期待成長率 g(f) = fμ - (fσ)^2 / 2。f=2f* でちょうど 0 になる。"""
    return f * mu - (f * sd) ** 2 / 2.0


def simulate_paths(mu: float, sd: float, f: float, n: int, paths: int,
                   seed: int) -> dict:
    """複利で回した時の最終資産・最大ドローダウン・破産確率を実測する。"""
    rng = random.Random(seed)
    finals, maxdds = [], []
    ruin = half = 0
    for _ in range(paths):
        eq, peak, mdd = 1.0, 1.0, 0.0
        dead = False
        for _ in range(n):
            r = rng.gauss(mu, sd)
            eq *= 1.0 + f * r
            if eq <= 0.0:
                eq, dead = 1e-12, True
                break
            peak = max(peak, eq)
            mdd = max(mdd, 1.0 - eq / peak)
        finals.append(eq)
        maxdds.append(mdd)
        if dead or eq < 0.10:
            ruin += 1
        if mdd >= 0.5:
            half += 1
    finals.sort()
    maxdds.sort()
    q = lambda xs, p: xs[min(int(p * len(xs)), len(xs) - 1)]
    return {
        "median": q(finals, 0.50), "p05": q(finals, 0.05), "p95": q(finals, 0.95),
        "mdd_med": q(maxdds, 0.50), "mdd_p95": q(maxdds, 0.95),
        "ruin": ruin / paths, "half": half / paths,
    }


def cmd_risk(args: argparse.Namespace) -> None:
    mu = args.mu / 100.0
    sd = args.sd / 100.0
    n = args.trades
    f_star = kelly_fraction(mu, sd)
    sr = mu / sd if sd > 0 else 0.0

    print()
    print("=" * 78)
    print(" §4 資金管理 — エッジが本物でも、賭け方だけで結果は0にも無限にもなる")
    print("=" * 78)
    print(f" 前提: 1取引あたり 平均 {args.mu:+.3f}% / 標準偏差 {args.sd:.3f}% / {n:,} 取引")
    print(f"       1取引シャープ {sr:.4f}  (年率 {sr * math.sqrt(TRADING_DAYS):.2f} 相当)")
    print()
    print(f" ケリー比率 f* = μ/σ² = {f_star:.2f} 倍 (資産に対するレバレッジ)")
    print(f" 最大成長率 g(f*) = SR²/2 = {growth_rate(mu, sd, f_star) * 100:.4f}% / 取引")
    print(f"                          = 年 {(math.exp(growth_rate(mu, sd, f_star) * TRADING_DAYS) - 1) * 100:.1f}%")
    print()
    print(f" モンテカルロ {args.paths:,} 本で実測する:")
    print()
    print(f"{'賭け金':>10} {'ﾚﾊﾞﾚｯｼﾞ':>9} {'理論成長率':>11} {'資産中央値':>11} {'下位5%':>10} "
          f"{'最大DD中央':>11} {'DD50%超':>9} {'破産':>7}")
    print("-" * 78)

    rows = []
    for mult in args.kelly_multiples:
        f = f_star * mult
        g = growth_rate(mu, sd, f)
        r = simulate_paths(mu, sd, f, n, args.paths, args.seed)
        name = f"{mult:g}x ｹﾘｰ"
        print(f"{name:>10} {f:>8.2f}x {g * 100:>10.4f}% {r['median']:>10.2f}倍 "
              f"{r['p05']:>9.2f}倍 {r['mdd_med'] * 100:>10.1f}% {r['half'] * 100:>8.1f}% "
              f"{r['ruin'] * 100:>6.1f}%")
        rows.append((mult, f, g, r))
    print("-" * 78)
    print(f"  (破産 = 資産が初期の1/10未満。DD = ピークからの下落率)")
    print()

    full = next((r for m, f, g, r in rows if m == 1.0), None)
    half_k = next((r for m, f, g, r in rows if m == 0.5), None)
    print("■ 読み方")
    print("  ・ 賭け金を増やすと成長率は上がるが、f* を超えた瞬間から下がり始める。")
    print(f"    f = 2f* ({2 * f_star:.2f}倍) で成長率はちょうど 0。それ以上は")
    print("    期待値プラスの取引を繰り返しながら資産が 0 に収束する。")
    print("    これは運の問題ではなく、log の凹性から来る確定した帰結である。")
    if full and half_k:
        print()
        print(f"  ・ ハーフケリーの成長率はフルケリーの 75%。それでいて最大DDは")
        print(f"    {full['mdd_med'] * 100:.0f}% → {half_k['mdd_med'] * 100:.0f}% に下がる。")
        print("    成長を25%諦めるだけでリスクが半減する。この非対称性が効く。")
    print()
    print("■ ここから導かれる聖杯の第三条件")
    print("  「ケリー比率の 1/2 以下で賭ける」")
    print("  理由は3つ。(1) μ と σ の推定値には必ず誤差があり、f* を過大評価する。")
    print("  (2) 実際のリターンは正規分布より裾が厚く、想定より大きく飛ぶ。")
    print("  (3) 途中のDDに人間が耐えられず、最悪のタイミングで降りる。")
    print("  この3つを織り込むと、実務上の安全域はハーフケリー以下になる。")
    print()


# --------------------------------------------------------------------------
# §5 分散 — 金融における唯一のタダ飯
# --------------------------------------------------------------------------

def cmd_mix(args: argparse.Namespace) -> None:
    print()
    print("=" * 78)
    print(" §5 分散 — シャープを上げる唯一の合法的な方法")
    print("=" * 78)
    print(" 同じ強さのエッジを k 本、相関 ρ で束ねた時の合成シャープ:")
    print("   SR(k, ρ) = SR(1本) × sqrt( k / (1 + (k-1)ρ) )")
    print()
    ks = (1, 2, 3, 5, 10, 20, 50)
    print(f"{'相関ρ':>8}" + "".join(f"{('k=' + str(k)):>10}" for k in ks))
    print("-" * 78)
    for rho in (0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0):
        cells = []
        for k in ks:
            denom = 1.0 + (k - 1) * rho
            cells.append(f"{math.sqrt(k / denom):>10.2f}")
        print(f"{rho:>8.1f}" + "".join(cells))
    print("-" * 78)
    print("  (本数を増やした時、1本あたりのシャープが何倍になるか)")
    print()
    print("■ 読み方")
    print("  ・ ρ=0 なら k 本で sqrt(k) 倍。10本束ねればシャープは3.16倍になる。")
    print("    元が 0.3 の使い物にならないエッジでも、10本集めれば 0.95 になる。")
    print("  ・ ρ=0.9 では 10本束ねても 1.05倍。ほぼ何も起きない。")
    print("    「日本株の中で10戦略」は大体ここ。全部同じ地合いに賭けている。")
    print("  ・ 相関を下げる方が、エッジを強くするより遥かに安上がりである。")
    print("    銘柄ではなく、資産クラス・時間軸・ロジックの系統をずらすこと。")
    print()
    print("■ 検証コストへの効き目")
    print("  必要取引数は シャープの2乗に反比例する。ρ=0 で k 本束ねると")
    print("  合成シャープは sqrt(k) 倍、つまり必要な検証期間は 1/k になる。")
    print("  分散は「儲かる」だけでなく「証明が早く終わる」点でも効く。")
    print()
    print("■ ここから導かれる聖杯の第四条件")
    print("  「相関の低いエッジを可能な限り多く並べる」")
    print("  1本の完璧な手法を探すのは、数学的に最も効率の悪い戦い方である。")
    print()


# --------------------------------------------------------------------------
# 結論
# --------------------------------------------------------------------------

def cmd_conclusion() -> None:
    print("=" * 78)
    print(" 結論 — 聖杯の正体")
    print("=" * 78)
    print()
    print(" 「確実に勝てる売買ルール」は存在しない。存在し得ない理由は §2 と §3 で")
    print(" 見た通りで、市場が難しいからではなく、統計の構造からそうなっている。")
    print(" 確実に勝てるルールが見つかったなら、それは")
    print("   (a) 試行回数を数えていない (§2)  か")
    print("   (b) サンプルが足りていない (§3)  のどちらかである。例外はない。")
    print()
    print(" 一方で、次の4つは全て数学的に保証されている。これが聖杯の中身である。")
    print()
    print("   1. 試行回数で割り引いた後に残る優位性は、本物である        (§2)")
    print("   2. 同じ優位性でも、取引回数を増やせば年率シャープは伸びる  (§3)")
    print("   3. ケリー比率の半分以下で賭ければ、破産確率は実質消える    (§4)")
    print("   4. 相関の低いエッジを k 本束ねれば、シャープは sqrt(k) 倍  (§5)")
    print()
    print(" 聖杯とは単一の手法ではなく、この4つを回し続ける手順のことである。")
    print(" 具体的には:")
    print()
    print("   ・ 小さくてよいので、理由の説明がつく優位性を探す")
    print("     (なぜ他人がその利益を取り残しているのか答えられること)")
    print("   ・ 試したパラメータの数を全部記録し、DSR で割り引く")
    print("   ・ 生き残ったものだけを、ハーフケリー以下で、複数本同時に回す")
    print("   ・ 優位性は必ず減衰するので、検定を止めずに回し続ける")
    print()
    print(" このリポジトリで言えば、nikkei_daytrade.py / elliott_wave3.py が")
    print(" 「候補を出す」係で、nikkei_verify.py が「本物か確かめる」係、")
    print(" この holy_grail.py が「まぐれと本物を分ける」係にあたる。")
    print(" 足りていないのは資金管理の実装と、相関の低い2本目のエッジである。")
    print()
    print("=" * 78)
    print()


# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="投資の聖杯を数式で追い詰める検証エンジン",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="引数なしで実行すると、ネット接続の要らない §2〜§5 を通しで表示します。",
    )
    parser.add_argument("--edge", action="store_true", help="§1 reports/ の選定を統計検定する (要ネット)")
    parser.add_argument("--trap", action="store_true", help="§2 多重検定の罠を実演する")
    parser.add_argument("--power", action="store_true", help="§3 必要取引数の早見表")
    parser.add_argument("--risk", action="store_true", help="§4 破産確率とケリー比率")
    parser.add_argument("--mix", action="store_true", help="§5 分散によるシャープ改善")

    parser.add_argument("--reports", default="reports", metavar="ﾃﾞｨﾚｸﾄﾘ", help="レポートの保存先")
    parser.add_argument("--top", type=int, default=0, metavar="件数", help="各日の上位N銘柄だけ検証する")
    parser.add_argument("--cost-pct", type=float, default=0.1, metavar="率", help="往復コスト%% (デフォルト: 0.1)")
    parser.add_argument("--trials", type=int, default=20, metavar="回数",
                        help="これまでに試した戦略/パラメータの総数。DSRの割引率を決める (デフォルト: 20)")

    parser.add_argument("--periods", type=int, default=250, metavar="期間", help="§2 1戦略あたりの期間数")
    parser.add_argument("--trap-trials", type=int, nargs="+", default=[1, 10, 50, 200, 1000],
                        metavar="N", help="§2 試行回数のリスト")

    parser.add_argument("--mu", type=float, default=0.05, metavar="%%", help="§4 1取引の平均リターン%% (デフォルト: 0.05)")
    parser.add_argument("--sd", type=float, default=1.5, metavar="%%", help="§4 1取引の標準偏差%% (デフォルト: 1.5)")
    parser.add_argument("--trades", type=int, default=2450, metavar="回数", help="§4 シミュレーションの取引数 (デフォルト: 2450 = 10年)")
    parser.add_argument("--paths", type=int, default=4000, metavar="本数", help="§4 モンテカルロの試行本数")
    parser.add_argument("--kelly-multiples", type=float, nargs="+",
                        default=[0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0],
                        metavar="倍率", help="§4 ケリー比率の何倍を賭けるか")

    parser.add_argument("--seed", type=int, default=42, metavar="値", help="乱数シード")
    args = parser.parse_args()

    picked = args.edge or args.trap or args.power or args.risk or args.mix
    if not picked:
        args.trap = args.power = args.risk = args.mix = True

    if args.edge:
        cmd_edge(args)
    if args.trap:
        cmd_trap(args)
    if args.power:
        cmd_power(args)
    if args.risk:
        cmd_risk(args)
    if args.mix:
        cmd_mix(args)
    if not picked or (args.trap and args.risk and args.mix):
        cmd_conclusion()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n中断しました。")
