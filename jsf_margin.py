#!/usr/bin/env python3
"""
日本証券金融（日証金）の貸借取引残高

信用需給を日次で見るためのデータです。全銘柄分が1つのCSVにまとまっているため、
1リクエストで足ります。東証が公表する信用残（週次・PDF）とは別物で、
こちらは「証券会社が日証金から借りた分」＝制度信用の一部である点に注意してください。
方向感を日次で追う用途には十分ですが、信用買残そのものではありません。

Usage: python jsf_margin.py 9021 1925
"""

import io
import csv
import sys
import gzip
import urllib.error
import urllib.request

ZANDAKA_URL = "https://www.taisyaku.jp/data/zandaka.csv"
REGULATION_URL = "https://www.taisyaku.jp/data/seigenichiran.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
    "Accept-Encoding": "gzip",
}

_cache: dict[str, dict] | None = None
_regulations: dict[str, str] | None = None


def _get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("cp932", errors="replace")


def _num(s: str) -> float:
    try:
        return float(s.replace(",", ""))
    except (ValueError, AttributeError):
        return 0.0


def load() -> dict[str, dict]:
    """全銘柄の貸借残高を {コード: 指標} で返す。取得失敗時は空辞書。"""
    global _cache
    if _cache is not None:
        return _cache
    _cache = {}
    try:
        rows = list(csv.reader(io.StringIO(_get(ZANDAKA_URL))))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[WARN] 貸借残高を取得できませんでした ({e})。需給なしで続行します。")
        return _cache

    for row in rows[1:]:
        if len(row) < 20 or not row[2].strip().isdigit():
            continue
        code = row[2].strip()
        # 同じ銘柄が複数市場で並ぶことがある。売買の主戦場である東証の行を優先する。
        if code in _cache and not row[4].startswith("東証"):
            continue

        loan = _num(row[9])          # 融資残高株数 = 買い方
        short = _num(row[12])        # 貸株残高株数 = 売り方
        if loan == 0 and short == 0:
            continue

        new_loan, repaid = _num(row[7]), _num(row[8])
        _cache[code] = {
            "date": row[0],
            "loan": loan,
            "short": short,
            "ratio": loan / short if short else None,   # 貸借倍率
            "loan_change": new_loan - repaid,           # 当日の買い残増減
            "turnover_days": _num(row[32]) or None,     # 融資・残高回転日数
        }
    return _cache


def regulations() -> dict[str, str]:
    """貸借取引の制限措置銘柄を {コード: 措置内容} で返す。"""
    global _regulations
    if _regulations is not None:
        return _regulations
    _regulations = {}
    try:
        rows = list(csv.reader(io.StringIO(_get(REGULATION_URL))))
    except (urllib.error.URLError, TimeoutError, OSError):
        return _regulations

    for row in rows:
        if len(row) < 5 or not row[1].strip().isdigit():
            continue
        # 「申込停止 / 新規売り」のように、措置と対象を並べて持つ。
        _regulations[row[1].strip()] = f"{row[3].strip()}({row[4].strip()})"
    return _regulations


def fetch(code: str) -> dict | None:
    m = load().get(code)
    if m is None:
        return None
    return {**m, "regulation": regulations().get(code)}


# --------------------------------------------------------------------------
# 評価
# --------------------------------------------------------------------------

def stance(ratio: float | None) -> tuple[float, str]:
    """貸借倍率から (調整値, 説明) を返す。

    融資残と貸株残がぴったり同数になる銘柄が多いのは、日証金の貸借が
    その水準で付き合っているためで、売り長ではなく「均衡」として扱う。
    """
    if ratio is None:                     # 貸株ゼロ＝売り方不在。買い残だけが残る形。
        return -5.0, "貸株残ゼロ（売り方不在）"
    if ratio < 0.8:
        return 4.0, f"貸借倍率 {ratio:.2f}倍（売り長・踏み上げ余地）"
    if ratio <= 1.5:
        return 1.0, f"貸借倍率 {ratio:.2f}倍（融資と貸株が均衡）"
    if ratio <= 3.0:
        return 0.0, f"貸借倍率 {ratio:.1f}倍（やや買い長）"
    if ratio <= 8.0:
        return -2.0, f"貸借倍率 {ratio:.1f}倍（買い長）"
    if ratio <= 15.0:
        return -4.0, f"貸借倍率 {ratio:.1f}倍（買い長・上値が重い）"
    return -6.0, f"貸借倍率 {ratio:.1f}倍（大きく買い長・戻り売り圧力）"


def adjust(m: dict | None) -> float:
    """需給を波形スコアへの調整値にする。買い長は上値が重く、売り長は踏み上げ余地。"""
    if not m:
        return 0.0

    score, _ = stance(m["ratio"])

    # 買い残が減っている＝しこりの整理が進んでいる。
    if m["loan"]:
        rate = m["loan_change"] / m["loan"]
        if rate <= -0.05:
            score += 3.0
        elif rate < 0:
            score += 1.0
        elif rate >= 0.05:
            score -= 3.0
        else:
            score -= 1.0

    if m.get("regulation"):
        score -= 3.0
    return score


def ratio_text(m: dict | None) -> str:
    if not m:
        return "—"
    if m["ratio"] is None:
        return "売残0"
    return f"{m['ratio']:.1f}倍"


def summary(m: dict | None) -> str:
    """「貸借倍率 9.3倍（買い長）/ 買い残 増加中」の形にまとめる。"""
    if not m:
        return "— 貸借データなし"

    _, text = stance(m["ratio"])

    rate = m["loan_change"] / m["loan"] if m["loan"] else 0.0
    if rate <= -0.05:
        trend = "買い残が大きく減少（整理進行）"
    elif rate < 0:
        trend = "買い残は微減"
    elif rate >= 0.05:
        trend = "買い残が大きく増加（しこり増）"
    else:
        trend = "買い残は横ばい"

    out = f"{text} / {trend}"
    if m.get("regulation"):
        out += f" / **貸借規制: {m['regulation']}**"
    return out


def main() -> None:
    codes = sys.argv[1:]
    if not codes:
        data = load()
        print(f"{len(data)} 銘柄の貸借残高を取得しました（規制 {len(regulations())} 銘柄）。")
        return
    for code in codes:
        m = fetch(code)
        print(f"--- {code}: {summary(m)}  (調整 {adjust(m):+.0f})")
        if m:
            for k, v in m.items():
                print(f"    {k:<15} {v}")


if __name__ == "__main__":
    main()
