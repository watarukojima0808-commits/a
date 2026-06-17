#!/usr/bin/env python3
"""
Mercari price alert bot
Usage: python mercari_alert.py "Nintendo Switch" 20000 --interval 60
       python mercari_alert.py "Nintendo Switch" 20000 --gmail-from you@gmail.com --gmail-to you@gmail.com --gmail-password "xxxx xxxx xxxx xxxx"
"""

import time
import sys
import json
import argparse
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

import urllib.request
import urllib.error


SEARCH_API = "https://api.mercari.jp/v2/entities:search"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "X-Platform": "web",
    "Origin": "https://jp.mercari.com",
    "Referer": "https://jp.mercari.com/",
    "DPoP": "dummy",
}


def build_payload(keyword: str, max_price: int, limit: int = 30) -> dict:
    return {
        "userId": "",
        "pageToken": "",
        "searchSessionId": "",
        "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
        "thumbnailTypes": [],
        "searchCondition": {
            "keyword": keyword,
            "excludeKeyword": "",
            "sort": "SORT_CREATED_TIME",
            "order": "ORDER_DESC",
            "status": ["STATUS_ON_SALE"],
            "sizeId": [],
            "categoryId": [],
            "brandId": [],
            "sellerId": [],
            "priceMin": 0,
            "priceMax": max_price,
            "itemConditionId": [],
            "shippingPayerId": [],
            "shippingFromArea": [],
            "shippingMethod": [],
            "colorId": [],
            "hasCoupon": False,
            "attributes": [],
            "itemTypes": [],
            "skuIds": [],
        },
        "defaultDatasets": ["DATASET_TYPE_MERCARI"],
        "serviceFrom": "suruga",
        "withItemBrand": True,
        "withItemSize": False,
        "withItemPromotions": False,
        "withItemSizes": False,
        "attractiveItemsMaxItemCount": 0,
        "followUpItemsMaxItemCount": 0,
        "limit": limit,
    }


def fetch_items(keyword: str, max_price: int) -> list[dict]:
    body = json.dumps(build_payload(keyword, max_price)).encode()
    req = urllib.request.Request(SEARCH_API, data=body, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()).get("items", [])
    except urllib.error.HTTPError as e:
        print(f"\n[WARN] HTTP {e.code} — {e.reason}")
        return []
    except Exception as e:
        print(f"\n[WARN] 通信エラー: {e}")
        return []


def send_gmail(gmail_from: str, gmail_to: str, password: str, subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = gmail_from
    msg["To"] = gmail_to
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(gmail_from, password)
            smtp.send_message(msg)
        print("  [Gmail] 送信完了")
    except Exception as e:
        print(f"  [Gmail] 送信失敗: {e}")


def format_items_text(items: list[dict]) -> str:
    lines = []
    for item in items:
        item_id = item.get("id", "")
        name = item.get("name", "不明")
        price = item.get("price", 0)
        url = f"https://jp.mercari.com/item/{item_id}"
        lines.append(f"¥{price:,}  {name}\n{url}")
    return "\n\n".join(lines)


def print_item(item: dict) -> None:
    item_id = item.get("id", "")
    name = item.get("name", "不明")[:50]
    price = item.get("price", 0)
    url = f"https://jp.mercari.com/item/{item_id}"
    print(f"  ¥{price:>7,}  {name}")
    print(f"            {url}")


def run(keyword: str, max_price: int, interval: int, gmail: dict | None) -> None:
    print("メルカリ監視開始")
    print(f"  キーワード : {keyword}")
    print(f"  上限価格   : ¥{max_price:,}")
    print(f"  監視間隔   : {interval}秒")
    print(f"  Gmail通知  : {'有効' if gmail else '無効'}")
    print(f"  停止       : Ctrl+C\n")

    seen: set[str] = set()

    print("初期データ取得中...")
    initial = fetch_items(keyword, max_price)
    seen.update(i["id"] for i in initial if "id" in i)
    print(f"既存 {len(seen)} 件を確認。以降の新着を通知します。\n")

    while True:
        time.sleep(interval)
        now = datetime.now().strftime("%H:%M:%S")
        items = fetch_items(keyword, max_price)
        new_items = [i for i in items if i.get("id") not in seen]

        if new_items:
            bar = "=" * 60
            print(f"\n{bar}")
            print(f"[{now}] 新着 {len(new_items)} 件!  「{keyword}」 ¥{max_price:,}以下")
            print(bar)
            for item in new_items:
                print_item(item)
            print()

            if gmail:
                subject = f"【メルカリ】新着 {len(new_items)} 件「{keyword}」¥{max_price:,}以下"
                body = format_items_text(new_items)
                send_gmail(gmail["from"], gmail["to"], gmail["password"], subject, body)

            seen.update(i["id"] for i in new_items if "id" in i)
        else:
            print(f"[{now}] 新着なし ({len(seen)} 件監視中)", end="\r", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="メルカリ価格アラートボット",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  python mercari_alert.py 'Nintendo Switch' 20000\n"
            "  python mercari_alert.py 'AirPods Pro' 15000 --interval 120 \\\n"
            "    --gmail-from you@gmail.com --gmail-to you@gmail.com \\\n"
            "    --gmail-password 'xxxx xxxx xxxx xxxx'"
        ),
    )
    parser.add_argument("keyword", help="検索キーワード")
    parser.add_argument("max_price", type=int, help="上限価格 (円)")
    parser.add_argument("--interval", type=int, default=60, metavar="秒", help="監視間隔 (デフォルト: 60秒)")
    parser.add_argument("--gmail-from", metavar="アドレス", help="送信元Gmailアドレス")
    parser.add_argument("--gmail-to", metavar="アドレス", help="送信先メールアドレス")
    parser.add_argument("--gmail-password", metavar="パスワード", help="Gmailアプリパスワード (16桁)")
    args = parser.parse_args()

    if args.max_price <= 0:
        sys.exit("上限価格は1以上を指定してください。")
    if args.interval < 10:
        sys.exit("監視間隔は10秒以上を指定してください (サーバー負荷対策)。")

    gmail = None
    if args.gmail_from or args.gmail_to or args.gmail_password:
        if not all([args.gmail_from, args.gmail_to, args.gmail_password]):
            sys.exit("Gmail通知を使う場合は --gmail-from / --gmail-to / --gmail-password をすべて指定してください。")
        gmail = {
            "from": args.gmail_from,
            "to": args.gmail_to,
            "password": args.gmail_password,
        }

    try:
        run(args.keyword, args.max_price, args.interval, gmail)
    except KeyboardInterrupt:
        print("\n\n監視を終了しました。")


if __name__ == "__main__":
    main()
