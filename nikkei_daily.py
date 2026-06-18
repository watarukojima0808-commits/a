#!/usr/bin/env python3
"""
Nikkei 225 Daily Stock Recommendations
Usage: python nikkei_daily.py
       python nikkei_daily.py --gmail-from you@gmail.com --gmail-to you@gmail.com --gmail-password "xxxx xxxx xxxx xxxx"
       GMAIL_FROM=you@gmail.com GMAIL_TO=you@gmail.com GMAIL_PASSWORD="xxxx" python nikkei_daily.py
"""

import smtplib
import argparse
import sys
import os
from email.mime.text import MIMEText
from datetime import datetime

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    sys.exit("必要なライブラリをインストールしてください: pip install yfinance pandas")

# 日経225構成銘柄 (2024年時点)
NIKKEI_225 = [
    "1332.T", "1333.T", "1605.T", "1721.T", "1801.T", "1802.T", "1803.T",
    "1808.T", "1812.T", "1925.T", "1928.T", "1963.T", "2002.T", "2269.T",
    "2282.T", "2413.T", "2432.T", "2501.T", "2502.T", "2503.T", "2531.T",
    "2768.T", "2801.T", "2802.T", "2871.T", "2914.T", "3086.T", "3092.T",
    "3099.T", "3289.T", "3382.T", "3401.T", "3402.T", "3407.T", "3436.T",
    "3659.T", "3861.T", "3863.T", "4004.T", "4005.T", "4021.T", "4042.T",
    "4043.T", "4061.T", "4063.T", "4151.T", "4183.T", "4188.T", "4208.T",
    "4272.T", "4307.T", "4324.T", "4502.T", "4503.T", "4506.T", "4507.T",
    "4519.T", "4523.T", "4543.T", "4568.T", "4578.T", "4661.T", "4689.T",
    "4704.T", "4751.T", "4755.T", "4901.T", "4902.T", "5019.T", "5020.T",
    "5101.T", "5105.T", "5108.T", "5201.T", "5202.T", "5214.T", "5232.T",
    "5233.T", "5301.T", "5332.T", "5333.T", "5401.T", "5406.T", "5411.T",
    "5413.T", "5541.T", "5631.T", "5706.T", "5707.T", "5711.T", "5713.T",
    "5714.T", "5715.T", "5801.T", "5802.T", "5803.T", "6098.T", "6103.T",
    "6113.T", "6146.T", "6178.T", "6273.T", "6301.T", "6302.T", "6305.T",
    "6326.T", "6361.T", "6366.T", "6367.T", "6471.T", "6472.T", "6473.T",
    "6479.T", "6501.T", "6503.T", "6504.T", "6506.T", "6508.T", "6645.T",
    "6674.T", "6701.T", "6702.T", "6703.T", "6724.T", "6725.T", "6752.T",
    "6753.T", "6758.T", "6762.T", "6770.T", "6841.T", "6857.T", "6861.T",
    "6902.T", "6903.T", "6952.T", "6954.T", "6958.T", "6971.T", "6976.T",
    "6988.T", "7003.T", "7004.T", "7011.T", "7012.T", "7013.T", "7182.T",
    "7186.T", "7201.T", "7202.T", "7203.T", "7205.T", "7211.T", "7261.T",
    "7267.T", "7269.T", "7270.T", "7272.T", "7731.T", "7733.T", "7735.T",
    "7741.T", "7751.T", "7752.T", "7762.T", "7832.T", "7911.T", "7912.T",
    "7951.T", "7974.T", "8001.T", "8002.T", "8015.T", "8031.T", "8035.T",
    "8053.T", "8058.T", "8233.T", "8252.T", "8267.T", "8306.T", "8308.T",
    "8309.T", "8316.T", "8354.T", "8355.T", "8411.T", "8601.T", "8604.T",
    "8630.T", "8697.T", "8725.T", "8750.T", "8766.T", "8795.T", "9001.T",
    "9005.T", "9007.T", "9008.T", "9009.T", "9020.T", "9021.T", "9022.T",
    "9064.T", "9101.T", "9104.T", "9107.T", "9201.T", "9202.T", "9301.T",
    "9432.T", "9433.T", "9434.T", "9531.T", "9532.T", "9602.T", "9613.T",
    "9735.T", "9766.T", "9983.T", "9984.T",
]


