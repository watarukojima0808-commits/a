#!/usr/bin/env python3
"""
信用残スクリーナー

JPX公式の「銘柄別信用取引週末残高」(週次) を複数週分取得し、株価と突き合わせて
2種類の銘柄リストを抽出します。

  A. 逆行警戒: 株価が下落しているのに信用買い残が増え続けている銘柄
     (高値掴みの買い方が塩漬けになり、戻り売り圧力=しこりが溜まっていく形)
  B. 需給良好: 売り長 (信用倍率が低い) で買い残も軽く、上値が軽い銘柄
     (踏み上げ余地があり、上昇局面で需給が追い風になる形)

Usage: python margin_screener.py
       python margin_screener.py --weeks 5 --top 15 --out-dir reports/margin
       python margin_screener.py --inspect   # JPXファイルの構造を確認する
"""

import os
import re
import ssl
import sys
import json
import time
import gzip
import html
import random
import argparse
import urllib.error
import urllib.request
from io import StringIO
from datetime import datetime, date, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

JPX_MARGIN_PAGES = [
    # 銘柄別信用取引週末残高の掲載ページ。構成が変わっても拾えるよう複数当たる。
    "https://www.jpx.co.jp/markets/statistics-equities/margin/05.html",
    "https://www.jpx.co.jp/markets/statistics-equities/margin/index.html",
]
JPX_BASE = "https://www.jpx.co.jp"
CHART_API = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range}&interval=1d"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip",
}

JST = timezone(timedelta(hours=9))


# --------------------------------------------------------------------------
# 通信
# --------------------------------------------------------------------------

def http_get(url: str, timeout: int = 30, retries: int = 3) -> bytes:
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
# JPX: 週次ファイルの発見とダウンロード
# --------------------------------------------------------------------------

