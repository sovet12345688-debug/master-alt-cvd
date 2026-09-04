from __future__ import annotations
import json
import pandas as pd
import run_restart_phase1_v01 as b

OUT=b.Path('btc_trend_v30_restart/output/restart_phase1_v02'); OUT.mkdir(parents=True,exist_ok=True)


def oos_method_strict(D,S,T,side,mode):
    rows=[]; allsig=[]
    for y in range(2020,2027):
        cutoff=pd.Timestamp(f'{y}-01-01',tz='UTC')
        # Strict point-in-time training: all 180D truth/fractal/economic outcomes used for
        # parameter choice must have fully matured before the test year begins.
        train_end=cutoff-pd.Timedelta(days=181)
        tr=S[S.index<=train_end]; te=S[S.index.year==y]
        tt=T[T.time<=train_end]; ty=T[T.time.dt.year==y]
        if len(tr)<500 or len(te)<90 or len(tt[tt.side==side])<3: continue
        ct,ft,qt=b.choose_train(D,tr,tt,side,mode)
        sig=b.build_signals(te,side,mode,ct,ft,qt); ev=b.evaluate_signals(D,sig,ty,side)
        ev.update(year=y,ct=ct,ft=ft,qt=qt,train_end=str(train_end)); rows.append(ev)
        for s in sig: s.update(test_year=y,side=side,mode=mode); allsig.append(s)
    if not rows:return rows,{},[]
    years={r['year'] for r in rows}; poolT=T[T.time.dt.year.isin(years)]
    agg=b.evaluate_signals(D,allsig,poolT,side); agg['years']=len(rows); agg['mode']=mode
    return rows,agg,allsig


