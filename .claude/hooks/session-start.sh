#!/bin/bash
set -euo pipefail

# 依存ライブラリをインストール
if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  pip install -q -r "$CLAUDE_PROJECT_DIR/requirements.txt"
fi

# 平日の朝7:00〜9:30 JSTなら日経レポートを表示
HOUR_JST=$(TZ=Asia/Tokyo date +%H)
MINUTE_JST=$(TZ=Asia/Tokyo date +%M)
DOW=$(TZ=Asia/Tokyo date +%u)  # 1=月曜〜7=日曜

if [ "$DOW" -le 5 ] && \
   { [ "$HOUR_JST" -eq 7 ] || \
     [ "$HOUR_JST" -eq 8 ] || \
     { [ "$HOUR_JST" -eq 9 ] && [ "$MINUTE_JST" -lt 30 ]; }; }; then
  echo "=== 日経225 本日のおすすめ銘柄 ==="
  python "$CLAUDE_PROJECT_DIR/nikkei_daily.py" 2>/dev/null || true
fi
