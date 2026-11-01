//+------------------------------------------------------------------+
//| SMMA_Rebound_Short.mq5                                           |
//| 15分足SMMAへの戻り(タッチ)からの反落でショートするEA              |
//+------------------------------------------------------------------+
#property version   "1.00"
#property description "下降中のSMMAに戻ってきて反落した確定足でショート"

#include <Trade\Trade.mqh>

//--- SMMA設定
input group "=== SMMA設定 ==="
input ENUM_TIMEFRAMES    InpTF            = PERIOD_M15;   // 判定する時間足
input int                InpSmmaPeriod    = 50;           // SMMA期間
input ENUM_APPLIED_PRICE InpSmmaPrice     = PRICE_CLOSE;  // SMMA適用価格
input int                InpSlopeBars     = 5;            // 傾き判定: N本前より下なら下降(0=無効)
input int                InpTouchTolPts   = 20;           // タッチ判定の許容幅(ポイント)

//--- 反発(反落)の条件
input group "=== 反落の条件 ==="
input bool               InpNeedBearCandle   = true;      // 確定足が陰線であること
input double             InpMinUpperWickRatio = 0.0;      // 上ヒゲ/足全体の最低比率(0=無効, 例0.4)

//--- 注文・リスク
input group "=== 注文・リスク ==="
input double             InpRiskPercent   = 1.0;          // 1回の許容損失(口座残高%)。0なら固定ロット
input double             InpFixedLot      = 0.01;         // 固定ロット
input int                InpSLBufferPts   = 50;           // 損切り: 足の高値/SMMAの上+何ポイント
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
double CalcLot(double entry, double sl)
{
   if(InpRiskPercent <= 0)
      return NormalizeLot(InpFixedLot);

   double profit = 0.0;
   if(!OrderCalcProfit(ORDER_TYPE_SELL, _Symbol, 1.0, entry, sl, profit) || profit >= 0)
   {
      Print("ロット計算に失敗。固定ロットを使用します");
      return NormalizeLot(InpFixedLot);
   }
   double lossPerLot = -profit;
   double riskMoney  = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPercent / 100.0;
   return NormalizeLot(riskMoney / lossPerLot);
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

   //--- SMMA取得 (smma[0]=1本前の確定足, smma[InpSlopeBars]=傾き比較用)
   int need = MathMax(1, InpSlopeBars + 1);
   double smma[];
   ArraySetAsSeries(smma, true);
   if(CopyBuffer(smmaHandle, 0, 1, need, smma) < need) return;

   //--- 1本前の確定足
   MqlRates bar[];
   ArraySetAsSeries(bar, true);
   if(CopyRates(_Symbol, InpTF, 1, 1, bar) < 1) return;

   double o = bar[0].open, h = bar[0].high, l = bar[0].low, c = bar[0].close;
   double s = smma[0];
   double tol = InpTouchTolPts * _Point;

   //--- 条件1: SMMAが下向き
   bool slopeDown = (InpSlopeBars <= 0) || (smma[0] < smma[InpSlopeBars]);
   //--- 条件2: 高値がSMMAに届いた(タッチ)
   bool touched   = (h >= s - tol);
   //--- 条件3: 終値はSMMAより下(上抜けできずに戻された)
   bool rejected  = (c < s);
   //--- 条件4: 陰線
   bool bearOK    = !InpNeedBearCandle || (c < o);
   //--- 条件5: 上ヒゲ比率
   bool wickOK    = true;
   if(InpMinUpperWickRatio > 0)
   {
      double range = h - l;
      double upper = h - MathMax(o, c);
      wickOK = (range > 0 && upper / range >= InpMinUpperWickRatio);
   }

   if(!(slopeDown && touched && rejected && bearOK && wickOK))
      return;

   //--- 注文価格の計算
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double sl  = NormalizeDouble(MathMax(h, s) + InpSLBufferPts * _Point, _Digits);
   double dist = sl - bid;
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

   double tp  = NormalizeDouble(bid - dist * InpRR, _Digits);
   double lot = CalcLot(bid, sl);

   if(trade.Sell(lot, _Symbol, 0.0, sl, tp, "SMMA rebound short"))
      PrintFormat("SELL %.2f lot  SL=%s  TP=%s  SMMA=%s",
                  lot, DoubleToString(sl, _Digits), DoubleToString(tp, _Digits),
                  DoubleToString(s, _Digits));
   else
      PrintFormat("発注失敗: %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
}
//+------------------------------------------------------------------+