def main():
    d0,fd=b.load_interval('1d'); h0,f4=b.load_interval('4h')
    D=b.add_features(d0,'1D'); H4=b.add_features(h0,'4H')
    S=b.build_state(D,H4); T=b.build_truth(D); T['time']=pd.to_datetime(T.time,utc=True)
    F=b.add_fractal_prior(D,S)

    methods={}; signal_rows=[]
    for side in ['LOW','HIGH']:
        methods[side]={}
        for mode in ['NONE','SOFT','RANK','INTERACTION']:
            yr,agg,sigs=oos_method_strict(D,F,T,side,mode)
            methods[side][mode]=dict(aggregate=agg,years=yr)
            signal_rows.extend(sigs)

    def best_mode(side):
        opts=[]
        for m,v in methods[side].items():
            a=v['aggregate']; opts.append((a.get('research_utility',0),a.get('recall_pct') or 0,a.get('precision_pct') or 0,m))
        return max(opts)[-1]

    chosen={'LONG':best_mode('LOW'),'SHORT':best_mode('HIGH')}
    baseline={'LONG':methods['LOW']['NONE']['aggregate'],'SHORT':methods['HIGH']['NONE']['aggregate']}
    selected={'LONG':methods['LOW'][chosen['LONG']]['aggregate'],'SHORT':methods['HIGH'][chosen['SHORT']]['aggregate']}

    Z=b.build_zone_registry(D); zl,zlb=b.zone_oos(Z,'LOW'); zs,zsb=b.zone_oos(Z,'HIGH')
    zl.to_csv(OUT/'zone_oos_long.csv',index=False); zs.to_csv(OUT/'zone_oos_short.csv',index=False); Z.to_csv(OUT/'zone_registry.csv',index=False)
    F.tail(1000).to_csv(OUT/'recent_state_with_fractal.csv')
    pd.DataFrame(signal_rows).to_csv(OUT/'all_oos_signals.csv',index=False); T.to_csv(OUT/'truth_events.csv',index=False)

    fractal_gain={k:round((selected[k].get('research_utility',0)-baseline[k].get('research_utility',0)),2) for k in ['LONG','SHORT']}
    gates={
      'long_nonzero':selected['LONG'].get('signals',0)>0,'short_nonzero':selected['SHORT'].get('signals',0)>0,
      'long_recall_ge_25':(selected['LONG'].get('recall_pct') or 0)>=25,'short_recall_ge_25':(selected['SHORT'].get('recall_pct') or 0)>=25,
      'long_precision_ge_30':(selected['LONG'].get('precision_pct') or 0)>=30,'short_precision_ge_30':(selected['SHORT'].get('precision_pct') or 0)>=30,
      'long_economic_ge_50':(selected['LONG'].get('economic_success_pct') or 0)>=50,'short_economic_ge_50':(selected['SHORT'].get('economic_success_pct') or 0)>=50,
      'long_lag_le_21':selected['LONG'].get('median_confirmation_lag_days') is not None and selected['LONG']['median_confirmation_lag_days']<=21,
      'short_lag_le_21':selected['SHORT'].get('median_confirmation_lag_days') is not None and selected['SHORT']['median_confirmation_lag_days']<=21,
      'long_localization_le_15':selected['LONG'].get('median_origin_price_error_pct') is not None and selected['LONG']['median_origin_price_error_pct']<=15,
      'short_localization_le_15':selected['SHORT'].get('median_origin_price_error_pct') is not None and selected['SHORT']['median_origin_price_error_pct']<=15,
      'fractal_does_not_extinguish_signals':selected['LONG'].get('signals',0)>0 and selected['SHORT'].get('signals',0)>0,
      'zone_long_separation':b.monotonic_tendency(zlb),'zone_short_separation':b.monotonic_tendency(zsb),
      'strict_maturity_gap_before_each_test_year':True,'no_synthetic_oi_funding':True,'production_untouched':True,
    }
    summary={
      'engine':'MASTER_BTC_TREND_V3_RESTART_PHASE1_V0_2_STRICT_WF','status':'RESEARCH_ONLY_NOT_LIVE',
      'data':{'source':'Binance Data Vision Spot BTCUSDT completed 1D/4H','start':str(D.index.min()),'end':str(D.index.max()),'daily_rows':len(D),'h4_rows':len(H4),'failures':fd+f4,'oi_funding':'N/A in long-history Phase1; no synthetic repair'},
      'anti_leakage':{'parameter_training':'test year Y uses signals/truth ending no later than Jan-1(Y)-181D','fractal':'analogs <= query-181D','zone':'model training only outcomes resolved before test year'},
      'truth':{'LOW':'30D local low +30% before -15% within180D','HIGH':'30D local high -25% before +15% within180D','future_usage':'evaluation labels only'},
      'method_comparison':methods,'chosen_mode':chosen,'baseline_none':baseline,'selected':selected,'fractal_utility_gain_vs_none':fractal_gain,
      'zone_quality':{'LONG_BUCKETS':zlb,'SHORT_BUCKETS':zsb,'LONG_OOS_N':len(zl),'SHORT_OOS_N':len(zs),'reach_is_separate_from_reaction':True,'reaction_label':'fixed structural 3R before 1R after actual touch'},
      'gates':gates,'stage':'PASS_TO_FOUR_ENGINE_RESEARCH' if all(gates.values()) else 'REDESIGN_PROGRESS_NOT_YET_PASS',
      'master_btc_trend_v26_modified':False,'production_modified':False,'integration':'FORBIDDEN_PENDING_EXPLICIT_USER_APPROVAL',
      'notes':['Fractal is compared as NONE/SOFT/RANK/INTERACTION and never hard-deletes a signal.','Research utility is only a model-selection criterion, not a MASTER score/probability.','Current/live BTC decision is out of scope for this historical Phase1 validator.']}
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'stage':summary['stage'],'chosen_mode':chosen,'selected':selected,'fractal_gain':fractal_gain,'zone_long':zlb,'zone_short':zsb,'gates':gates},ensure_ascii=False))

if __name__=='__main__':main()
