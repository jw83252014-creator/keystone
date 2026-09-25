package com.agentbridge.kalshihud;

import android.app.*;
import android.os.*;
import android.content.*;
import android.graphics.*;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.net.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.*;

/** Native Android UI and direct HTTPS collection; no localhost, WebView or ThinkPad. */
public class MainActivity extends Activity {
 static final int BG=Color.rgb(12,20,23), FG=Color.rgb(238,246,245), MUTED=Color.rgb(166,185,190), GREEN=Color.rgb(117,230,181), RED=Color.rgb(255,156,155);
 static final String API="https://api.elections.kalshi.com/trade-api/v2";
 final Handler ui=new Handler(Looper.getMainLooper());
 final ExecutorService fetch=Executors.newFixedThreadPool(2);
 final ArrayList<Sample> history=new ArrayList<>();
 ScheduledExecutorService poller;
 final ScheduledExecutorService maintenance=Executors.newSingleThreadScheduledExecutor();
 volatile String gradeStatus="Checking saved phone calls…"; volatile int phonePending=0;
 volatile long scoreSuccess=0; int gradeCursor=0;
 volatile Sample sample;
 volatile String failure="Connecting directly from this phone…";
 volatile boolean active=false; volatile int generation=0;
 final ExecutorService historyFetch=Executors.newSingleThreadExecutor();
 TextView headline,price,quote,clock,detail,research,position,source,record,call,epoch;
 Chart chart; boolean contract=false;
 // Best-effort read of the ThinkPad HUD for the paper win rate and the cross-asset
 // watch. The app stays standalone: if the HUD is unreachable this stays blank.
 volatile String scoreLine="", crossLine="", verdict="", lockedCall="", lockedLean=""; volatile double lockedYes=Double.NaN; volatile boolean verdictSkip=true; volatile long scoreAt=0, verdictAt=0; volatile String verdictTicker="";
 // Phone-local open + late lock. Same rule as desktop. Works with USB unplugged.
 volatile String localTicker="", localOpen="", localLate="", localLean="", localT0="", localTier="", localTrack="UNKNOWN"; volatile long localSeenSeconds=-1; volatile double localOpenYes=Double.NaN, localLateYes=Double.NaN;
 volatile int phoneOpenW=0,phoneOpenL=0,phoneLateW=0,phoneLateL=0,phoneBetW=0,phoneBetL=0;
 volatile String phoneFirstStreak="No graded first calls", phoneFirstOutcomes="", phoneScoreRows=""; volatile double phoneFirstBaseline=.63;
 static final String HUD="http://100.119.33.21:8765/api/state";
 final Runnable tick=new Runnable(){public void run(){if(active){render();ui.postDelayed(this,1000);}}};
 static class Sample {
  String ticker; double spot,strike,bid,ask,noBid,noAsk;long open,close,received,fetchMs;
  String openCall="",openTier="",openTrack="UNKNOWN",nowLean="",verdictText="";long firstSeenSeconds=-1;double openYes=Double.NaN;
  JSONObject json() throws JSONException {JSONObject j=new JSONObject();j.put("ticker",ticker);j.put("received_at_ms",received);j.put("close_at_ms",close);j.put("fetch_ms",fetchMs);j.put("spot_coinbase",spot);j.put("strike",strike);j.put("yes_bid",bid);j.put("yes_ask",ask);j.put("no_bid",noBid);j.put("no_ask",noAsk);j.put("mode","paper");j.put("direction",Rules.direction(spot,strike,bid,ask));j.put("model_status","unvalidated_observation_only");
   j.put("locked_open_call",openCall);j.put("locked_open_yes",Double.isNaN(openYes)?JSONObject.NULL:openYes);j.put("locked_first_tier",openTier);j.put("locked_first_track",openTrack);j.put("first_seen_seconds",firstSeenSeconds<0?JSONObject.NULL:firstSeenSeconds);j.put("locked_now_lean",nowLean);
   j.put("verdict",verdictText);return j;}
 }
 int dp(float n){return (int)(n*getResources().getDisplayMetrics().density+.5f);}
 TextView text(LinearLayout parent,String s,int size,int color){TextView t=new TextView(this);t.setText(s);t.setTextSize(size);t.setTextColor(color);t.setPadding(0,dp(7),0,dp(7));parent.addView(t);return t;}
 Button button(LinearLayout parent,String label,Runnable action){Button b=new Button(this);b.setText(label);b.setTextColor(FG);b.setAllCaps(false);android.graphics.drawable.GradientDrawable shape=new android.graphics.drawable.GradientDrawable();shape.setColor(Color.rgb(22,34,39));shape.setCornerRadius(dp(14));shape.setStroke(dp(1),Color.rgb(60,84,89));b.setBackground(shape);LinearLayout.LayoutParams bp=new LinearLayout.LayoutParams(-1,dp(48));bp.setMargins(0,dp(4),0,dp(6));parent.addView(b,bp);b.setOnClickListener(v->action.run());return b;}
 @Override public void onCreate(Bundle b){super.onCreate(b);getWindow().setStatusBarColor(BG);getWindow().setNavigationBarColor(BG);
  ScrollView scroll=new ScrollView(this);scroll.setBackgroundColor(BG);LinearLayout body=new LinearLayout(this);body.setOrientation(1);body.setPadding(dp(22),dp(20),dp(22),dp(24));scroll.addView(body);setContentView(scroll);
  if(Build.VERSION.SDK_INT>=30)scroll.setOnApplyWindowInsetsListener((v,insets)->{android.graphics.Insets i=insets.getInsets(WindowInsets.Type.systemBars());v.setPadding(i.left,i.top,i.right,i.bottom);return insets;});
  loadRecord();
  maintenance.execute(()->exportAudit(true));
  maintenance.scheduleWithFixedDelay(this::gradeSavedCalls,0,30,TimeUnit.SECONDS);
  text(body,"BTC / 15m",25,FG);clock=text(body,"PAPER · Phone powered",14,MUTED);call=text(body,"",20,FG);headline=text(body,"Connecting…",27,GREEN);price=text(body,"—",35,FG);quote=text(body,"Waiting for executable quotes",16,MUTED);
  epoch=text(body,"",14,MUTED);
  chart=new Chart();body.addView(chart,new LinearLayout.LayoutParams(-1,dp(235)));
  button(body,"Bitcoin / contract chart",()->{contract=!contract;chart.invalidate();});
  detail=text(body,"",15,FG);record=text(body,"",15,FG);research=text(body,"Research mix waiting on samples",14,MUTED);source=text(body,"",12,MUTED);
  button(body,"Why? / glossary",this::glossary);
  position=text(body,"No paper position recorded",17,GREEN);
  button(body,"Paper position / exit calculator",this::calculator);
  button(body,"Replay saved observations",this::replay);
  text(body,"Native Android · direct public feeds\nMonitoring runs while this screen is open. No orders or account keys. Coinbase is a spot reference, not the settlement index.",12,MUTED);
 }
 @Override public void onResume(){super.onResume();active=true;generation++;poller=Executors.newSingleThreadScheduledExecutor();poller.scheduleWithFixedDelay(this::collect,0,2,TimeUnit.SECONDS);ui.post(tick);}
 @Override public void onPause(){active=false;generation++;ui.removeCallbacks(tick);if(poller!=null)poller.shutdownNow();super.onPause();}
 @Override public void onDestroy(){maintenance.shutdownNow();historyFetch.shutdownNow();fetch.shutdownNow();super.onDestroy();}
 JSONObject get(String url)throws Exception{return get(url,5000);}
 JSONObject get(String url,int timeoutMs)throws Exception{
  HttpURLConnection c=(HttpURLConnection)new URL(url).openConnection();c.setConnectTimeout(timeoutMs);c.setReadTimeout(timeoutMs);c.setRequestProperty("User-Agent","AgentBridge-Moto-Paper/0.1");
  try {int status=c.getResponseCode();if(status!=200)throw new IOException("Feed HTTP "+status);try(InputStream in=c.getInputStream();ByteArrayOutputStream out=new ByteArrayOutputStream()){byte[] buf=new byte[8192];int n;while((n=in.read(buf))!=-1){out.write(buf,0,n);if(out.size()>2000000)throw new IOException("Feed response too large");}return new JSONObject(out.toString("UTF-8"));}}finally{c.disconnect();}
 }
 // No custom networking or credential bridge is used.
 double number(JSONObject j,String key)throws JSONException{double v=j.getDouble(key);if(!Double.isFinite(v))throw new JSONException("Invalid "+key);return v;}
 double quoteValue(JSONObject j,String key)throws JSONException {String dollars=key+"_dollars";return j.has(dollars)?number(j,dollars):number(j,key)/100;}
 void collect(){int run=generation;long started=System.currentTimeMillis();Future<JSONObject> a=null,b=null;try{
   Sample prior=sample;
   boolean reuse=prior!=null&&Rules.reuseMarket(started,prior.close,prior.ticker!=null);
   final String marketUrl=reuse?API+"/markets/"+prior.ticker:API+"/markets?series_ticker=KXBTC15M&status=open&limit=20";
   a=fetch.submit(()->get(marketUrl));
   b=fetch.submit(()->get("https://api.coinbase.com/v2/prices/BTC-USD/spot"));
   JSONObject markets=a.get(12,TimeUnit.SECONDS),btc=b.get(12,TimeUnit.SECONDS);long now=System.currentTimeMillis();JSONObject m=null;
   if(reuse)m=markets.getJSONObject("market");
   else{
    JSONArray list=markets.getJSONArray("markets");long earliest=Long.MAX_VALUE;
    for(int i=0;i<list.length();i++){JSONObject x=list.getJSONObject(i);long close=Instant.parse(x.getString("close_time")).toEpochMilli(),open=Instant.parse(x.getString("open_time")).toEpochMilli();if(open<=now&&close>now&&close<earliest){m=x;earliest=close;}}
    if(m==null)throw new IOException("Waiting for next active BTC round");
    m=get(API+"/markets/"+m.getString("ticker")).getJSONObject("market");
   }
   Sample s=new Sample();s.ticker=m.getString("ticker");s.open=Instant.parse(m.getString("open_time")).toEpochMilli();s.close=Instant.parse(m.getString("close_time")).toEpochMilli();s.strike=number(m,"floor_strike");s.spot=number(btc.getJSONObject("data"),"amount");s.bid=quoteValue(m,"yes_bid");s.ask=quoteValue(m,"yes_ask");s.noBid=quoteValue(m,"no_bid");s.noAsk=quoteValue(m,"no_ask");s.received=started;s.fetchMs=System.currentTimeMillis()-started;
   if(!Rules.quote(s.bid,s.ask)||!Rules.quote(s.noBid,s.noAsk)||s.spot<=0||s.strike<=0||!Rules.fresh(System.currentTimeMillis(),s.received,s.close))throw new IOException("Waiting for a complete fresh quote");
   if(!active||run!=generation)return;
   synchronized(history){
    if(sample==null||!sample.ticker.equals(s.ticker))history.clear();
    history.add(s);while(history.size()>500)history.remove(0);}
   sample=s;failure="";
   lockNative(s);
   s.openCall=localOpen;s.openYes=localOpenYes;s.openTier=localTier;s.openTrack=localTrack;s.firstSeenSeconds=localSeenSeconds;s.nowLean=localLean;s.verdictText=Rules.forecastHeadline(localOpen,localLate,localLean);
   append(s);ui.post(this::render);
   if(System.currentTimeMillis()-scoreAt>=8000){scoreAt=System.currentTimeMillis();historyFetch.submit(this::pullScore);}
  }catch(Exception e){if(active&&run==generation){failure="Feed unavailable · "+(e.getCause()!=null?e.getCause().getClass().getSimpleName():e.getClass().getSimpleName());ui.post(this::render);}}finally{if(a!=null)a.cancel(true);if(b!=null)b.cancel(true);}}
 SharedPreferences locks(){return getSharedPreferences("phone_locks",MODE_PRIVATE);}
 void loadLock(String ticker){
  String raw=locks().getString(ticker,"");
  localTicker=ticker;localOpen="";localLate="";localT0="";localTier="";localTrack="UNKNOWN";localSeenSeconds=-1;localOpenYes=Double.NaN;localLateYes=Double.NaN;
  SharedPreferences meta=getSharedPreferences("lock_metadata",MODE_PRIVATE);
  localT0=meta.getString(ticker+".t0","");
  localTier=meta.getString(ticker+".tier","");
  localTrack=Rules.savedFirstTrack(meta.getString(ticker+".track",""),meta.getString(ticker,""));
  localSeenSeconds=meta.getLong(ticker+".first_seconds",-1);
  if(raw.isEmpty())return;
  String[] p=raw.split("\\|",-1);
  if(p.length<4)return;
  localOpen=p[0];localLate=p[1];
  try{localOpenYes=p[2].isEmpty()?Double.NaN:Double.parseDouble(p[2]);}catch(Exception e){localOpenYes=Double.NaN;}
  try{localLateYes=p[3].isEmpty()?Double.NaN:Double.parseDouble(p[3]);}catch(Exception e){localLateYes=Double.NaN;}
 }
 void saveLock(){
  if(localTicker==null||localTicker.isEmpty())return;
  String o=Double.isNaN(localOpenYes)?"":String.valueOf(localOpenYes);
  String l=Double.isNaN(localLateYes)?"":String.valueOf(localLateYes);
  locks().edit().putString(localTicker,localOpen+"|"+localLate+"|"+o+"|"+l).apply();
 }
 SharedPreferences recordStore(){return getSharedPreferences("phone_record",MODE_PRIVATE);}
 void loadRecord(){
  int ow=0,ol=0,lw=0,ll=0,bw=0,bl=0,pending=0;
  SharedPreferences grades=getSharedPreferences("official_results",MODE_PRIVATE);
  SharedPreferences meta=getSharedPreferences("lock_metadata",MODE_PRIVATE);
  Map<String,?> allMeta=meta.getAll();
  int[] rowN=new int[3],rowWins=new int[3],missingAsk=new int[3];double[] rowAsk=new double[3];
  for(Map.Entry<String,?> e:locks().getAll().entrySet()){
   String[] calls=String.valueOf(e.getValue()).split("\\|",-1);
   String result=grades.getString(e.getKey(),"");
   if(result.isEmpty()){pending++;continue;}
   String first=calls.length>0?calls[0]:"";
   String late=calls.length>1?calls[1]:"";
   if(Rules.isSide(first)){if(first.equals(result))ow++;else ol++;}
   if(Rules.isSide(late)){if(late.equals(result))lw++;else ll++;}
   int group=-1;String scoredSide="";Object askValue=null;
   if(Rules.isSide(first)){
    String track=Rules.savedFirstTrack(meta.getString(e.getKey()+".track",""),meta.getString(e.getKey(),""));
    group="FIRST".equals(track)?0:"LATE-FIRST".equals(track)?1:-1;
    scoredSide=first;askValue=allMeta.get(e.getKey()+".ask");
   }else if("COINFLIP".equals(first)&&Rules.isSide(late)){
    group=2;scoredSide=late;askValue=allMeta.get(e.getKey()+".late_ask");
   }
   if(group>=0){
    double ask=askValue instanceof Number?((Number)askValue).doubleValue():Double.NaN;
    if(Double.isFinite(ask)&&ask>=0&&ask<=1){rowN[group]++;rowWins[group]+=scoredSide.equals(result)?1:0;rowAsk[group]+=ask;}
    else missingAsk[group]++;
   }
   String bet=Rules.betLock(first,late);
   if(Rules.isSide(bet)){if(bet.equals(result))bw++;else bl++;}
  }
  phoneOpenW=ow;phoneOpenL=ol;phoneLateW=lw;phoneLateL=ll;phoneBetW=bw;phoneBetL=bl;phonePending=pending;
  phoneFirstBaseline=rowN[0]>0?rowWins[0]/(double)rowN[0]:.63;
  phoneScoreRows=Rules.scoreboardLine("FIRST (≤60s)",rowN[0],rowWins[0],rowAsk[0])+"\n"+
   Rules.scoreboardLine("LATE-FIRST (>60s)",rowN[1],rowWins[1],rowAsk[1])+"\n"+
   Rules.scoreboardLine("LATE after COINFLIP",rowN[2],rowWins[2],rowAsk[2])+"\n"+
   "Matched settled quotes only; missing paper asks "+missingAsk[0]+"/"+missingAsk[1]+"/"+missingAsk[2]+". No fills or fees.";
  ArrayList<String> ordered=new ArrayList<>(locks().getAll().keySet());
  Collections.sort(ordered,(a,b)->Long.compare(Rules.tickerOrder(a),Rules.tickerOrder(b)));
  StringBuilder outcomes=new StringBuilder();
  for(String ticker:ordered){String first=locks().getString(ticker,"").split("\\|",-1)[0];String result=grades.getString(ticker,"");
   if(Rules.isSide(first)&&Rules.isSide(result)&&"FIRST".equals(Rules.savedFirstTrack(meta.getString(ticker+".track",""),meta.getString(ticker,""))))outcomes.append(first.equals(result)?'W':'L');}
  phoneFirstOutcomes=outcomes.toString();
  phoneFirstStreak=Rules.gradedStreak(phoneFirstOutcomes);
 }
 void gradeSavedCalls(){
  if(!active)return;
  try{
   SharedPreferences grades=getSharedPreferences("official_results",MODE_PRIVATE);
   ArrayList<String> pending=new ArrayList<>();
   for(String ticker:locks().getAll().keySet())if(!grades.contains(ticker)&&!ticker.equals(localTicker))pending.add(ticker);
   Collections.sort(pending);
   int count=Math.min(8,pending.size());
   for(int i=0;i<count&&!Thread.currentThread().isInterrupted();i++){
    String ticker=pending.get((gradeCursor+i)%pending.size());
    try{JSONObject m=get(API+"/markets/"+ticker).getJSONObject("market");
     String result=m.optString("result","").toUpperCase(Locale.US);
     if(Rules.isSide(result))grades.edit().putString(ticker,result).commit();
    }catch(Exception e){gradeStatus="Settlement retry pending";}
   }
   gradeCursor+=count;loadRecord();
   gradeStatus=phonePending+" saved rounds awaiting result / current round";
   exportAudit(false);ui.post(this::render);
  }catch(Exception e){gradeStatus="Settlement retry pending";}
 }
 void exportAudit(boolean observations){
  try{
   File dir=getExternalFilesDir(null);if(dir==null)return;
   JSONObject audit=new JSONObject();audit.put("exported_at",Instant.now().toString());
   audit.put("locks",new JSONObject(locks().getAll()));
   audit.put("lock_metadata",new JSONObject(getSharedPreferences("lock_metadata",MODE_PRIVATE).getAll()));
   audit.put("official_results",new JSONObject(getSharedPreferences("official_results",MODE_PRIVATE).getAll()));
   audit.put("legacy_coinbase_record",new JSONObject(recordStore().getAll()));
   try(FileOutputStream out=new FileOutputStream(new File(dir,"phone-audit.json"))){out.write(audit.toString(2).getBytes(StandardCharsets.UTF_8));}
   if(observations)for(String name:new String[]{"observations.jsonl","observations.previous.jsonl"}){
    File input=new File(getFilesDir(),name);if(!input.exists())continue;
    try(FileInputStream in=new FileInputStream(input);FileOutputStream out=new FileOutputStream(new File(dir,name))){byte[] buf=new byte[8192];int n;while((n=in.read(buf))!=-1)out.write(buf,0,n);}
   }
  }catch(Exception e){android.util.Log.w("KalshiHUD","Audit export failed",e);}
 }
 void lockNative(Sample s){
  if(s==null||s.ticker==null)return;
  if(!s.ticker.equals(localTicker))loadLock(s.ticker);
  double mid=(s.bid+s.ask)/2,delta=s.spot-s.strike;
  long seenAt=System.currentTimeMillis();
  long left=Math.max(0,(s.close-seenAt)/1000);
  localLean=Rules.nowLean(mid);
  boolean changed=false;
  if(localT0==null||localT0.isEmpty()){
   String t0=Rules.decideT0Call(delta);
   if("YES".equals(t0)||"NO".equals(t0)){localT0=t0;getSharedPreferences("lock_metadata",MODE_PRIVATE).edit().putString(s.ticker+".t0",t0).apply();}
  }
  if(localOpen==null||localOpen.isEmpty()){
   localOpen=Rules.decideOpenCall(delta,mid);localOpenYes=mid;localTier=Rules.firstTier(localOpen,delta,mid,left);localTrack=Rules.firstTrack(s.open,seenAt);localSeenSeconds=Rules.secondsAfterOpen(s.open,seenAt);changed=true;
   getSharedPreferences("lock_metadata",MODE_PRIVATE).edit().putString(localTicker,Rules.observationTiming(left)).putLong(localTicker+".at",seenAt).putFloat(localTicker+".ask",(float)("NO".equals(localOpen)?s.noAsk:s.ask)).putString(localTicker+".tier",localTier).putString(localTicker+".track",localTrack).putLong(localTicker+".first_seconds",localSeenSeconds).apply();
  }
  if("COINFLIP".equals(localOpen)&&(localLate==null||localLate.isEmpty())&&Rules.lateLockWindow(left)){
   String turned=Rules.decideOpenCall(delta,mid);
   if("YES".equals(turned)||"NO".equals(turned)){localLate=turned;localLateYes=mid;changed=true;
    getSharedPreferences("lock_metadata",MODE_PRIVATE).edit().putLong(localTicker+".late_at",System.currentTimeMillis()).putFloat(localTicker+".late_ask",(float)("NO".equals(turned)?s.noAsk:s.ask)).apply();}
  }
  if(changed)saveLock();
 }
 /** Pull the paper record and cross-asset watch off the ThinkPad HUD. Optional; silent on failure. */
 void pullScore(){
  try{JSONObject s=get(HUD,1200);
   JSONObject sc=s.optJSONObject("paper_score"),ep=s.optJSONObject("epoch_score"),ca=s.optJSONObject("cross_asset");
   StringBuilder b=new StringBuilder();
   // The locked open call is what the record grades. The big lean above is live
   // and is never scored — show both so they are not confused for each other.
   JSONArray bd=s.optJSONArray("boards");
   verdict="";verdictAt=0;verdictTicker="";lockedCall="";lockedLean="";lockedYes=Double.NaN;verdictSkip=true;
   if(bd!=null)for(int i=0;i<bd.length();i++){JSONObject brd=bd.optJSONObject(i);
    if(brd!=null&&"KXBTC15M".equals(brd.optString("series"))){
     JSONObject lv=brd.optJSONObject("live");
     if(s.optBoolean("stale",true)||brd.optBoolean("stale",true)||lv==null||sample==null||!sample.ticker.equals(lv.optString("ticker")))break;
     verdictTicker=lv.optString("ticker");
     verdictAt=Instant.parse(s.getString("ts")).toEpochMilli();
     JSONObject de=brd.optJSONObject("decision");
     if(de!=null){verdictSkip=!"YES".equals(de.optString("forecast_call"))&&!"NO".equals(de.optString("forecast_call"));
      StringBuilder v=new StringBuilder(de.optString("headline","WAITING FOR DIRECTION"));
      if(!de.isNull("trade_commentary"))v.append("\nScalp commentary: ").append(de.optString("trade_commentary"));
      JSONArray rs=de.optJSONArray("reasons");
      if(rs!=null)for(int k=0;k<Math.min(5,rs.length());k++)v.append("\n· ").append(rs.optString(k));
      if(!de.isNull("streak_note"))v.append("\n").append(de.optString("streak_note"));
      verdict=v.toString();}
     JSONObject mix=brd.optJSONObject("research");
     b.append("Desktop mix now: ").append(mix!=null&&mix.optBoolean("ok")?mix.optString("vote","waiting"):"waiting for fresh inputs").append("\n");
     JSONObject ww=brd.optJSONObject("window_watch");
     if(ww!=null&&!ww.isNull("open_call")){lockedCall=ww.optString("open_call");lockedYes=ww.optDouble("open_yes",Double.NaN);
      lockedLean=ww.optString("now_lean","");
      b.append(String.format(Locale.US,"Locked open call: %s · YES quote %.0f¢ · now leaning %s",
       lockedCall,100*lockedYes,lockedLean.isEmpty()?"—":lockedLean));
      // A coinflip open that later turned strong. Graded on its own track;
      // it never replaces the official call.
      if(!ww.isNull("revised_call"))b.append(String.format(Locale.US,
        "\nRevised track: turned %s · YES quote %.0f¢ (separate grade, not the open call)",
        ww.optString("revised_call"),100*ww.optDouble("revised_yes",Double.NaN)));
      if(ww.optInt("jev_lock_version")==2)b.append(String.format(Locale.US,
        "\nJev first real response (%s): %s · action score %.0f%% · uncalibrated, not win odds",
        ww.optString("jev_open_source","unknown"), ww.optString("jev_side"),
        100*ww.optDouble("jev_edge",Double.NaN)));}break;}}
   if(sc!=null&&!sc.isNull("win_pct")){if(b.length()>0)b.append("\n");
    b.append(String.format(Locale.US,"That call is %s · %.1f%% of %d",sc.optString("record","—"),sc.optDouble("win_pct"),sc.optInt("scored")));}
   if(ep!=null){JSONObject op=ep.optJSONObject("open"),mx=ep.optJSONObject("research");
    String epText="DESKTOP EPOCH · "+ep.optString("since","unknown")+"\nUnchanged base: "+(op==null?"—":op.optString("record"))+" · "+(op==null?"—":op.optString("win_pct"))+"%\nResearch mix: "+(mx==null?"—":mx.optString("record"))+" · "+(mx==null?"—":mx.optString("win_pct"))+"%";
    getSharedPreferences("desktop_score",MODE_PRIVATE).edit().putString("epoch",epText).apply();
   }
   if(ep!=null)b.append("\nScore epoch: ").append(ep.optString("since","unknown"));
   if(ep!=null){JSONObject op=ep.optJSONObject("open"),rs=ep.optJSONObject("research");
    if(op!=null&&!op.isNull("win_pct"))b.append(String.format(Locale.US,"\nOpen since epoch %s · %.0f%%%s",
      op.optString("record","—"),op.optDouble("win_pct"),op.optInt("scored")<20?" (small n — noise)":""));
    if(rs!=null&&!rs.isNull("win_pct"))b.append(String.format(Locale.US,"\nMix since epoch %s · %.0f%%",
      rs.optString("record","—"),rs.optDouble("win_pct")));}
   JSONObject rv=s.optJSONObject("revised_score"),cx=s.optJSONObject("crossasset_score");
   if(rv!=null||cx!=null)b.append(String.format(Locale.US,"\n  tracks: revised %s · cross-asset %s",
     rv==null?"0-0":rv.optString("record","0-0"), cx==null?"0-0":cx.optString("record","0-0")));
   JSONObject shadow=s.optJSONObject("shadow_score");
   if(shadow!=null){JSONObject models=shadow.optJSONObject("models");
    if(models!=null){b.append("\nFIRST-OBS MODEL TRIALS · experimental");
     for(String name:new String[]{"base","market","tiger","mix","book","agreement"}){JSONObject row=models.optJSONObject(name);if(row!=null)b.append("\n").append(name).append(" ").append(row.optString("record","0-0")).append(" · unavailable ").append(row.optInt("unavailable")).append(" · neutral ").append(row.optInt("abstained"));}
    }
   }
   scoreLine=b.toString();scoreSuccess=System.currentTimeMillis();
   getSharedPreferences("desktop_score",MODE_PRIVATE).edit().putString("text",scoreLine).putString("server_ts",s.optString("ts")).apply();
   crossLine=(ca!=null&&ca.optBoolean("ok")&&!ca.isNull("setup"))?("CROSS-ASSET · "+ca.optString("say")):"";
  }catch(Exception e){crossLine="";}}
 void append(Sample s){try{File file=new File(getFilesDir(),"observations.jsonl");if(file.length()>8000000){File previous=new File(getFilesDir(),"observations.previous.jsonl");if(previous.exists())previous.delete();file.renameTo(previous);}try(FileOutputStream out=new FileOutputStream(file,true)){out.write((s.json().toString()+"\n").getBytes(StandardCharsets.UTF_8));}}catch(Exception e){android.util.Log.w("KalshiHUD","Cannot save replay",e);}}
 boolean usable(){Sample s=sample;return active&&s!=null&&failure.isEmpty()&&Rules.fresh(System.currentTimeMillis(),s.received,s.close);}
 void render(){if(headline==null)return;Sample s=sample;boolean fresh=usable();long left=s==null?0:Math.max(0,(s.close-System.currentTimeMillis())/1000);
  clock.setText("PAPER · "+(s==null?"connecting":String.format(Locale.US,"%02d:%02d remaining",left/60,left%60)));
  headline.setText(!fresh?"Signal unavailable":left<=60?"Final minute · observe only":Rules.direction(s.spot,s.strike,s.bid,s.ask));headline.setTextColor(!fresh?MUTED:s.spot>=s.strike?GREEN:RED);
  price.setText(!fresh?"—":String.format(Locale.US,"$%,.2f",s.spot));
  quote.setText(!fresh?"No current executable quote":String.format(Locale.US,"YES sell %.0f¢ · buy %.0f¢\nNO sell %.0f¢ · buy %.0f¢",s.bid*100,s.ask*100,s.noBid*100,s.noAsk*100));
  detail.setText(!fresh?(failure.isEmpty()?"Data expired. Waiting for a fresh round and both feeds.":failure):String.format(Locale.US,"%+,.2f USD vs target $%,.2f\nEntry: no validated edge. Direction alone is not a buy signal.",s.spot-s.strike,s.strike));
  String nativeCall=Rules.observedHeadline(localOpen,localLate,localLean,localTrack);
  if(s!=null&&s.ticker!=null&&s.ticker.equals(localTicker)&&localOpen!=null&&!localOpen.isEmpty()){
   nativeCall+=Rules.lockLabel("LATE-FIRST".equals(localTrack)?"LATE-FIRST":"FIRST".equals(localTrack)?"FIRST OBSERVED":"TIMING UNKNOWN",localOpen,localOpenYes);
   if(localSeenSeconds>=0)nativeCall+="\nFirst data seen "+localSeenSeconds+"s after market open";
   if(localTier!=null&&!localTier.isEmpty())nativeCall+="\n"+("FIRST".equals(localTrack)?"FIRST":"LATE-FIRST".equals(localTrack)?"LATE-FIRST":"First-seen")+" strength "+localTier+" · descriptive, not win odds";
   if(localSeenSeconds<0)nativeCall+="\n"+getSharedPreferences("lock_metadata",MODE_PRIVATE).getString(localTicker,"First-observation timing not recorded in older version");
   if(localLate!=null&&!localLate.isEmpty())nativeCall+=Rules.lockLabel("LATE",localLate,localLateYes);
   double midNow=(s.bid+s.ask)/2,deltaNow=s.spot-s.strike;
   String bet=Rules.betLock(localOpen,localLate);
   nativeCall+="\n"+Rules.firstObsStrength(bet, Rules.isSide(localOpen)?localOpenYes:localLateYes);
   double lockPx=Rules.isSide(localOpen)?("NO".equals(localOpen)?1-localOpenYes:localOpenYes):("NO".equals(localLate)?1-localLateYes:localLateYes);
   nativeCall+="\n"+Rules.betFilter(bet, Rules.isSide(localOpen)?localOpenYes:localLateYes);
   String rich=Rules.skipRich(lockPx); if(!rich.isEmpty()) nativeCall+="\n"+rich;
   nativeCall+="\n"+Rules.liveOdds(bet, midNow);
   if(localT0!=null&&!localT0.isEmpty())nativeCall+="\nT+0 spot-vs-strike "+localT0+" · paper track, not FIRST";
   String watch=Rules.flipWatch(bet,midNow,deltaNow,left,0);
   if(!watch.isEmpty())nativeCall+="\n"+watch;
  }
  if(call!=null){call.setText(!fresh?"Waiting on Kalshi + Coinbase":nativeCall);call.setVisibility(android.view.View.VISIBLE);
   boolean waiting=!Rules.isSide(Rules.betLock(localOpen,localLate));
   call.setTextColor(!fresh||waiting?Color.rgb(255,107,107):Color.rgb(255,193,74));}
  if(epoch!=null){boolean ef=scoreSuccess>0&&System.currentTimeMillis()-scoreSuccess>=0&&System.currentTimeMillis()-scoreSuccess<20000;
   epoch.setText(getSharedPreferences("desktop_score",MODE_PRIVATE).getString("epoch","DESKTOP EPOCH · waiting for ThinkPad history")+"\n"+(ef?"Connected · separate from phone book":"Offline / cached · needs ThinkPad connection"));}
  if(record!=null){
   int os=phoneOpenW+phoneOpenL, ls=phoneLateW+phoneLateL, bs=phoneBetW+phoneBetL;
   String r=String.format(Locale.US,"COMBINED LOCKS %d-%d%s\nALL FIRST-SEEN LOCKS (mixed timing) %d-%d%s\nLATE AFTER COINFLIP %d-%d%s\nSettlement accuracy, not trade profit. First-seen and late use the same agreement rule.",
     phoneBetW,phoneBetL,bs>0?String.format(Locale.US," · %.0f%%",100.0*phoneBetW/bs):"",
     phoneOpenW,phoneOpenL,os>0?String.format(Locale.US," · %.0f%%",100.0*phoneOpenW/os):"",
     phoneLateW,phoneLateL,ls>0?String.format(Locale.US," · %.0f%%",100.0*phoneLateW/ls):"");
   r+="\n\nPHONE TIMING SCOREBOARD\n"+phoneScoreRows;
   r+="\n"+Rules.performanceHealth(phoneFirstOutcomes)+"\nTRUE FIRST graded streak: "+phoneFirstStreak+"\n"+Rules.streakHealth(phoneFirstOutcomes,phoneFirstBaseline);
  r+="\nOldest → newest · pending/coinflip omitted. A loss streak does not make a win due.";
   r+="\n"+gradeStatus;
   String desktop=scoreLine.isEmpty()?getSharedPreferences("desktop_score",MODE_PRIVATE).getString("text",""):scoreLine;
   boolean desktopFresh=scoreSuccess>0&&System.currentTimeMillis()-scoreSuccess>=0&&System.currentTimeMillis()-scoreSuccess<20000;
   r+="\nThinkPad history · "+(desktopFresh?"connected":"OFFLINE / cached; not live");
   if(!desktop.isEmpty())r+="\n"+desktop;
   r+="\nHistory timestamp: "+getSharedPreferences("desktop_score",MODE_PRIVATE).getString("server_ts","unavailable");
   if(desktopFresh&&!crossLine.isEmpty())r+="\n"+crossLine;
   record.setText(r);record.setVisibility(r.isEmpty()?android.view.View.GONE:android.view.View.VISIBLE);
   record.setTextColor(crossLine.isEmpty()?FG:Color.rgb(255,193,74));}
  if(research!=null){
   double[] closes;synchronized(history){closes=new double[history.size()];for(int i=0;i<history.size();i++)closes[i]=history.get(i).spot;}
   if(s==null||closes.length<3)research.setText("Research mix waiting on samples · not a buy");
   else{
    int streak=Rules.tickStreak(closes);double ema9=Rules.ema(closes,9);double ema21=Rules.ema(closes,21);
    double yes=(s.bid+s.ask)/2*100;
    String ghost=Rules.ghost(s.spot,s.strike,yes,left,streak);
    double comp=Rules.composite(s.spot,s.strike,streak,0);
    int crossings=0;double vel=Double.NaN,sigma=Double.NaN;
    synchronized(history){
     for(int i=1;i<history.size();i++){
      double a=history.get(i-1).spot-history.get(i-1).strike,b=history.get(i).spot-history.get(i).strike;
      if(a!=0&&b!=0&&(a>0)!=(b>0))crossings++;
     }
     Sample first=history.get(0),lastS=history.get(history.size()-1);
     double mins=(lastS.received-first.received)/60000.0;
     for(int i=history.size()-1;i>=0;i--){
      double dm=(lastS.received-history.get(i).received)/60000.0;
      if(dm>=1.0){vel=((lastS.bid+lastS.ask)/2-(history.get(i).bid+history.get(i).ask)/2)/dm;break;}
     }
     if(Double.isNaN(vel)&&mins>.05)vel=((lastS.bid+lastS.ask)/2-(first.bid+first.ask)/2)/mins;
     if(history.size()>=10&&mins>0){
      double sum=0,sq=0;int n=0;
      for(int i=1;i<history.size();i++){
       double p0=history.get(i-1).spot,p1=history.get(i).spot;
       if(p0>0&&p1>0){double r=Math.log(p1/p0);sum+=r;sq+=r*r;n++;}
      }
      if(n>2){double mean=sum/n,var=sq/n-mean*mean;
       if(var>0)sigma=Math.sqrt(var)*Math.sqrt(n/(mins/60.0/24.0/365.0));}
     }
    }
    double z=Rules.moveZ(s.spot-s.strike,s.spot,sigma,left);
    String veto=Rules.scalpVeto(crossings,vel,s.bid,s.ask);
    research.setText(String.format(Locale.US,"MIX · composite %+.1f · ticks %+d\nEMA %s\nz %s · crossings %d · vel %s\n%s%s\n2s phone samples, not 1m candles. Uncalibrated. Not a buy.",
      comp,streak,Double.isNaN(ema9)||Double.isNaN(ema21)?"need more samples":(ema9>=ema21?"9≥21":"9<21"),
      Double.isNaN(z)?"—":String.format(Locale.US,"%+.2f",z),crossings,
      Double.isNaN(vel)?"—":String.format(Locale.US,"%+.0f¢/min",vel*100),
      veto.isEmpty()?"":veto+"\n",ghost));
   }
  }
  source.setText(s==null?"Direct from this Moto · Kalshi + Coinbase":s.ticker+"\nReceipt age "+Math.max(0,(System.currentTimeMillis()-s.received)/1000)+"s · last request "+s.fetchMs+"ms · not exchange latency");
  updatePosition();chart.invalidate();
 }
 void glossary(){new AlertDialog.Builder(this).setTitle("Finance + plain English").setMessage(Glossary.TEXT).setPositiveButton("Got it",null).show();}
 void calculator(){LinearLayout form=new LinearLayout(this);form.setOrientation(1);form.setPadding(dp(18),0,dp(18),0);android.content.SharedPreferences prefs=getPreferences(0);String[] keys={"qty","entry","entryFees","exitFees"},labels={"Contracts","Entry price (cents)","Total entry fees ($)","Estimated total exit fees ($)"};EditText[] fields=new EditText[4];Spinner side=new Spinner(this);side.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,new String[]{"YES","NO"}));side.setSelection(prefs.getBoolean("no",false)?1:0);form.addView(side);
  for(int i=0;i<4;i++){text(form,labels[i],14,FG);fields[i]=new EditText(this);fields[i].setTextColor(FG);fields[i].setInputType(8194);fields[i].setText(prefs.getString(keys[i],i==0?"1":i==1?"50":"0"));form.addView(fields[i]);}
  AlertDialog d=new AlertDialog.Builder(this).setTitle("Hypothetical paper exit").setView(form).setPositiveButton("Save",null).setNeutralButton("Clear",(x,y)->{prefs.edit().clear().apply();updatePosition();}).setNegativeButton("Cancel",null).create();
  d.setOnShowListener(x->d.getButton(-1).setOnClickListener(v->{try{double q=Double.parseDouble(fields[0].getText().toString()),entry=Double.parseDouble(fields[1].getText().toString())/100,ef=Double.parseDouble(fields[2].getText().toString()),xf=Double.parseDouble(fields[3].getText().toString());Rules.net(q,entry,.5,ef,xf);android.content.SharedPreferences.Editor ed=prefs.edit();for(int i=0;i<4;i++)ed.putString(keys[i],fields[i].getText().toString());ed.putBoolean("no",side.getSelectedItemPosition()==1).putBoolean("has",true).putString("ticker",sample==null?"":sample.ticker).apply();updatePosition();d.dismiss();}catch(Exception e){Toast.makeText(this,"Check quantity, 0–100¢ price, and nonnegative fees",1).show();}}));d.show();
 }
 void updatePosition(){if(position==null)return;android.content.SharedPreferences p=getPreferences(0);if(!p.getBoolean("has",false)){position.setText("Paper exit · no position recorded");return;}if(!usable()||!p.getString("ticker","").equals(sample.ticker)){position.setText("Paper exit unavailable · stale or different round");return;}
  try{boolean no=p.getBoolean("no",false);double bid=no?sample.noBid:sample.bid;double n=Rules.net(Double.parseDouble(p.getString("qty","1")),Double.parseDouble(p.getString("entry","50"))/100,bid,Double.parseDouble(p.getString("entryFees","0")),Double.parseDouble(p.getString("exitFees","0")));position.setText(String.format(Locale.US,"Hypothetical %s exit: %+.2f USD net\nAt %.0f¢ bid, using your fees; fill size unverified.",no?"NO":"YES",n,bid*100));}catch(Exception e){position.setText("Edit paper position to correct inputs");}}
 void replay(){ArrayList<String> lines=new ArrayList<>();try(BufferedReader r=new BufferedReader(new FileReader(new File(getFilesDir(),"observations.jsonl")))){String line;while((line=r.readLine())!=null){lines.add(line);if(lines.size()>15)lines.remove(0);}}catch(IOException e){}StringBuilder out=new StringBuilder();for(String line:lines){try{JSONObject j=new JSONObject(line);out.append(Instant.ofEpochMilli(j.getLong("received_at_ms"))).append("\n").append(j.getString("ticker")).append("\n").append(j.getString("direction")).append(" · ").append(String.format(Locale.US,"YES %.0f/%.0f¢",j.getDouble("yes_bid")*100,j.getDouble("yes_ask")*100)).append("\n\n");}catch(JSONException e){}}new AlertDialog.Builder(this).setTitle("Last 15 saved observations").setMessage(out.length()==0?"No recorded observations yet.":out.toString()).setPositiveButton("Close",null).show();}
 class Chart extends View {
  Paint p=new Paint(3);Chart(){super(MainActivity.this);setContentDescription("Observed BTC price or YES quote; no forecast");}
  void label(Canvas c,String s,float x,float y,int color){p.setColor(color);p.setTextSize(dp(11));c.drawText(s,x,y,p);}
  protected void onDraw(Canvas c){super.onDraw(c);ArrayList<Sample> rows;synchronized(history){rows=new ArrayList<>(history);}int w=getWidth(),h=getHeight();if(rows.size()<2){label(c,"Collecting chart history on this phone…",dp(5),h/2,MUTED);return;}Sample last=rows.get(rows.size()-1);boolean live=usable();double min=contract?0:last.strike,max=contract?1:last.strike;for(Sample s:rows){double v=contract?(s.bid+s.ask)/2:s.spot;min=Math.min(min,v);max=Math.max(max,v);}double pad=contract?0:Math.max(5,(max-min)*.15);min-=pad;max+=pad;final double low=min,range=Math.max(.01,max-min);float left=dp(8),right=w-dp(8),top=dp(35),bottom=h-dp(30);
   p.setColor(Color.rgb(42,57,62));p.setStrokeWidth(dp(1));for(int i=0;i<4;i++){float y=top+(bottom-top)*i/3;c.drawLine(left,y,right,y,p);}label(c,contract?"YES midpoint · observed cents":"BTC · observed dollars",left,dp(17),MUTED);
   if(!contract){float y=(float)(bottom-(last.strike-low)/range*(bottom-top));p.setColor(MUTED);p.setPathEffect(new DashPathEffect(new float[]{6,6},0));c.drawLine(left,y,right,y,p);p.setPathEffect(null);label(c,String.format(Locale.US,"Target $%,.2f",last.strike),left,y-dp(5),MUTED);}
   Path path=new Path();for(int i=0;i<rows.size();i++){Sample s=rows.get(i);float x=left+(right-left)*(s.received-last.open)/(float)(last.close-last.open);double v=contract?(s.bid+s.ask)/2:s.spot;float y=(float)(bottom-(v-low)/range*(bottom-top));if(i==0)path.moveTo(x,y);else path.lineTo(x,y);}p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(dp(2));p.setColor(live?GREEN:MUTED);c.drawPath(path,p);p.setStyle(Paint.Style.FILL);label(c,"Round open",left,h-dp(8),MUTED);label(c,live?"Observed · no forecast":"STALE HISTORY",w/2,h-dp(8),MUTED);
  }
 }
}