def fetch_data(tickers: list[str]) -> pd.DataFrame:
    print(f"データ取得中 ({len(tickers)}銘柄)...")
    try:
        data = yf.download(
            tickers,
            period="30d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as e:
        print(f"[ERROR] データ取得失敗: {e}")
        return pd.DataFrame()
    return data


def score_stocks(data: pd.DataFrame, tickers: list[str]) -> list[dict]:
    if data.empty:
        return []

    results = []
    multi = isinstance(data.columns, pd.MultiIndex)

    for ticker in tickers:
        try:
            if multi:
                close = data["Close"][ticker].dropna()
                volume = data["Volume"][ticker].dropna()
            else:
                close = data["Close"].dropna()
                volume = data["Volume"].dropna()

            if len(close) < 5:
                continue

            last = float(close.iloc[-1])
            prev = float(close.iloc[-2])
            p5d = float(close.iloc[-5] if len(close) >= 5 else close.iloc[0])
            p20d = float(close.iloc[-20] if len(close) >= 20 else close.iloc[0])

            if prev == 0 or p5d == 0 or p20d == 0:
                continue

            day_chg = (last - prev) / prev * 100
            mom5 = (last - p5d) / p5d * 100
            mom20 = (last - p20d) / p20d * 100

            avg_vol = float(volume.iloc[:-1].mean()) if len(volume) > 1 else float(volume.iloc[-1])
            vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1.0

            ma20 = float(close.tail(20).mean())
            ma_score = (last - ma20) / ma20 * 100

            # モメンタム重視のスコアリング
            score = (
                mom5 * 0.35
                + mom20 * 0.25
                + max(vol_ratio - 1.0, 0.0) * 5.0 * 0.20
                + ma_score * 0.20
            )

            results.append({
                "ticker": ticker,
                "price": last,
                "day_chg": day_chg,
                "mom5": mom5,
                "mom20": mom20,
                "vol_ratio": vol_ratio,
                "score": score,
            })
        except Exception:
            continue

    return sorted(results, key=lambda x: x["score"], reverse=True)


def get_names(tickers: list[str]) -> dict[str, str]:
    names = {}
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).fast_info
            name = getattr(info, "display_name", None) or ticker
            names[ticker] = name
        except Exception:
            names[ticker] = ticker
    return names


def build_report(top: list[dict]) -> str:
    today = datetime.now().strftime("%Y年%m月%d日")
    tickers = [s["ticker"] for s in top]
    names = get_names(tickers)

    lines = [
        "日経225 本日のおすすめ銘柄レポート",
        f"集計日時: {today}",
        "=" * 55,
        "",
        "【選定基準】5日・20日モメンタム、出来高倍率、移動平均乖離率",
        "",
    ]

    for i, s in enumerate(top, 1):
        name = names.get(s["ticker"], s["ticker"])
        lines += [
            f"【第{i}位】 {name} ({s['ticker']})",
            f"  現在値     : ¥{s['price']:,.0f}",
            f"  前日比     : {s['day_chg']:+.2f}%",
            f"  5日騰落    : {s['mom5']:+.2f}%",
            f"  20日騰落   : {s['mom20']:+.2f}%",
            f"  出来高倍率 : {s['vol_ratio']:.2f}倍 (20日平均比)",
            f"  スコア     : {s['score']:.2f}",
            "",
        ]

    lines += [
        "─" * 55,
        "※技術的分析に基づく参考情報です。投資判断はご自身の責任でお願いします。",
    ]

    return "\n".join(lines)


def send_gmail(sender: str, recipient: str, password: str, subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
        print("  [Gmail] 送信完了")
    except Exception as e:
        print(f"  [Gmail] 送信失敗: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="日経225 毎朝おすすめ銘柄レポート",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  python nikkei_daily.py\n"
            "  python nikkei_daily.py --gmail-from you@gmail.com --gmail-to you@gmail.com \\\n"
            "    --gmail-password 'xxxx xxxx xxxx xxxx'\n"
            "\n"
            "環境変数でも設定可能:\n"
            "  GMAIL_FROM / GMAIL_TO / GMAIL_PASSWORD"
        ),
    )
    parser.add_argument("--gmail-from", default=os.environ.get("GMAIL_FROM"), metavar="アドレス",
                        help="送信元Gmailアドレス")
    parser.add_argument("--gmail-to", default=os.environ.get("GMAIL_TO"), metavar="アドレス",
                        help="送信先メールアドレス")
    parser.add_argument("--gmail-password", default=os.environ.get("GMAIL_PASSWORD"), metavar="パスワード",
                        help="Gmailアプリパスワード (16桁)")
    parser.add_argument("--top", type=int, default=5, metavar="N",
                        help="上位N銘柄を表示 (デフォルト: 5)")
    args = parser.parse_args()

    data = fetch_data(NIKKEI_225)
    if data.empty:
        sys.exit("[ERROR] データ取得に失敗しました。")

    ranked = score_stocks(data, NIKKEI_225)
    if not ranked:
        sys.exit("[ERROR] スコア計算に失敗しました。")

    report = build_report(ranked[: args.top])
    print(report)

    if args.gmail_from and args.gmail_to and args.gmail_password:
        today_str = datetime.now().strftime("%Y/%m/%d")
        subject = f"【日経225】本日のおすすめ銘柄 {today_str}"
        send_gmail(args.gmail_from, args.gmail_to, args.gmail_password, subject, report)
    elif any([args.gmail_from, args.gmail_to, args.gmail_password]):
        print("[WARN] Gmail通知を使う場合は --gmail-from / --gmail-to / --gmail-password をすべて指定してください。")


if __name__ == "__main__":
    main()
