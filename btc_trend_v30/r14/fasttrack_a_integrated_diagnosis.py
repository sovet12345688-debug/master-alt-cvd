from __future__ import annotations
import argparse, json
from pathlib import Path
import pandas as pd


def b(s):
    if s.dtype == bool: return s
    return s.astype(str).str.lower().eq('true')

def rstats(df):
    r=pd.to_numeric(df['realized_unit_R'],errors='coerce').dropna()
    return {'n':int(len(df)),'wins':int((r>0).sum()),'losses':int((r<0).sum()),'sum_R':float(r.sum()),'mean_R':float(r.mean()) if len(r) else None}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--snapshot-root',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    root=Path(a.snapshot_root)/'r13/output/historical_diagnostic'
    legs=pd.read_csv(root/'r13_trade_legs.csv'); ep=pd.read_csv(root/'r13_episode_metrics.csv')
    for c in ['confirm_time','resolved_time']: legs[c]=pd.to_datetime(legs[c],utc=True,format='mixed',errors='coerce')
    first=legs[legs.leg.eq('1H_ENTRY')].sort_values('confirm_time').drop_duplicates('episode_id').copy()
    add=legs[legs.leg.eq('4H_ADD')].copy()
    fs=first[['episode_id','route','resolved_time','realized_unit_R','outcome']].rename(columns={'route':'first_route','resolved_time':'first_resolved','realized_unit_R':'first_R','outcome':'first_outcome'})
    x=add.merge(fs,on='episode_id',how='left',validate='many_to_one')
    x['route_changed']=x.route.ne(x.first_route)
    x['after_first_resolved']=x.confirm_time>x.first_resolved
    x['first_loss']=pd.to_numeric(x.first_R,errors='coerce').eq(-1.0)
    x['first_win']=pd.to_numeric(x.first_R,errors='coerce').eq(3.0)
    sc=x[x.engine.eq('SC')]
    route_change=x[x.route_changed]
    first_loss=x[x.first_loss]
    first_loss_short=first_loss[first_loss.direction.eq('short')]
    first_loss_caution=first_loss[first_loss.risk_state.eq('CAUTION')]
    first_loss_retest=first_loss[first_loss.route.eq('RETEST')]
    false_complete=ep[b(ep.available_90d) & b(ep.entry_1h)]
    false_starts=false_complete[b(false_complete.confirmed_false_start_90d)]
    checks={
      'episodes_136':len(ep)==136,'first_80':len(first)==80,'adds_64':len(add)==64,
      'first_sum_16':abs(rstats(first)['sum_R']-16)<1e-12,'add_sum_minus8':abs(rstats(add)['sum_R']+8)<1e-12,
      'sc_add_11_all_loss':len(sc)==11 and rstats(sc)['wins']==0,
      'route_change_12_all_loss':len(route_change)==12 and rstats(route_change)['wins']==0,
      'first_loss_add_45':len(first_loss)==45,
      'first_loss_short_25_all_loss':len(first_loss_short)==25 and rstats(first_loss_short)['wins']==0,
      'first_loss_caution_20_all_loss':len(first_loss_caution)==20 and rstats(first_loss_caution)['wins']==0,
      'first_loss_retest_34':len(first_loss_retest)==34,
      'false_start_39_of_78':len(false_complete)==78 and len(false_starts)==39,
    }
    if not all(checks.values()): raise SystemExit('A_INTEGRATED_DIAGNOSIS_IDENTITY_FAIL '+json.dumps(checks))
    out={
      'status':'A_INTEGRATED_DIAGNOSIS_PASS_DIAGNOSTIC_ONLY',
      'checks':checks,
      'core':{
        'first_1h':rstats(first),'add_4h':rstats(add),'edge_given_back_pct':50.0,
        'sc_add':rstats(sc),'route_change_add':rstats(route_change),
        'first_loss_then_add':rstats(first_loss),'first_loss_short':rstats(first_loss_short),
        'first_loss_caution':rstats(first_loss_caution),'first_loss_retest':rstats(first_loss_retest),
        'false_start_rate':len(false_starts)/len(false_complete)
      },
      'integrated_bottlenecks_ranked':[
        {'rank':1,'name':'4H_ADD_THESIS_REUSE','evidence':'First 1H edge +16R; 4H ADD -8R. First-loss then ADD 45 cases = 3W/42L, -33R.'},
        {'rank':2,'name':'SHORT_REENTRY_AFTER_FAILED_THESIS','evidence':'First-loss SHORT ADD 25 cases = 0W/25L.'},
        {'rank':3,'name':'RETEST_RECONFIRMATION_WEAKNESS','evidence':'First-loss RETEST ADD 34 cases = 1W/33L.'},
        {'rank':4,'name':'SC_FALSE_CONTINUATION','evidence':'SC 4H ADD 11 cases = 0W/11L; prior forensic found 10/11 without 20% 90D trend.'},
        {'rank':5,'name':'ROUTE_RECLASSIFICATION','evidence':'Route-changed ADD 12 cases = 0W/12L.'},
        {'rank':6,'name':'SAFETY_SCORE_SATURATION','evidence':'Prior false-start and SC audits showed Safety=100 across both good and bad cohorts; weak discrimination.'},
        {'rank':7,'name':'TREND_CAPTURE_EXIT_BOTTLENECK','evidence':'R1.3 entry timing improved strongly but MCR remained 6.70%/3.57%, indicating position-management/exit capture remains a separate bottleneck.'}
      ],
      'design_constraints_for_r14':[
        'Keep R1.3 1H RETEST/IGNITION entry architecture unchanged as baseline signal layer.',
        '4H may not behave as an independent second trade; it must prove continuity of the original 1H thesis.',
        'No 4H ADD after the initial 1H leg has resolved TP or SL within the same episode.',
        '4H ADD route must equal the original 1H route; route changes are not ADDs.',
        '4H ADD requires OPEN risk state and directional daily transition alignment.',
        '4H ADD requires objective favorable progress before adding; freeze rule before replay.',
        'Introduce a runner exit architecture so large-trend MCR can improve without loosening entry quality.',
        'No threshold selection from the R1.3 historical diagnostic after R1.4 freeze.'
      ]
    }
    Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
