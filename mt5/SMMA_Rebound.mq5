//+------------------------------------------------------------------+
//| SMMA_Rebound.mq5                                                 |
//| SMMAへの戻り(タッチ)からの反発でエントリーするEA (ロング/ショート) |
//|                                                                  |
//| A) トレンド押し目/戻り : SMMAの傾き方向へ、SMMAタッチ後の反発で入る |
//| B) ブレイク後のリテスト: SMMAを抜けた後に一旦戻ってSMMAで止まったら |
//|    抜けた方向へ入る (傾きがまだ変わっていなくてもOK)              |
//+------------------------------------------------------------------+
#property version   "2.00"
#property description "SMMAタッチからの反発でロング/ショート (押し目・戻り・ブレイク後リテスト)"

#include <Trade\Trade.mqh>

//--- SMMA設定
input group "=== SMMA設定 ==="
input ENUM_TIMEFRAMES    InpTF            = PERIOD_M15;   // 判定する時間足
input int                InpSmmaPeriod    = 50;           // SMMA期間
input ENUM_APPLIED_PRICE InpSmmaPrice     = PRICE_CLOSE;  // SMMA適用価格
input int                InpSlopeBars     = 5;            // 傾き判定: N本前と比較(0=傾き条件なし)
input int                InpTouchTolPts   = 20;           // タッチ判定の許容幅(ポイント)

//--- 売買方向
input group "=== 売買方向 ==="
input bool               InpAllowLong     = true;         // ロングを許可
input bool               InpAllowShort    = true;         // ショートを許可

//--- ブレイク後リテスト
input group "=== ブレイク後リテスト ==="
input bool               InpUseRetest     = true;         // SMMAブレイク後の戻りでもエントリー
input int                InpRetestLookback = 10;          // 何本以内のブレイクを有効とするか

//--- 反発の条件
input group "=== 反発の条件 ==="
input bool               InpNeedCandleDir    = true;      // ロング=陽線 / ショート=陰線 で確定
input double             InpMinWickRatio     = 0.0;       // 反発側ヒゲ/足全体の最低比率(0=無効, 例0.4)

//--- 注文・リスク
input group "=== 注文・リスク ==="
input double             InpRiskPercent   = 1.0;          // 1回の許容損失(口座残高%)。0なら固定ロット
input double             InpFixedLot      = 0.01;         // 固定ロット
input int                InpSLBufferPts   = 50;           // 損切り: 足の高安/SMMAの外側+何ポイント
input int                InpMaxSLPts      = 500;          // 損切り幅の上限(ポイント, 0=無制限)。超えたら見送り
input double             InpRR            = 1.5;          // 利確 = 損切り幅 × この倍率
input int                InpMaxSpreadPts  = 30;           // 許容スプレッド(ポイント)
input int                InpSlippagePts   = 10;           // 許容スリッページ(ポイント)
input ulong              InpMagic         = 20261101;     // マジックナンバー

//--- 時間フィルター
input group "=== 時間フィルター(サーバー時間) ==="
input bool               InpUseTimeFilter = false;        // 時間フィルターを使う
input int                InpStartHour     = 9;            // 開始時(含む)
input int                InpEndHour       = 23;           // 終了時(含まない)

CTrade   trade;
int      smmaHandle = INVALID_HANDLE;
datetime lastBarTime = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   smmaHandle = iMA(_Symbol, InpTF, InpSmmaPeriod, 0, MODE_SMMA, InpSmmaPrice);
   if(smmaHandle == INVALID_HANDLE)
   {
      Print("SMMAハンドルの作成に失敗: ", GetLastError());
      return INIT_FAILED;
   }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePts);
   trade.SetTypeFillingBySymbol(_Symbol);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(smmaHandle != INVALID_HANDLE)
      IndicatorRelease(smmaHandle);
}

//+------------------------------------------------------------------+
bool IsNewBar()
{
   datetime t = iTime(_Symbol, InpTF, 0);
   if(t == 0 || t == lastBarTime)
      return false;
   lastBarTime = t;
   return true;
}

//+------------------------------------------------------------------+
bool HasPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == _Symbol &&
         (ulong)PositionGetInteger(POSITION_MAGIC) == InpMagic)
         return true;
   }
   return false;
}

//+------------------------------------------------------------------+
bool InTradingHours()
{
   if(!InpUseTimeFilter) return true;
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   if(InpStartHour <= InpEndHour)
      return (dt.hour >= InpStartHour && dt.hour < InpEndHour);
   // 日付をまたぐ設定 (例: 22時〜3時)
   return (dt.hour >= InpStartHour || dt.hour < InpEndHour);
}

//+------------------------------------------------------------------+
double NormalizeLot(double lot)
{
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double stepLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(stepLot <= 0) stepLot = 0.01;
   lot = MathFloor(lot / stepLot) * stepLot;
   lot = MathMax(minLot, MathMin(maxLot, lot));
   return NormalizeDouble(lot, 2);
}

//+------------------------------------------------------------------+
double CalcLot(ENUM_ORDER_TYPE type, double entry, double sl)
{
   if(InpRiskPercent <= 0)
      return NormalizeLot(InpFixedLot);

   double profit = 0.0;
   if(!OrderCalcProfit(type, _Symbol, 1.0, entry, sl, profit) || profit >= 0)
   {
      Print("ロット計算に失敗。固定ロットを使用します");
      return NormalizeLot(InpFixedLot);
   }
   double riskMoney = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;
   return NormalizeLot(riskMoney / (-profit));
}

