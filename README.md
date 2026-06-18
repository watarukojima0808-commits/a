# 日経225 毎朝おすすめ銘柄レポート

日経225の全構成銘柄を毎朝8:30 JSTに分析し、モメンタム・出来高・移動平均乖離率でスコアリングしたTop5銘柄をGmailで通知します。

## セットアップ

```bash
pip install -r requirements.txt
```

## 使い方

### ローカル実行

```bash
# ターミナルに出力するだけ
python nikkei_daily.py

# Gmail通知あり
python nikkei_daily.py \
  --gmail-from you@gmail.com \
  --gmail-to you@gmail.com \
  --gmail-password "xxxx xxxx xxxx xxxx"

# 上位10銘柄を表示
python nikkei_daily.py --top 10
```

### GitHub Actions（毎朝8:30 JST 自動実行）

1. GitHubリポジトリの **Settings → Secrets and variables → Actions** を開く
2. 以下の3つのシークレットを追加:

| シークレット名 | 内容 |
|---|---|
| `GMAIL_FROM` | 送信元Gmailアドレス |
| `GMAIL_TO` | 送信先メールアドレス |
| `GMAIL_PASSWORD` | Gmailアプリパスワード（16桁） |

3. ワークフローが月〜金の8:30 JSTに自動実行されます
4. **Actions → 日経225 毎朝おすすめ銘柄レポート → Run workflow** から手動実行も可能

> **Gmailアプリパスワードの取得方法**: Googleアカウント → セキュリティ → 2段階認証を有効化 → アプリパスワード → 16桁のパスワードを生成

## 出力例

```
日経225 本日のおすすめ銘柄レポート
集計日時: 2024年01月15日
=======================================================

【選定基準】5日・20日モメンタム、出来高倍率、移動平均乖離率

【第1位】 Tokyo Electron Ltd (8035.T)
  現在値     : ¥38,520
  前日比     : +3.12%
  5日騰落    : +7.45%
  20日騰落   : +15.23%
  出来高倍率 : 2.31倍 (20日平均比)
  スコア     : 8.94

...（第2〜5位も同形式）

※技術的分析に基づく参考情報です。投資判断はご自身の責任でお願いします。
```

## 注意事項

- データはYahoo Financeから取得しています（前営業日の終値ベース）
- 本スクリプトの情報は投資を推奨するものではありません

---

# メルカリ 価格アラートボット

指定したキーワードと上限価格でメルカリを定期監視し、新着出品をターミナルに表示します。

## セットアップ

Python 3.x のみ必要です（追加ライブラリ不要）。

## 使い方

```bash
python mercari_alert.py <キーワード> <上限価格円> [--interval 秒]
```

### 例

```bash
# Nintendo Switch を ¥20,000 以下で60秒ごとに監視
python mercari_alert.py "Nintendo Switch" 20000

# AirPods Pro を ¥15,000 以下で2分ごとに監視
python mercari_alert.py "AirPods Pro" 15000 --interval 120
```

## 出力例

```
メルカリ監視開始
  キーワード : Nintendo Switch
  上限価格   : ¥20,000
  監視間隔   : 60秒
  停止       : Ctrl+C

初期データ取得中...
既存 30 件を確認。以降の新着を通知します。

============================================================
[14:35:22] 新着 2 件!  「Nintendo Switch」 ¥20,000以下
============================================================
  ¥ 18,000  Nintendo Switch 本体 ジョイコン付き
            https://jp.mercari.com/item/m12345678901
  ¥ 15,500  ニンテンドースイッチ 本体のみ
            https://jp.mercari.com/item/m98765432109
```

## 注意事項

- 監視間隔は最低10秒以上にしてください（サーバー負荷対策）
- Ctrl+C で停止できます
