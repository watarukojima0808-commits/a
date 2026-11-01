//+------------------------------------------------------------------+
//| SMMA_Rebound.mq5                                                 |
//| SMMAへの戻り(タッチ)からの反発でエントリーするEA (ロング/ショート) |
//|                                                                  |
//| A) トレンド押し目/戻り : SMMAの傾き方向へ、SMMAタッチ後の反発で入る |
//| B) ブレイク後のリテスト: SMMAを抜けた後に一旦戻ってSMMAで止まったら |
//|    抜けた方向へ入る (傾きがまだ変わっていなくてもOK)              |
//|                                                                  |
//| 値幅系の設定はATR倍率で指定 (ゴールドなど値動きが変わる銘柄向け)   |
//+------------------------------------------------------------------+
#property version   "3.00"
#property description "SMMAタッチからの反発でロング/ショート + レンジフィルター (XAUUSD想定)"

#include <Trade\Trade.mqh>

//--- SMMA設定
input group "=== SMMA設定 ==="
input ENUM_TIMEFRAMES    InpTF            = PERIOD_M15;   // 判定する時間足
input int                InpSmmaPeriod    = 20;           // SMMA期間
input ENUM_APPLIED_PRICE InpSmmaPrice     = PRICE_CLOSE;  // SMMA適用価格
input int                InpSlopeBars     = 5;            // 傾き判定: N本前と比較(0=傾き条件なし)
input int                InpAtrPeriod     = 14;           // ATR期間(値幅の基準)
input double             InpTouchTolAtr   = 0.10;         // タッチ判定の許容幅(ATR倍率)

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
input bool               InpNeedCandleDir = true;         // ロング=陽線 / ショート=陰線 で確定
input double             InpMinWickRatio  = 0.0;          // 反発側ヒゲ/足全体の最低比率(0=無効, 例0.4)

//--- レンジフィルター
input group "=== レンジフィルター ==="
input double             InpMinSlopeAtr   = 0.30;         // [1] SMMAの傾きの強さ: N本での変化がATR×これ以上(0=無効)
input int                InpCrossLookback = 20;           // [2] SMMAクロス回数を数える本数(0=無効)
input int                InpMaxCrosses    = 3;            // [2] その間のクロスがこの回数を超えたらレンジ扱い
input bool               InpUseAdx        = false;        // [3] ADXフィルターを使う
input int                InpAdxPeriod     = 14;           // [3] ADX期間
input double             InpMinAdx        = 20.0;         // [3] ADXがこの値未満ならレンジ扱い
input bool               InpUseHTF        = true;         // [4] 上位足の向きと一致する時だけ
input ENUM_TIMEFRAMES    InpHTF           = PERIOD_H1;    // [4] 上位足
input int                InpHTFPeriod     = 20;           // [4] 上位足SMMA期間

//--- 注文・リスク
input group "=== 注文・リスク ==="
input double             InpRiskPercent   = 1.0;          // 1回の許容損失(口座残高%)。0なら固定ロット
input double             InpFixedLot      = 0.01;         // 固定ロット
input double             InpSLBufferAtr   = 0.20;         // 損切り: 足の高安/SMMAの外側+ATR×これ
input double             InpMaxSLAtr      = 2.0;          // 損切り幅の上限(ATR倍率, 0=無制限)。超えたら見送り
input double             InpRR            = 1.5;          // 利確 = 損切り幅 × この倍率
input int                InpMaxSpreadPts  = 50;           // 許容スプレッド(ポイント。2桁表示のゴールドなら50=0.50ドル)
input int                InpSlippagePts   = 30;           // 許容スリッページ(ポイント)
input ulong              InpMagic         = 20261101;     // マジックナンバー

//--- 時間フィルター
input group "=== 時間フィルター(サーバー時間) ==="
input bool               InpUseTimeFilter = false;        // 時間フィルターを使う
input int                InpStartHour     = 9;            // 開始時(含む)
input int                InpEndHour       = 23;           // 終了時(含まない)

CTrade   trade;
int      smmaHandle = INVALID_HANDLE;
int      atrHandle  = INVALID_HANDLE;
int      adxHandle  = INVALID_HANDLE;
int      htfHandle  = INVALID_HANDLE;
datetime lastBarTime = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   smmaHandle = iMA(_Symbol, InpTF, InpSmmaPeriod, 0, MODE_SMMA, InpSmmaPrice);
   atrHandle  = iATR(_Symbol, InpTF, InpAtrPeriod);
   if(smmaHandle == INVALID_HANDLE || atrHandle == INVALID_HANDLE)
   {
      Print("インジケーターの作成に失敗: ", GetLastError());
      return INIT_FAILED;
   }
   if(InpUseAdx)
   {
      adxHandle = iADX(_Symbol, InpTF, InpAdxPeriod);
      if(adxHandle == INVALID_HANDLE) { Print("ADXの作成に失敗"); return INIT_FAILED; }
   }
   if(InpUseHTF)
   {
      htfHandle = iMA(_Symbol, InpHTF, InpHTFPeriod, 0, MODE_SMMA, PRICE_CLOSE);
      if(htfHandle == INVALID_HANDLE) { Print("上位足SMMAの作成に失敗"); return INIT_FAILED; }
   }
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpSlippagePts);
   trade.SetTypeFillingBySymbol(_Symbol);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(smmaHandle != INVALID_HANDLE) IndicatorRelease(smmaHandle);
   if(atrHandle  != INVALID_HANDLE) IndicatorRelease(atrHandle);
   if(adxHandle  != INVALID_HANDLE) IndicatorRelease(adxHandle);
   if(htfHandle  != INVALID_HANDLE) IndicatorRelease(htfHandle);
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
//| 直近N本で終値がSMMAを何回またいだか                               |
//+------------------------------------------------------------------+
int CountCrosses(const MqlRates &rates[], const double &smma[], int count)
{
   int n = MathMin(InpCrossLookback, count - 1);
   int crosses = 0;
   for(int k = 0; k < n; k++)
   {
      bool aboveNow  = rates[k].close     > smma[k];
      bool abovePrev = rates[k + 1].close > smma[k + 1];
      if(aboveNow != abovePrev) crosses++;
   }
   return crosses;
}