//+------------------------------------------------------------------+
//| ブレイク後リテスト判定                                           |
//| isLong=true : 直近N本以内にSMMA下で確定した足があり、               |
//|               その後少なくとも1本SMMA上で確定(=上抜け確定)している   |
//| isLong=false: その逆                                             |
//| rates/smma は series 配列で [0]=1本前の確定足                      |
//+------------------------------------------------------------------+
bool IsRetest(bool isLong, const MqlRates &rates[], const double &smma[], int count)
{
   if(!InpUseRetest) return false;
   int last = MathMin(InpRetestLookback, count - 1);
   for(int k = 1; k <= last; k++)
   {
      bool otherSide = isLong ? (rates[k].close < smma[k]) : (rates[k].close > smma[k]);
      if(otherSide)
         return (k >= 2);   // k=1 だとブレイク足の直後すぐで、ブレイクが確定していない
   }
   return false;            // 期間内にブレイクなし(=ずっと同じ側) → リテストではない
}

//+------------------------------------------------------------------+
//| シグナル判定                                                     |
//+------------------------------------------------------------------+
bool CheckSignal(bool isLong, const MqlRates &rates[], const double &smma[], int count, string &reason)
{
   double o = rates[0].open, h = rates[0].high, l = rates[0].low, c = rates[0].close;
   double s = smma[0];
   double tol = InpTouchTolPts * _Point;

   // 傾き (A: トレンド押し目/戻り)
   bool slopeOK = false;
   if(InpSlopeBars <= 0)
      slopeOK = true;
   else if(InpSlopeBars < count)
      slopeOK = isLong ? (smma[0] > smma[InpSlopeBars]) : (smma[0] < smma[InpSlopeBars]);

   // ブレイク後リテスト (B)
   bool retestOK = IsRetest(isLong, rates, smma, count);

   if(!slopeOK && !retestOK) return false;

   // SMMAタッチ & 反対側へ抜けずに確定
   bool touched, held, candleOK, wickOK = true;
   double range = h - l;
   if(isLong)
   {
      touched  = (l <= s + tol);
      held     = (c > s);
      candleOK = !InpNeedCandleDir || (c > o);
      if(InpMinWickRatio > 0)
         wickOK = (range > 0 && (MathMin(o, c) - l) / range >= InpMinWickRatio);
   }
   else
   {
      touched  = (h >= s - tol);
      held     = (c < s);
      candleOK = !InpNeedCandleDir || (c < o);
      if(InpMinWickRatio > 0)
         wickOK = (range > 0 && (h - MathMax(o, c)) / range >= InpMinWickRatio);
   }

   if(!(touched && held && candleOK && wickOK)) return false;

   reason = retestOK ? "retest" : "trend";
   return true;
}

//+------------------------------------------------------------------+
void OpenTrade(bool isLong, const MqlRates &bar, double s, string reason)
{
   double price, sl, tp, dist;
   if(isLong)
   {
      price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      sl    = NormalizeDouble(MathMin(bar.low, s) - InpSLBufferPts * _Point, _Digits);
      dist  = price - sl;
   }
   else
   {
      price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      sl    = NormalizeDouble(MathMax(bar.high, s) + InpSLBufferPts * _Point, _Digits);
      dist  = sl - price;
   }
   if(dist <= 0) return;

   if(InpMaxSLPts > 0 && dist > InpMaxSLPts * _Point)
   {
      PrintFormat("損切り幅が大きすぎるため見送り: %.0f pts", dist / _Point);
      return;
   }
   long stopsLevel = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if(dist < stopsLevel * _Point)
   {
      Print("損切り幅がブローカーの最小距離未満のため見送り");
      return;
   }

   tp = NormalizeDouble(isLong ? price + dist * InpRR : price - dist * InpRR, _Digits);
   ENUM_ORDER_TYPE type = isLong ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double lot = CalcLot(type, price, sl);
   string cmt = "SMMA " + reason;

   bool ok = isLong ? trade.Buy(lot, _Symbol, 0.0, sl, tp, cmt)
                    : trade.Sell(lot, _Symbol, 0.0, sl, tp, cmt);
   if(ok)
      PrintFormat("%s %.2f lot (%s)  SL=%s  TP=%s  SMMA=%s",
                  isLong ? "BUY" : "SELL", lot, reason,
                  DoubleToString(sl, _Digits), DoubleToString(tp, _Digits),
                  DoubleToString(s, _Digits));
   else
      PrintFormat("発注失敗: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
}

//+------------------------------------------------------------------+
void OnTick()
{
   if(!IsNewBar())        return;   // 足の確定ごとに1回だけ判定
   if(HasPosition())      return;   // 同時保有は1ポジションまで
   if(!InTradingHours())  return;

   long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
   if(InpMaxSpreadPts > 0 && spread > InpMaxSpreadPts)
   {
      PrintFormat("スプレッド拡大のため見送り: %d", spread);
      return;
   }

   // [0]=1本前の確定足 から過去へ
   int count = MathMax(InpSlopeBars, InpRetestLookback) + 1;
   double smma[];
   MqlRates rates[];
   ArraySetAsSeries(smma, true);
   ArraySetAsSeries(rates, true);
   if(CopyBuffer(smmaHandle, 0, 1, count, smma) < count) return;
   if(CopyRates(_Symbol, InpTF, 1, count, rates) < count) return;

   string reason = "";
   if(InpAllowLong && CheckSignal(true, rates, smma, count, reason))
   {
      OpenTrade(true, rates[0], smma[0], reason);
      return;
   }
   if(InpAllowShort && CheckSignal(false, rates, smma, count, reason))
      OpenTrade(false, rates[0], smma[0], reason);
}
//+------------------------------------------------------------------+
