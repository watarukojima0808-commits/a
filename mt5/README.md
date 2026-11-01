# SMMA_Rebound_Short (MT5 EA)

15分足のSMMAが下向きのとき、価格がSMMAまで戻ってきて反落した確定足でショートするEA。

## エントリー条件（1本前の確定足で判定）
1. SMMAが下向き（`InpSlopeBars`本前のSMMAより低い）
2. 足の高値がSMMAにタッチ（許容幅 `InpTouchTolPts`）
3. 終値がSMMAより下で確定
4. 陰線（`InpNeedBearCandle`）
5. 上ヒゲ比率（任意, `InpMinUpperWickRatio`）

## 決済
- 損切り: 足の高値とSMMAの高い方 + `InpSLBufferPts`
- 利確: 損切り幅 × `InpRR`
- 損切り幅が `InpMaxSLPts` を超えるときは見送り

## 導入方法
1. MT5 → ファイル → データフォルダを開く → `MQL5/Experts/` に `.mq5` を置く
2. MetaEditorで開いてコンパイル（F7）
3. ストラテジーテスターでバックテスト → デモ口座で動作確認 → 本番