//+------------------------------------------------------------------+
//| 上位足の向き: +1=上 / -1=下 / 0=不明                              |
//| 上位足の確定足の終値がSMMAより上で、SMMAも上向きなら「上」          |
//+------------------------------------------------------------------+
int HTFDirection()
{
   double hs[];
   ArraySetAsSeries(hs, true);
   if(CopyBuffer(htfHandle, 0, 1, 4, hs) < 4) return 0;
   double hc = iClose(_Symbol, InpHTF, 1);
   if(hc == 0) return 0;
   if(hc > hs[0] && hs[0] > hs[3]) return  1;
   if(hc < hs[0] && hs[0] < hs[3]) return -1;
   return 0;
}

//+------------------------------------------------------------------+
//| 両方向共通のレンジ判定。trueならレンジ(=見送り)                   |
//+------------------------------------------------------------------+
bool IsRangeMarket(const MqlRates &rates[], const double &smma[], int count)
{
   // [2] SMMAを何度もまたいでいる = 方向感がない
   if(InpCrossLookback > 0)
   {
      int crosses = CountCrosses(rates, smma, count);
      if(crosses > InpMaxCrosses)
      {
         PrintFormat("レンジ判定(SMMAクロス %d回)のため見送り", crosses);
         return true;
      }
   }
   // [3] ADXが低い = トレンドが弱い
   if(InpUseAdx)
   {
      double adx[];
      ArraySetAsSeries(adx, true);
      if(CopyBuffer(adxHandle, 0, 1, 1, adx) < 1) return true;
      if(adx[0] < InpMinAdx)
      {
         PrintFormat("レンジ判定(ADX %.1f)のため見送り", adx[0]);
         return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| ブレイク後リテスト判定                                           |
//| isLong=true : 直近N本以内にSMMA下で確定した足があり、               |
//|               その後少なくとも1本SMMA上で確定(=上抜け確定)している   |
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
   return false;            // 期間内にブレイクなし → リテストではない
}

//+------------------------------------------------------------------+
//| シグナル判定                                                     |
//+------------------------------------------------------------------+
bool CheckSignal(bool isLong, const MqlRates &rates[], const double &smma[], int count,
                 double atr, int htfDir, string &reason)
{
   // [4] 上位足の向きと一致しているか
   if(InpUseHTF && htfDir != (isLong ? 1 : -1)) return false;

   double o = rates[0].open, h = rates[0].high, l = rates[0].low, c = rates[0].close;
   double s = smma[0];
   double tol = InpTouchTolAtr * atr;

   // A: トレンド押し目/戻り (傾きの向き + [1]傾きの強さ)
   bool slopeOK = false;
   if(InpSlopeBars <= 0)
      slopeOK = true;
   else if(InpSlopeBars < count)
   {
      double change = smma[0] - smma[InpSlopeBars];
      if(!isLong) change = -change;
      slopeOK = (change > 0) && (change >= InpMinSlopeAtr * atr);
   }

   // B: ブレイク後リテスト
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

   reason = slopeOK ? "trend" : "retest";
   return true;
}

//+------------------------------------------------------------------+
void OpenTrade(bool isLong, const MqlRates &bar, double s, double atr, string reason)
{
   double price, sl, tp, dist;
   double buffer = InpSLBufferAtr * atr;
   if(isLong)
   {
      price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      sl    = NormalizeDouble(MathMin(bar.low, s) - buffer, _Digits);
      dist  = price - sl;
   }
   else
   {
      price = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      sl    = NormalizeDouble(MathMax(bar.high, s) + buffer, _Digits);
      dist  = sl - price;
   }
   if(dist <= 0) return;

   if(InpMaxSLAtr > 0 && dist > InpMaxSLAtr * atr)
   {
      PrintFormat("損切り幅が大きすぎるため見送り: %s (ATR %s)",
                  DoubleToString(dist, _Digits), DoubleToString(atr, _Digits));
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
   int count = MathMax(MathMax(InpSlopeBars, InpRetestLookback), InpCrossLookback) + 1;
   double smma[], atrBuf[];
   MqlRates rates[];
   ArraySetAsSeries(smma, true);
   ArraySetAsSeries(atrBuf, true);
   ArraySetAsSeries(rates, true);
   if(CopyBuffer(smmaHandle, 0, 1, count, smma) < count) return;
   if(CopyRates(_Symbol, InpTF, 1, count, rates) < count) return;
   if(CopyBuffer(atrHandle, 0, 1, 1, atrBuf) < 1) return;
   double atr = atrBuf[0];
   if(atr <= 0) return;

   if(IsRangeMarket(rates, smma, count)) return;

   int htfDir = InpUseHTF ? HTFDirection() : 0;

   string reason = "";
   if(InpAllowLong && CheckSignal(true, rates, smma, count, atr, htfDir, reason))
   {
      OpenTrade(true, rates[0], smma[0], atr, reason);
      return;
   }
   if(InpAllowShort && CheckSignal(false, rates, smma, count, atr, htfDir, reason))
      OpenTrade(false, rates[0], smma[0], atr, reason);
}
//+------------------------------------------------------------------+
