package com.agentbridge.kalshihud;

/** Observation grammar, not a calibrated entry model. All prices in dollars. */
public final class Rules {
 /** Coinbase vs strike only. Not FIRST. Waits for no Kalshi 52/48. */
 public static String decideT0Call(double deltaUsd) {
  if(!Double.isFinite(deltaUsd)||deltaUsd==0)return "";
  return deltaUsd>0?"YES":"NO";
 }
 public static String firstObsStrength(String side,double yesMid) {
  if(!isSide(side)||!Double.isFinite(yesMid)||yesMid<0||yesMid>1)return "no directional lock yet";
  double px="NO".equals(side)?1-yesMid:yesMid;
  return "LOCK PRICE · "+Math.round(100*px)+"¢ · market price, not tested confidence";
 }
 /** Health warning. A loss streak does not make a win due. */
 public static String streakHealth(String outcomes,double baselineP) {
  if(outcomes==null||outcomes.isEmpty())return "No graded first calls";
  char last=outcomes.charAt(outcomes.length()-1);
  if(last!='W'&&last!='L')return "No graded first calls";
  int run=0;
  for(int i=outcomes.length()-1;i>=0&&outcomes.charAt(i)==last;i--)run++;
  if(last=='L'){
   double p=baselineP>0&&baselineP<1?baselineP:.63;
   double chance=Math.pow(1-p,run);
   return run+" losses in a row. If the book is "+Math.round(100*p)+"% this run is about "+
     (Math.round(chance*1000)/10.0)+"% for one fixed block of "+run+" independent calls — not a due win or a next-call probability.";
  }
  return run+" wins in a row. Hot streaks are not a buy signal.";
 }
 public static String flipWatch(String side,double yesMid,double deltaUsd,long secondsLeft,int crossings) {
  if(!isSide(side)||!Double.isFinite(yesMid)||!Double.isFinite(deltaUsd))return "";
  if(secondsLeft<=60||secondsLeft>180||yesMid<0||yesMid>1)return "";
  double px="NO".equals(side)?1-yesMid:yesMid;
  boolean opposing=("YES".equals(side)&&deltaUsd<0)||("NO".equals(side)&&deltaUsd>0);
  if(px>=.70&&(crossings>=2||opposing))return "FLIP WATCH · called side still rich with opposing spot or crossings";
  return "";
 }
 public static String skipRich(double calledMid) {
  if(!Double.isFinite(calledMid)||calledMid<0||calledMid>1)return "";
  if(calledMid>=.90)return "HIGH PRICE · ≥90¢ at lock · limited upside before fees";
  return "";
 }
 public static String betFilter(String side,double yesMid) {
  if(!isSide(side)||!Double.isFinite(yesMid)||yesMid<0||yesMid>1)return "";
  double px="NO".equals(side)?1-yesMid:yesMid;
  if(px<.55)return "EXPERIMENTAL FILTER · sub-55¢ at lock · excluded from trial";
  return "EXPERIMENTAL FILTER · included in ≥55¢ trial · edge unvalidated";
 }
 public static String liveOdds(String side,double yesMid) {
  if(!isSide(side)||!Double.isFinite(yesMid)||yesMid<0||yesMid>1)return "Historical accuracy is not P(this window).";
  double px="NO".equals(side)?1-yesMid:yesMid;
  return "Current lock-side midpoint "+Math.round(100*px)+"¢ · historical accuracy is separate.";
 }
 public static String performanceHealth(String outcomes){
  if(outcomes==null||outcomes.length()<40)return "PERFORMANCE WATCH · need 40 graded calls";
  int n=outcomes.length(),priorN=Math.min(50,n-20),rw=0,pw=0;
  for(int i=n-20;i<n;i++)if(outcomes.charAt(i)=='W')rw++;
  for(int i=n-20-priorN;i<n-20;i++)if(outcomes.charAt(i)=='W')pw++;
  double recent=rw/20.0,prior=pw/(double)priorN;
  return (prior-recent>=.15-1e-9?"PERFORMANCE DROP":"PERFORMANCE WATCH")+" · last 20 "+Math.round(100*recent)+"% vs prior "+priorN+" "+Math.round(100*prior)+"% · descriptive, cause unknown";
 }
 public static String gradedStreak(String outcomes){
  if(outcomes==null||outcomes.isEmpty())return "No graded first calls";
  char last=outcomes.charAt(outcomes.length()-1);int count=0;
  for(int i=outcomes.length()-1;i>=0&&outcomes.charAt(i)==last;i--)count++;
  return count+(last=='W'?" wins":" losses")+" · "+outcomes.substring(Math.max(0,outcomes.length()-12)).replace("", " ").trim();
 }
 public static long tickerOrder(String ticker){
  try{return java.time.LocalDateTime.parse(ticker.split("-")[1],new java.time.format.DateTimeFormatterBuilder().parseCaseInsensitive().appendPattern("yyMMMddHHmm").toFormatter(java.util.Locale.US)).toInstant(java.time.ZoneOffset.UTC).toEpochMilli();}
  catch(Exception e){return 0;}
 }
 public static boolean isSide(String side){return "YES".equals(side)||"NO".equals(side);}
 public static String lockLabel(String track,String side,double yesMid){
  if(!isSide(side)||!Double.isFinite(yesMid))return "\n"+track+" "+side;
  double sideMid="NO".equals(side)?1-yesMid:yesMid;
  return String.format(java.util.Locale.US,"\nLOCKED %s %s · %s midpoint %.0f¢ at observation (not a fill)",track,side,side,100*sideMid);
 }
 public static boolean quote(double bid,double ask) {return Double.isFinite(bid)&&Double.isFinite(ask)&&bid>=0&&ask<=1&&bid<=ask;}
 public static boolean fresh(long now,long received,long close) {return received>0&&now>=received&&now-received<=10000&&close>now;}
 public static String direction(double spot,double strike,double bid,double ask) {
  if(!Double.isFinite(spot)||!Double.isFinite(strike)||spot<=0||strike<=0||!quote(bid,ask))return "Waiting for complete data";
  double mid=(bid+ask)/2;
  if(spot>strike&&mid>=.52)return "Above target · market leans YES";
  if(spot<strike&&mid<=.48)return "Below target · market leans NO";
  return "Signals disagree or sit near even";
 }
 /** Same lock as desktop decide_open_call. Does not rewrite a prior lock. */
 public static String decideOpenCall(double deltaUsd,double yesMid) {
  if(!Double.isFinite(deltaUsd)||!Double.isFinite(yesMid))return "COINFLIP";
  if(deltaUsd<0&&yesMid<=.48)return "NO";
  if(deltaUsd>0&&yesMid>=.52)return "YES";
  return "COINFLIP";
 }
 /** Classification of the first phone observation, independent of the YES/NO rule. */
 public static String firstTrack(long openMs,long observedMs) {
  if(openMs<=0||observedMs<openMs)return "UNKNOWN";
  return observedMs-openMs<=60000?"FIRST":"LATE-FIRST";
 }
 public static long secondsAfterOpen(long openMs,long observedMs) {
  if(openMs<=0||observedMs<openMs)return -1;
  return (observedMs-openMs+999)/1000;
 }
 public static String savedFirstTrack(String saved,String timingNote) {
  if("FIRST".equals(saved)||"LATE-FIRST".equals(saved))return saved;
  if(timingNote!=null&&timingNote.startsWith("Seen in first minute"))return "FIRST";
  if(timingNote!=null&&timingNote.startsWith("First seen "))return "LATE-FIRST";
  return "UNKNOWN";
 }
 public static String scoreboardLine(String label,int n,int wins,double askSum) {
  if(n<=0)return label+" · n=0 · waiting for graded quotes";
  double win=wins/(double)n,avgAsk=askSum/n;
  return String.format(java.util.Locale.US,
   "%s · n=%d · win %.1f%% · avg paper ask %.1f¢ · win−ask %+.1f pts",
   label,n,100*win,100*avgAsk,100*(win-avgAsk));
 }
 public static String observedHeadline(String openCall,String lateCall,String lean,String firstTrack) {
  String now=lean==null||lean.isEmpty()?"—":lean;
  String side=betLock(openCall,lateCall);
  String first="FIRST".equals(firstTrack)?"FIRST OBSERVATION":"LATE-FIRST".equals(firstTrack)?"LATE-FIRST":"FIRST TIMING UNKNOWN";
  String title=isSide(openCall)?first+" LOCK · "+openCall:isSide(lateCall)?"LATE AFTER COINFLIP · "+lateCall:first+" · COINFLIP";
  return title+"\nMarket price leans "+now+(isSide(side)&&isSide(now)&&!side.equals(now)?" · against locked call":"")+"\nPrice lean is not a new call or a switch instruction.";
 }
 /** Descriptive FIRST lock-time strength; never changes the call or implies edge. */
 public static String firstTier(String openCall,double deltaUsd,double yesMid,long secondsLeft) {
  if("COINFLIP".equals(openCall))return "EDGE-COINFLIP";
  if(!isSide(openCall)||!Double.isFinite(deltaUsd)||!Double.isFinite(yesMid)||secondsLeft<=0||yesMid<0||yesMid>1)return "";
  double distance=Math.abs(deltaUsd)/Math.sqrt(secondsLeft),margin=Math.abs(yesMid-.5);
  if(distance<.8||margin<.05)return "EDGE-COINFLIP";
  if(distance>=2.5&&margin>=.10)return "STRONG";
  return "LEAN";
 }
 public static String nowLean(double yesMid) {
  if(!Double.isFinite(yesMid))return "COINFLIP";
  if(yesMid>=.52)return "YES";
  if(yesMid<=.48)return "NO";
  return "COINFLIP";
 }
 /** Late lock window: after first minute, before last minute of a 15m round. */
 public static boolean lateLockWindow(long secondsLeft) {return secondsLeft>60&&secondsLeft<=840;}
 /** Coinbase vs strike at rollover. Not the Kalshi index. */
 public static String settleSide(double spot,double strike) {
  if(!Double.isFinite(spot)||!Double.isFinite(strike))return "";
  return spot>strike?"YES":"NO";
 }
 public static String forecastHeadline(String openCall,String lateCall,String lean) {
  String open=openCall==null||openCall.isEmpty()?"WAITING":openCall;
  String now=lean==null||lean.isEmpty()?"—":lean;
  if("COINFLIP".equals(open)&&("YES".equals(lateCall)||"NO".equals(lateCall)))
   return "LATE "+lateCall+" · NOW "+now;
  if("YES".equals(open)||"NO".equals(open))return "OPEN "+open+" · NOW "+now;
  return "OPEN COINFLIP · WAITING FOR DIRECTION";
 }
 /** Combined directional lock; no entry recommendation or confidence implied. */
 public static String betLock(String openCall,String lateCall) {
  if(isSide(openCall))return openCall;
  if(isSide(lateCall))return lateCall;
  return "";
 }
 public static String betHeadline(String openCall,String lateCall,String lean) {
  String now=lean==null||lean.isEmpty()?"—":lean;
  String side=betLock(openCall,lateCall);
  String title=isSide(openCall)?"FIRST OBSERVATION LOCK · "+openCall:isSide(lateCall)?"LATE LOCK · "+lateCall:"FIRST OBSERVATION · COINFLIP";
  return title+"\nMarket price leans "+now+(isSide(side)&&isSide(now)&&!side.equals(now)?" · against locked call":"")+"\nPrice lean is not a new call or a switch instruction.";
 }
 public static boolean reuseMarket(long now,long close,boolean hasTicker){return hasTicker&&close>now;}
 public static String observationTiming(long secondsLeft){
  long elapsed=Math.max(0,900-secondsLeft);
  return elapsed<=60?"Seen in first minute":"First seen "+(elapsed/60)+"m "+(elapsed%60)+"s after open";
 }
 public static double net(double qty,double entry,double bid,double entryFees,double exitFees) {
  if(!Double.isFinite(qty)||!Double.isFinite(entry)||!Double.isFinite(bid)||!Double.isFinite(entryFees)||!Double.isFinite(exitFees)||qty<=0||entry<0||entry>1||bid<0||bid>1||entryFees<0||exitFees<0)throw new IllegalArgumentException("Use positive quantity, prices 0–100 cents, and nonnegative fees.");
  return qty*(bid-entry)-entryFees-exitFees;
 }
 public static double ema(double[] closes,int period) {
  if(closes==null||period<1||closes.length<period)return Double.NaN;
  double value=0;
  for(int i=0;i<period;i++)value+=closes[i];
  value/=period;
  double k=2.0/(period+1);
  for(int i=period;i<closes.length;i++)value=closes[i]*k+value*(1-k);
  return value;
 }
 public static int tickStreak(double[] closes) {
  if(closes==null||closes.length<2)return 0;
  int streak=0;
  for(int i=closes.length-1;i>0;i--){
   double d=closes[i]-closes[i-1];
   if(d==0)break;
   int step=d>0?1:-1;
   if(streak==0||(step>0&&streak>0)||(step<0&&streak<0))streak+=step; else break;
  }
  return streak;
 }
 public static double composite(double spot,double strike,int streak,double imbalance) {
  if(!Double.isFinite(spot)||!Double.isFinite(strike))return Double.NaN;
  double dist=Math.tanh((spot-strike)/15.0);
  double mom=Math.max(-1,Math.min(1,streak/7.0));
  double ob=Double.isFinite(imbalance)?Math.max(-1,Math.min(1,imbalance)):0;
  return (0.45*dist+0.35*mom+0.20*ob)*100.0;
 }
 /** Distance to strike in SDs of the time remaining. $23 at 8 min != $23 at 40 sec. */
 public static double moveZ(double deltaUsd,double spot,double sigmaAnnual,double secondsLeft) {
  if(!Double.isFinite(deltaUsd)||!Double.isFinite(spot)||!Double.isFinite(sigmaAnnual))return Double.NaN;
  if(spot<=0||sigmaAnnual<=0||secondsLeft<=0)return Double.NaN;
  double sd=spot*sigmaAnnual*Math.sqrt((secondsLeft/60.0)/525600.0);
  return sd>0?deltaUsd/sd:Double.NaN;
 }
 /** Phone-observed entry cautions. Different sampling from the desktop study. */
 public static String scalpVeto(int crossings,double yesVelPerMin,double bid,double ask) {
  if(!quote(bid,ask))return "";
  double cheap=Math.min(ask,1.0-bid);
  java.util.ArrayList<String> reasons=new java.util.ArrayList<>();
  if(cheap<.30)reasons.add("cheap "+(ask<=1.0-bid?"YES":"NO")+" side under 30c");
  if(crossings==0)reasons.add("no crossing yet");
  if(Double.isFinite(yesVelPerMin)&&Math.abs(yesVelPerMin)<.05)reasons.add("slow drift");
  return reasons.isEmpty()?"":"SCALP CHECK · "+String.join(" · ",reasons)+" · entry caution, not a forecast reversal";
 }
 public static String ghost(double spot,double strike,double yesCents,long secondsLeft,int streak) {
  if(!Double.isFinite(spot)||!Double.isFinite(strike)||spot<=0||strike<=0)return "Ghost waiting";
  double vol=Math.max(1.0,Math.sqrt(Math.max(1,secondsLeft)/900.0)*15.0);
  double fair=100.0/(1.0+Math.exp(-(spot-strike)/vol));
  double edge=Double.isFinite(yesCents)?fair-yesCents:Double.NaN;
  if(Double.isFinite(edge)&&edge>12&&spot>strike)return "LEAN YES · model "+Math.round(fair)+"¢ · not a buy";
  if(streak<=-6)return "BOUNCE WATCH · "+Math.abs(streak)+" down ticks · not a buy";
  if(Double.isFinite(edge)&&edge<-12&&spot<strike)return "LEAN NO · model "+Math.round(fair)+"¢ · not a buy";
  return "WAIT · model "+Math.round(fair)+"¢ · uncalibrated";
 }
}