DATE_PAT = re.compile(r"(?:(\d{4})\s*年)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
LINK_PAT = re.compile(r'<a\s[^>]*href="([^"]+\.(?:xlsx?|csv))"[^>]*>(.*?)</a>', re.S | re.I)


def _guess_date(text: str, today: date) -> date | None:
    m = DATE_PAT.search(text)
    if not m:
        return None
    year = int(m.group(1)) if m.group(1) else today.year
    month, day = int(m.group(2)), int(m.group(3))
    try:
        d = date(year, month, day)
    except ValueError:
        return None
    # 年の記載がないリンクで、未来日付になってしまったら前年扱い (年またぎ対策)
    if not m.group(1) and d > today + timedelta(days=7):
        d = date(year - 1, month, day)
    return d


def discover_weekly_files(verbose: bool = False) -> list[tuple[date | None, str, str]]:
    """JPXの掲載ページを巡回し、(基準日, URL, リンク周辺テキスト) のリストを新しい順に返す。"""
    today = datetime.now(JST).date()
    pages = list(JPX_MARGIN_PAGES)
    seen_pages: set[str] = set()
    found: dict[str, tuple[date | None, str]] = {}

    while pages:
        page_url = pages.pop(0)
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        try:
            body = http_get(page_url).decode("utf-8", errors="replace")
        except Exception as e:
            print(f"[WARN] ページ取得失敗 {page_url}: {e}")
            continue

        if verbose:
            all_links = re.findall(r'href="([^"]+)"', body)
            files = [h for h in all_links if re.search(r"\.(xlsx?|csv|pdf|zip)([?#]|$)", h, re.I)]
            print(f"  [DEBUG] {page_url}: {len(body)}文字 / リンク{len(all_links)}件 / ファイル{len(files)}件")
            for h in files[:30]:
                print(f"    file: {h}")
            if not files:
                # ファイルリンクが皆無ならページ抜粋を出す (JS描画などの切り分け用)
                pos = body.find("残高")
                print(f"    抜粋: {body[max(0, pos - 200): pos + 800] if pos >= 0 else body[:800]!r}")

        # ページ内のExcel/CSVリンクを、リンク文言と直前の見出しテキストごと拾う
        for m in LINK_PAT.finditer(body):
            href, label = m.group(1), re.sub(r"<[^>]+>", " ", m.group(2))
            url = href if href.startswith("http") else JPX_BASE + href
            context = html.unescape(re.sub(r"<[^>]+>", " ", body[max(0, m.start() - 400): m.end()]))
            # 銘柄別の週末残高ファイルだけが対象。週間合計残高などのファイルを除外する。
            if "銘柄別" not in context and "syumatsu" not in url.lower():
                continue
            when = _guess_date(html.unescape(label), today) or _guess_date(context, today)
            if url not in found or (found[url][0] is None and when):
                found[url] = (when, html.unescape(label).strip())
            if verbose:
                print(f"  リンク発見: {when} {url} ({html.unescape(label).strip()[:40]})")

        # 過去分 (バックナンバー) ページも一段だけ辿る
        if len(seen_pages) <= 6:
            for href in re.findall(r'href="([^"]*margin[^"]*\.html)"', body):
                sub = href if href.startswith("http") else JPX_BASE + href
                if sub not in seen_pages:
                    pages.append(sub)

    items = [(when, url, label) for url, (when, label) in found.items()]
    # 基準日が取れたものを新しい順に。日付不明のものは末尾。
    items.sort(key=lambda t: (t[0] is None, -(t[0].toordinal() if t[0] else 0)))
    return items


def download_weekly(items, weeks: int, cache_dir: str) -> list[tuple[date | None, str, bytes]]:
    os.makedirs(cache_dir, exist_ok=True)
    out = []
    for when, url, _label in items[: weeks]:
        fname = os.path.join(cache_dir, os.path.basename(url))
        if os.path.exists(fname):
            with open(fname, "rb") as f:
                data = f.read()
        else:
            print(f"  ダウンロード: {url}")
            data = http_get(url)
            with open(fname, "wb") as f:
                f.write(data)
        out.append((when, url, data))
    return out


# --------------------------------------------------------------------------
# Excel/CSV 読み取り (追加ライブラリなしで .xlsx と .csv に対応、.xls は xlrd があれば)
# --------------------------------------------------------------------------

def _xlsx_rows(data: bytes) -> list[list[str]]:
    import zipfile
    import xml.etree.ElementTree as ET
    from io import BytesIO

    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    zf = zipfile.ZipFile(BytesIO(data))

    shared: list[str] = []
    if "xl/sharedStrings.xml" in zf.namelist():
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
        for si in root.findall("m:si", ns):
            shared.append("".join(t.text or "" for t in si.iter(
                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))

    sheet_names = sorted(n for n in zf.namelist()
                         if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
    rows: list[list[str]] = []
    for sheet in sheet_names[:1]:  # 先頭シートのみ
        root = ET.fromstring(zf.read(sheet))
        for row in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            cells: dict[int, str] = {}
            for c in row:
                ref = c.get("r", "")
                col = 0
                for ch in ref:
                    if ch.isalpha():
                        col = col * 26 + ord(ch.upper()) - 64
                    else:
                        break
                t = c.get("t")
                if t == "inlineStr":
                    value = "".join(el.text or "" for el in c.iter(
                        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"))
                else:
                    v = c.find("m:v", ns)
                    value = v.text if v is not None and v.text is not None else ""
                    if t == "s" and value != "":
                        value = shared[int(value)]
                cells[col - 1] = value
            width = max(cells) + 1 if cells else 0
            rows.append([cells.get(i, "") for i in range(width)])
    return rows


def _xls_rows(data: bytes) -> list[list[str]]:
    try:
        import xlrd  # type: ignore
    except ImportError:
        sys.exit(
            "JPXのファイルが旧形式 (.xls) でした。読み取りには xlrd が必要です:\n"
            "  pip install xlrd"
        )
    book = xlrd.open_workbook(file_contents=data)
    sheet = book.sheet_by_index(0)
    rows = []
    for r in range(sheet.nrows):
        row = []
        for c in range(sheet.ncols):
            v = sheet.cell_value(r, c)
            if isinstance(v, float) and v == int(v):
                v = int(v)
            row.append(str(v) if v is not None else "")
        rows.append(row)
    return rows


def _csv_rows(data: bytes) -> list[list[str]]:
    import csv as _csv
    text = data.decode("cp932", errors="replace")
    return [row for row in _csv.reader(StringIO(text))]


def read_table(url: str, data: bytes) -> list[list[str]]:
    low = url.lower()
    if low.endswith(".xlsx"):
        return _xlsx_rows(data)
    if low.endswith(".xls"):
        # 拡張子が .xls でも実体が zip (=xlsx) のことがある
        if data[:2] == b"PK":
            return _xlsx_rows(data)
        return _xls_rows(data)
    return _csv_rows(data)


# --------------------------------------------------------------------------
# 週次残高テーブルの解釈
# --------------------------------------------------------------------------

CODE_PAT = re.compile(r"^[0-9][0-9A-Z]{3}0?$")  # 4桁 (新コードは英字含む)、5桁表記は末尾0


def _clean_num(s: str) -> float | None:
    s = str(s).strip().replace(",", "").replace("△", "-").replace("▲", "-")
    if s in ("", "-", "—", "－", "*", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_records(rows: list[list[str]], verbose: bool = False) -> dict[str, dict]:
    """行列データから {code: {name, buy, sell}} を取り出す。

    JPXのレイアウト変更に耐えるため、位置決め打ちではなく
    「コードらしい列」「ヘッダ文言に 売/買 を含む数値列」を探して解釈する。
    """
    if not rows:
        return {}

    # 1) コード列: コード形式のセルが最も多い列
    ncols = max(len(r) for r in rows)
    code_hits = [0] * ncols
    for r in rows:
        for i, cell in enumerate(r):
            if CODE_PAT.fullmatch(str(cell).strip()):
                code_hits[i] += 1
    code_col = max(range(ncols), key=lambda i: code_hits[i])
    if code_hits[code_col] < 10:
        raise ValueError("銘柄コードの列を特定できませんでした")

    data_rows = [r for r in rows if len(r) > code_col
                 and CODE_PAT.fullmatch(str(r[code_col]).strip())]
    first_data = rows.index(data_rows[0])

    # 2) ヘッダ文言: データ開始行より上のセルを列ごとに連結
    header: list[str] = []
    for i in range(ncols):
        parts = []
        for r in rows[:first_data]:
            if i < len(r) and str(r[i]).strip():
                parts.append(str(r[i]).strip())
        header.append(" ".join(parts))

    # 結合セル対策: 「売残高」「買残高」の大分類ヘッダは右隣の列にも及ぶことがあるため、
    # 明示的なラベルのない列には直前の分類を引き継ぐ
    last_side = ""
    sides = [""] * ncols
    for i in range(ncols):
        h = header[i]
        if "売" in h and "買" not in h:
            last_side = "sell"
        elif "買" in h and "売" not in h:
            last_side = "buy"
        elif h and "売" not in h and "買" not in h and not re.search(r"前週|増減|比", h):
            # 別の系列 (銘柄名など) が始まったら引き継ぎを切る
            if re.search(r"[コ銘市名]", h):
                last_side = ""
        sides[i] = last_side

    # 3) 売残・買残の列を選ぶ。「前週比/増減」列は除外し、数値が入っている列に限る。
    def numeric_ratio(col: int) -> float:
        vals = [_clean_num(r[col]) for r in data_rows[:200] if col < len(r)]
        return sum(v is not None for v in vals) / max(len(vals), 1)

    buy_cols, sell_cols = [], []
    for i in range(ncols):
        if i == code_col or numeric_ratio(i) < 0.5:
            continue
        h = header[i]
        if re.search(r"前週|増減|比率|倍率", h):
            continue
        side = ("sell" if ("売" in h and "買" not in h) else
                "buy" if ("買" in h and "売" not in h) else sides[i])
        if side == "buy":
            buy_cols.append(i)
        elif side == "sell":
            sell_cols.append(i)

    # 「合計」列があればそれを優先、なければ該当列の合算 (制度+一般)
    def choose(cols: list[int]) -> list[int]:
        totals = [i for i in cols if "合計" in header[i]]
        return totals[:1] if totals else cols

    buy_cols, sell_cols = choose(buy_cols), choose(sell_cols)
    if verbose:
        print(f"  コード列: {code_col} / データ {len(data_rows)} 行")
        for i in range(ncols):
            print(f"    列{i}: '{header[i][:30]}' side={sides[i]} num={numeric_ratio(i):.0%}")
        print(f"  買残列: {buy_cols} / 売残列: {sell_cols}")
    if not buy_cols or not sell_cols:
        raise ValueError(f"売残・買残の列を特定できませんでした (買={buy_cols} 売={sell_cols})")

    # 4) 銘柄名列: コード列の隣で文字列率が高いほう
    name_col = None
    for cand in (code_col + 1, code_col - 1, code_col + 2):
        if 0 <= cand < ncols:
            texts = sum(1 for r in data_rows[:100]
                        if cand < len(r) and str(r[cand]).strip()
                        and _clean_num(r[cand]) is None)
            if texts > 50:
                name_col = cand
                break

    records: dict[str, dict] = {}
    for r in data_rows:
        code = str(r[code_col]).strip()
        if len(code) == 5 and code.endswith("0"):
            code = code[:4]
        buy = sum(v for i in buy_cols if (v := _clean_num(r[i]) if i < len(r) else None) is not None)
        sell = sum(v for i in sell_cols if (v := _clean_num(r[i]) if i < len(r) else None) is not None)
        name = str(r[name_col]).strip() if name_col is not None and name_col < len(r) else code
        # 同一コードが複数行 (市場別など) ある場合は残高を合算する
        if code in records:
            records[code]["buy"] += buy
            records[code]["sell"] += sell
        else:
            records[code] = {"name": name, "buy": buy, "sell": sell}
    return records


# --------------------------------------------------------------------------
# 株価取得 (Yahoo Finance)
# --------------------------------------------------------------------------

def fetch_prices(code: str, range_: str = "3mo") -> tuple[list[float], list[float]] | None:
    """(終値リスト, 出来高リスト) を古い順で返す。取得失敗は None。"""
    try:
        data = json.loads(http_get(CHART_API.format(symbol=f"{code}.T", range=range_), timeout=20))
    except Exception:
        return None
    result = data.get("chart", {}).get("result")
    if not result:
        return None
    quote = (result[0].get("indicators", {}).get("quote") or [{}])[0]
    closes, vols = [], []
    for c, v in zip(quote.get("close") or [], quote.get("volume") or []):
        if c is not None and v:
            closes.append(c)
            vols.append(v)
    return (closes, vols) if len(closes) >= 30 else None


# --------------------------------------------------------------------------
# スクリーニング
# --------------------------------------------------------------------------

def build_series(weekly: list[tuple[date | None, dict[str, dict]]]) -> dict[str, dict]:
    """週次データ (新しい順) を銘柄ごとの時系列 (古い順) にまとめる。"""
    ordered = list(reversed(weekly))  # 古い順
    stocks: dict[str, dict] = {}
    for _when, records in ordered:
        for code, rec in records.items():
            s = stocks.setdefault(code, {"name": rec["name"], "buy": [], "sell": []})
            s["buy"].append(rec["buy"])
            s["sell"].append(rec["sell"])
            s["name"] = rec["name"] or s["name"]
    # 全週そろっている銘柄だけ対象にする (新規貸借銘柄などの欠損を除外)
    n = len(ordered)
    return {c: s for c, s in stocks.items() if len(s["buy"]) == n}


def consecutive_increases(xs: list[float]) -> int:
    n = 0
    for prev, cur in zip(reversed(xs[:-1]), reversed(xs[1:])):
        if cur > prev:
            n += 1
        else:
            break
    return n


def screen(stocks: dict[str, dict], args) -> tuple[list[dict], list[dict], dict]:
    # --- 株価取得前の一次絞り込み (残高だけで判定できる条件) ---
    pre_a, pre_b = [], []
    for code, s in stocks.items():
        buy, sell = s["buy"], s["sell"]
        if buy[-1] <= 0:
            continue
        inc = consecutive_increases(buy)
        buy_chg = buy[-1] / buy[0] - 1 if buy[0] > 0 else float("inf")
        ratio = buy[-1] / sell[-1] if sell[-1] > 0 else float("inf")
        base = {"code": code, "name": s["name"], "buy": buy, "sell": sell,
                "inc_weeks": inc, "buy_chg": buy_chg, "ratio": ratio}
        if inc >= args.min_weeks and buy_chg >= args.min_buy_increase / 100 \
                and buy[-1] >= 50_000:
            pre_a.append(base)
        if ratio <= args.max_ratio and sell[-1] >= 50_000 \
                and buy[-1] <= buy[0] * 1.1:
            pre_b.append(base)

    pre_a.sort(key=lambda m: m["buy_chg"], reverse=True)
    pre_b.sort(key=lambda m: m["ratio"])
    pre_a, pre_b = pre_a[: args.max_fetch // 2], pre_b[: args.max_fetch // 2]

    # --- 株価と出来高で仕上げの判定 ---
    targets = {m["code"]: m for m in pre_a + pre_b}
    print(f"候補 {len(targets)} 銘柄の株価を取得中... (A候補 {len(pre_a)} / B候補 {len(pre_b)})")
    prices: dict[str, tuple[list[float], list[float]]] = {}

    def work(code: str):
        return code, fetch_prices(code)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (code, p) in enumerate(pool.map(work, list(targets)), 1):
            if p:
                prices[code] = p
            if i % 25 == 0 or i == len(targets):
                print(f"  {i}/{len(targets)} 完了", end="\r", flush=True)
    print()

    # 残高データと同じ期間 (週数×5営業日) の騰落を見る
    lookback = 5 * args.weeks

    def enrich(m: dict) -> dict | None:
        p = prices.get(m["code"])
        if not p:
            return None
        closes, vols = p
        close = closes[-1]
        if close < args.min_price:
            return None
        back = min(lookback, len(closes) - 1)
        ma25 = sum(closes[-25:]) / 25
        avg_vol20 = sum(vols[-20:]) / 20
        turnover = sum(c * v for c, v in zip(closes[-20:], vols[-20:])) / 20
        if turnover < args.min_turnover * 100_000_000:
            return None
        m = dict(m)
        m.update({
            "close": close,
            "price_chg": (close / closes[-back - 1] - 1) * 100,
            "ma25_dev": (close / ma25 - 1) * 100,
            "days_to_cover": m["buy"][-1] / avg_vol20 if avg_vol20 else float("inf"),
            "sell_days": m["sell"][-1] / avg_vol20 if avg_vol20 else 0.0,
            "turnover": turnover,
        })
        return m

    picks_a = []
    for m in pre_a:
        e = enrich(m)
        if e and e["price_chg"] <= -args.min_price_drop and e["ma25_dev"] < 0:
            e["score"] = e["buy_chg"] * 100 + abs(e["price_chg"]) * 2 + min(e["days_to_cover"], 20)
            picks_a.append(e)
    picks_a.sort(key=lambda m: m["score"], reverse=True)

    picks_b = []
    for m in pre_b:
        e = enrich(m)
        if e and e["ma25_dev"] >= 0 and e["days_to_cover"] <= 3.0:
            # 倍率が低いほど・売り方の踏み上げ燃料が多いほど高評価
            e["score"] = (2.0 - min(e["ratio"], 2.0)) * 40 + min(e["sell_days"], 5) * 8 \
                + max(min(e["ma25_dev"], 10), 0)
            picks_b.append(e)
    picks_b.sort(key=lambda m: m["score"], reverse=True)

    stats = {"universe": len(stocks), "pre_a": len(pre_a), "pre_b": len(pre_b),
             "fetched": len(prices)}
    return picks_a[: args.top], picks_b[: args.top], stats


# --------------------------------------------------------------------------
# 出力
# --------------------------------------------------------------------------

def fmt_man(v: float) -> str:
    return f"{v / 10_000:,.1f}万株"


def format_report(picks_a, picks_b, dates: list[date | None], stats: dict) -> str:
    now = datetime.now(JST)
    period = " → ".join(d.strftime("%m/%d") if d else "?" for d in reversed(dates))
    out = StringIO()
    w = out.write

    w(f"# 信用残スクリーニング {now:%Y-%m-%d (%a)}\n\n")
    w(f"生成時刻: {now:%H:%M} JST / 信用残: JPX 銘柄別信用取引週末残高 ({period} の {len(dates)} 週分)\n\n")
    w(f"対象 {stats['universe']} 銘柄 (全週データあり) / 株価照合 {stats['fetched']} 銘柄\n\n")

    w("## A. 逆行警戒: 株価下落中なのに信用買い残が増え続けている銘柄\n\n")
    w("下がる株を信用で買い下がっている状態。買い方の評価損が膨らむと、戻り局面での\n")
    w("返済売り (しこり) になりやすく、上値が重くなる典型的な需給悪化パターンです。\n\n")
    if picks_a:
        w("| # | コード | 銘柄 | 終値 | 株価騰落* | 買残 | 買残増加* | 連続増加 | 信用倍率 | 回転日数** |\n")
        w("|---|--------|------|------|-----------|------|-----------|----------|----------|--------|\n")
        for i, m in enumerate(picks_a, 1):
            ratio = "∞" if m["ratio"] == float("inf") else f"{m['ratio']:.1f}倍"
            w(f"| {i} | {m['code']} | {m['name']} | {m['close']:,.0f} | {m['price_chg']:+.1f}% | "
              f"{fmt_man(m['buy'][-1])} | {m['buy_chg'] * 100:+.0f}% | {m['inc_weeks']}週 | "
              f"{ratio} | {m['days_to_cover']:.1f}日 |\n")
    else:
        w("該当銘柄なし。\n")

    w("\n## B. 需給良好: 売り長で買い残も軽い銘柄\n\n")
    w("信用倍率が低く (売り残 ≧ 買い残)、買い残のしこりが小さい銘柄。株価が上昇すると\n")
    w("売り方の買い戻し (踏み上げ) が燃料になりやすい、需給面で追い風の形です。\n\n")
    if picks_b:
        w("| # | コード | 銘柄 | 終値 | 25日線乖離 | 信用倍率 | 売残 | 買残 | 買残回転** |\n")
        w("|---|--------|------|------|------------|----------|------|------|--------|\n")
        for i, m in enumerate(picks_b, 1):
            w(f"| {i} | {m['code']} | {m['name']} | {m['close']:,.0f} | {m['ma25_dev']:+.1f}% | "
              f"{m['ratio']:.2f}倍 | {fmt_man(m['sell'][-1])} | {fmt_man(m['buy'][-1])} | "
              f"{m['days_to_cover']:.1f}日 |\n")
    else:
        w("該当銘柄なし。\n")

    w(f"\n\\* 騰落・増加は約{len(dates)}週間 (残高データと同じ期間) の変化。\n")
    w("\\*\\* 回転日数 = 買残 ÷ 20日平均出来高。買い残を消化するのに何日分の商いが要るか。\n\n")
    w("---\n\n")
    w("本レポートは公表データからの機械的な抽出であり、投資判断を推奨するものではありません。\n")
    w("信用残は週次 (基準日は前週末) のため、直近の変化は反映されていません。売買は自己責任で行ってください。\n")
    return out.getvalue()


def print_console(picks_a, picks_b) -> None:
    def show(title, picks, cols):
        print()
        print("=" * 78)
        print(f" {title}")
        print("=" * 78)
        if not picks:
            print("該当銘柄なし。")
            return
        for i, m in enumerate(picks, 1):
            print(f"{i:>2}. {m['code']} {m['name']}: " + " / ".join(c(m) for c in cols))

    def fmt_ratio(m):
        return "∞" if m["ratio"] == float("inf") else f"{m['ratio']:.1f}"

    show("A. 株価下落中なのに買い残増加 (逆行警戒)", picks_a, [
        lambda m: f"株価{m['price_chg']:+.1f}%",
        lambda m: f"買残{m['buy_chg'] * 100:+.0f}% ({m['inc_weeks']}週連続増)",
        lambda m: "倍率" + fmt_ratio(m),
    ])
    show("B. 需給良好 (売り長・買い残軽い)", picks_b, [
        lambda m: f"倍率{m['ratio']:.2f}",
        lambda m: f"売残{fmt_man(m['sell'][-1])}",
        lambda m: f"25日線{m['ma25_dev']:+.1f}%",
    ])
    print()


# --------------------------------------------------------------------------

def run(args) -> None:
    print("JPXから銘柄別信用取引週末残高を探索中...")
    items = discover_weekly_files(verbose=args.inspect)
    if not items:
        sys.exit("週次ファイルが見つかりませんでした。JPXのページ構成が変わった可能性があります。")
    print(f"{len(items)} 件の週次ファイルを発見。直近 {args.weeks} 週分を使用します。")
    for when, url, label in items[: args.weeks]:
        print(f"  {when} {label[:40]} {url}")

    files = download_weekly(items, args.weeks, args.cache_dir)

    weekly: list[tuple[date | None, dict[str, dict]]] = []
    for when, url, data in files:
        rows = read_table(url, data)
        if args.inspect:
            print(f"\n--- {url} 先頭20行 ---")
            for r in rows[:20]:
                print("  | " + " | ".join(str(c)[:14] for c in r[:12]))
        records = extract_records(rows, verbose=args.inspect)
        print(f"  {when}: {len(records)} 銘柄を読み取り")
        weekly.append((when, records))

    if args.inspect:
        sample = list(weekly[0][1].items())[:5]
        print("\nサンプル (最新週):")
        for code, rec in sample:
            print(f"  {code} {rec['name']}: 買残 {rec['buy']:,.0f} / 売残 {rec['sell']:,.0f}")
        return

    if len(weekly) < 3:
        sys.exit("3週分以上のデータが必要です。")

    stocks = build_series(weekly)
    print(f"全 {len(weekly)} 週そろっている {len(stocks)} 銘柄を対象にスクリーニングします。")

    picks_a, picks_b, stats = screen(stocks, args)
    print_console(picks_a, picks_b)

    dates = [when for when, _ in weekly]
    report = format_report(picks_a, picks_b, dates, stats)

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        stem = os.path.join(args.out_dir, f"{datetime.now(JST):%Y-%m-%d}")
        with open(f"{stem}.md", "w", encoding="utf-8") as f:
            f.write(report)
        with open(f"{stem}.json", "w", encoding="utf-8") as f:
            json.dump({
                "generated": datetime.now(JST).isoformat(timespec="seconds"),
                "weeks": [d.isoformat() if d else None for d in dates],
                "declining_price_rising_margin": [
                    {k: (None if m[k] == float("inf") else m[k])
                     for k in ("code", "name", "close", "price_chg", "buy_chg",
                               "inc_weeks", "ratio", "days_to_cover")}
                    for m in picks_a
                ],
                "good_supply_demand": [
                    {k: m[k] for k in ("code", "name", "close", "ma25_dev", "ratio",
                                       "days_to_cover")}
                    for m in picks_b
                ],
            }, f, ensure_ascii=False, indent=1)
        print(f"レポートを保存しました: {stem}.md / {stem}.json")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="信用残スクリーナー (JPX 銘柄別信用取引週末残高)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--weeks", type=int, default=5, metavar="週", help="参照する週次データの数 (デフォルト: 5)")
    parser.add_argument("--top", type=int, default=15, metavar="件数", help="各リストの掲載数 (デフォルト: 15)")
    parser.add_argument("--min-weeks", type=int, default=3, metavar="週", help="A: 買残の最低連続増加週数 (デフォルト: 3)")
    parser.add_argument("--min-buy-increase", type=float, default=15, metavar="％", help="A: 期間中の買残増加率の下限 (デフォルト: 15%%)")
    parser.add_argument("--min-price-drop", type=float, default=5, metavar="％", help="A: 期間中の株価下落率の下限 (デフォルト: 5%%)")
    parser.add_argument("--max-ratio", type=float, default=1.2, metavar="倍", help="B: 信用倍率の上限 (デフォルト: 1.2)")
    parser.add_argument("--min-turnover", type=float, default=3, metavar="億円", help="20日平均売買代金の下限 (デフォルト: 3億円)")
    parser.add_argument("--min-price", type=float, default=100, metavar="円", help="株価の下限 (デフォルト: 100円)")
    parser.add_argument("--max-fetch", type=int, default=600, metavar="銘柄", help="株価を取得する候補数の上限 (デフォルト: 600)")
    parser.add_argument("--workers", type=int, default=4, metavar="数", help="並列取得数 (デフォルト: 4)")
    parser.add_argument("--out-dir", metavar="パス", help="レポートの保存先ディレクトリ")
    parser.add_argument("--cache-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache", "jpx"),
                        metavar="パス", help="JPXファイルのダウンロード先キャッシュ")
    parser.add_argument("--inspect", action="store_true", help="JPXファイルの構造を表示して終了 (デバッグ用)")
    args = parser.parse_args()

    try:
        run(args)
    except KeyboardInterrupt:
        print("\n中断しました。")


if __name__ == "__main__":
    main()
