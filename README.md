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
