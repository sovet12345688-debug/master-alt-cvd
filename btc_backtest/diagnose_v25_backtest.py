from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import run_v25_backtest as bt

OUT = Path('btc_backtest/output')
OUT.mkdir(parents=True, exist_ok=True)


def rr(entry, sl, target):
    risk = entry - sl
    reward = target - entry
    return reward / risk if risk > 0 and reward > 0 else -999.0


def safety(entry, sl):
    if not (entry > sl > 0):
        return 0
    dist = (entry - sl) / entry
    return 90 if dist <= 0.12 else (85 if dist <= 0.15 else (80 if dist <= 0.18 else 70))


def persistence(h, pos, zone):
    r = h.iloc[pos]
    a = pos >= 1 and r.close >= zone['center'] and h.close.iloc[pos-1] >= zone['center']
    b = r.close > zone['high'] and pos >= 2 and h.low.iloc[pos-2:pos+1].min() >= zone['low'] * 0.997
    return bool(a or b)


def independent_groups(h, pos, zone, ex, er):
    r = h.iloc[pos]
    prev3 = h.ret3.iloc[pos-3] if pos >= 3 and pd.notna(h.ret3.iloc[pos-3]) else np.nan
    groups = {
        'price_defense': bool((r.low <= zone['high'] and r.close >= zone['center']) or r.close > zone['high']),
        'volume_absorption': bool(pd.notna(r.vol_z) and r.vol_z >= 1.0 and r.close_loc >= 0.55),
        'taker_price_divergence': bool(pd.notna(r.taker_imb6) and r.taker_imb6 <= -0.05 and r.close >= zone['center']),
        'momentum_turn': bool(r.rsi14 <= 38 and er >= 70),
        'speed_deceleration': bool(pd.notna(prev3) and pd.notna(r.ret3) and r.ret3 > prev3 + 0.008),
        'wick_reclaim': bool(r.lower_wick_ratio >= 0.30 or r.close > zone['high']),
    }
    return groups, sum(groups.values())


def outcome(h, entry_time, entry_price, sl, target, expiry):
    fut = h[(h.index >= entry_time) & (h.index <= min(expiry, entry_time + pd.Timedelta(days=30)))]
    if fut.empty:
        return {}
    out = {}
    for n in [4, 24, 72]:
        out[f'ret{n}h'] = float(fut.close.iloc[min(n, len(fut)-1)] / entry_price - 1)
    out['mfe'] = float(fut.high.max() / entry_price - 1)
    out['mae'] = float(fut.low.min() / entry_price - 1)
    out['first_hit'] = 'none'
    out['hit_time'] = pd.NaT
    for t, b in fut.iterrows():
        # Conservative: if both are possible in same bar, SL wins because checked first.
        if b.low <= sl:
            out['first_hit'] = 'SL'; out['hit_time'] = t; break
        if b.high >= target:
            out['first_hit'] = 'TP1'; out['hit_time'] = t; break
    return out


def summarize_signals(df, prefix):
    sig = df[df[f'{prefix}_time'].notna()].copy()
    result = {'signals': int(len(sig))}
    if not len(sig):
        return result
    result['sl_first_rate_pct'] = round(100 * (sig[f'{prefix}_first_hit'] == 'SL').mean(), 1)
    result['tp1_first_rate_pct'] = round(100 * (sig[f'{prefix}_first_hit'] == 'TP1').mean(), 1)
    for k in ['ret4h','ret24h','ret72h','mfe','mae']:
        result[f'median_{k}_pct'] = round(100 * sig[f'{prefix}_{k}'].median(), 2)
    return result


def run():
    h, failures = bt.load_data(); h = bt.enrich_hourly(h)
    h4 = bt.resample_ohlcv(h, '4h'); d = bt.resample_ohlcv(h, '1D'); w = bt.resample_ohlcv(h, 'W-SUN')
    plans = bt.build_plans(h, d, w, h4)
    rows = []
    htimes = h.index

    for p in plans:
        win = h[(h.index >= p['start']) & (h.index <= p['expiry'])]
        if win.empty: continue
        drow = d.loc[d.index <= p['reg']].iloc[-1]
        wrow = bt.latest_row_before(w, p['reg'])
        r4reg = bt.latest_row_before(h4, p['reg'])
        for z in p['zones']:
            hits = win[(win.low <= z['high']) & (win.high >= z['low'])]
            if hits.empty: continue
            touch = hits.index[0]; tp = htimes.get_loc(touch); tr = h.iloc[tp]
            zq = bt.zone_quality(p, z, tr, drow, wrow, r4reg)
            rr_mid = rr(z['center'], p['sl'], p['anchor_high'])
            rec = {
                'plan': p['id'], 'zone': z['n'], 'touch': touch, 'zone_quality': zq,
                'zone_mid': z['center'], 'zone_low': z['low'], 'zone_high': z['high'],
                'sl': p['sl'], 'target': p['anchor_high'], 'rr_mid': rr_mid,
                'fear_any_24h': False, 'fear_any_72h': False,
                'max_exhaust_24h': 0, 'max_early_24h': 0, 'max_reaction_24h': 0,
                'max_exhaust_72h': 0, 'max_early_72h': 0, 'max_reaction_72h': 0,
                'max_indep_72h': 0,
            }
            variants = ['early_core72','early_rr3_72','early_rr35_72','early_rr4_72','fast_core72','fast_rr3_72']
            for v in variants:
                rec[f'{v}_time'] = pd.NaT; rec[f'{v}_price'] = np.nan
            core_rows_24 = 0; core_rows_72 = 0; fast_core_rows_72 = 0
            blockers = {k:0 for k in ['zone','fear','exhaust','early','safety','nonchase','independent','rr4_close','rr4_mid']}
            end = min(len(h)-1, tp+72)
            for pos in range(tp, end+1):
                r = h.iloc[pos]
                fear = sum(bt.fear_flags(h, pos).values()) >= 2
                ex = bt.exhaust_score(h, pos, z); er = bt.early_score(h, pos, z, ex); re = bt.reaction_score(h, pos, z, drow, ex)
                groups, indep = independent_groups(h, pos, z, ex, er)
                sp = safety(float(r.close), p['sl']); nc = bt.nonchase(r); rrc = rr(float(r.close), p['sl'], p['anchor_high'])
                if pos <= tp+24:
                    rec['fear_any_24h'] |= fear; rec['max_exhaust_24h'] = max(rec['max_exhaust_24h'], ex); rec['max_early_24h'] = max(rec['max_early_24h'], er); rec['max_reaction_24h'] = max(rec['max_reaction_24h'], re)
                rec['fear_any_72h'] |= fear; rec['max_exhaust_72h'] = max(rec['max_exhaust_72h'], ex); rec['max_early_72h'] = max(rec['max_early_72h'], er); rec['max_reaction_72h'] = max(rec['max_reaction_72h'], re); rec['max_indep_72h'] = max(rec['max_indep_72h'], indep)
                base_checks = {'zone':zq>=82,'fear':fear,'exhaust':ex>=70,'early':er>=75,'safety':sp>=85,'nonchase':nc,'independent':indep>=3}
                for k,ok in base_checks.items():
                    if not ok: blockers[k]+=1
                if rrc < 4: blockers['rr4_close'] += 1
                if rr_mid < 4: blockers['rr4_mid'] += 1
                core = all(base_checks.values())
                if core:
                    core_rows_72 += 1
                    if pos <= tp+24: core_rows_24 += 1
                    if pd.isna(rec['early_core72_time']): rec['early_core72_time']=h.index[pos]; rec['early_core72_price']=float(r.close)
                    if rrc>=3 and pd.isna(rec['early_rr3_72_time']): rec['early_rr3_72_time']=h.index[pos]; rec['early_rr3_72_price']=float(r.close)
                    if rrc>=3.5 and pd.isna(rec['early_rr35_72_time']): rec['early_rr35_72_time']=h.index[pos]; rec['early_rr35_72_price']=float(r.close)
                    if rrc>=4 and pd.isna(rec['early_rr4_72_time']): rec['early_rr4_72_time']=h.index[pos]; rec['early_rr4_72_price']=float(r.close)
                persist = persistence(h,pos,z)
                fastcore = zq>=80 and float(r.close)>z['high'] and persist and re>=65 and sp>=80 and nc
                if fastcore:
                    fast_core_rows_72 += 1
                    if pd.isna(rec['fast_core72_time']): rec['fast_core72_time']=h.index[pos]; rec['fast_core72_price']=float(r.close)
                    if rrc>=3 and pd.isna(rec['fast_rr3_72_time']): rec['fast_rr3_72_time']=h.index[pos]; rec['fast_rr3_72_price']=float(r.close)
            rec['early_core_rows_24h'] = core_rows_24; rec['early_core_rows_72h'] = core_rows_72; rec['fast_core_rows_72h'] = fast_core_rows_72
            rec['blockers'] = json.dumps(blockers, ensure_ascii=False)
            for v in variants:
                if pd.notna(rec[f'{v}_time']):
                    o = outcome(h, rec[f'{v}_time'], float(rec[f'{v}_price']), p['sl'], p['anchor_high'], p['expiry'])
                    for k,val in o.items(): rec[f'{v}_{k}'] = val
            rows.append(rec)

    ev = pd.DataFrame(rows)
    summary = {
        'schema':'MASTER_BTC_TREND_V2.5_DIAGNOSTIC_V2',
        'generated_utc':datetime.now(timezone.utc).isoformat(),
        'data_rows':int(len(h)), 'plans':int(len(plans)), 'zone_touch_events':int(len(ev)), 'download_failures':failures,
        'window_test':'24h original proxy vs 72h diagnostic',
        'variants':{},
        'rr_mid_distribution':{},
        'gate_reach':{},
        'interpretation_notes':[
            'Stage-1 still excludes historical OI/Funding and ETF/macro veto.',
            'actual-close variants are causally executable proxies; zone-mid RR is sensitivity only, not assumed fill.',
            'No MASTER threshold is changed by this diagnostic.',
        ]
    }
    for v in ['early_core72','early_rr3_72','early_rr35_72','early_rr4_72','fast_core72','fast_rr3_72']:
        summary['variants'][v] = summarize_signals(ev,v)
    if len(ev):
        summary['rr_mid_distribution'] = {
            'median':round(float(ev.rr_mid.median()),2),
            'ge3_events':int((ev.rr_mid>=3).sum()), 'ge35_events':int((ev.rr_mid>=3.5).sum()), 'ge4_events':int((ev.rr_mid>=4).sum()),
            'zone1_median':round(float(ev.loc[ev.zone==1,'rr_mid'].median()),2) if (ev.zone==1).any() else None,
            'zone2_median':round(float(ev.loc[ev.zone==2,'rr_mid'].median()),2) if (ev.zone==2).any() else None,
            'zone3_median':round(float(ev.loc[ev.zone==3,'rr_mid'].median()),2) if (ev.zone==3).any() else None,
        }
        summary['gate_reach'] = {
            'zone_ge82_events':int((ev.zone_quality>=82).sum()),
            'fear72_events':int(ev.fear_any_72h.sum()),
            'exhaust70_events':int((ev.max_exhaust_72h>=70).sum()),
            'early75_events':int((ev.max_early_72h>=75).sum()),
            'reaction65_events':int((ev.max_reaction_72h>=65).sum()),
            'independent3_events':int((ev.max_indep_72h>=3).sum()),
            'early_core_simultaneous_events_24h':int((ev.early_core_rows_24h>0).sum()),
            'early_core_simultaneous_events_72h':int((ev.early_core_rows_72h>0).sum()),
            'fast_core_simultaneous_events_72h':int((ev.fast_core_rows_72h>0).sum()),
        }
    (OUT/'v25_diagnostic_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    ev.to_csv(OUT/'v25_diagnostic_events.csv',index=False)
    lines=['# MASTER BTC TREND V2.5 Diagnostic Pass','',f"- 1H rows: {summary['data_rows']:,}",f"- Plans: {summary['plans']}",f"- Zone-touch events: {summary['zone_touch_events']}",'','## Gate reach', '']
    for k,v in summary['gate_reach'].items(): lines.append(f'- {k}: **{v}**')
    lines += ['', '## R:R sensitivity (zone midpoint reference only)', '']
    for k,v in summary['rr_mid_distribution'].items(): lines.append(f'- {k}: **{v}**')
    lines += ['', '## Executable actual-close variants', '', '| Variant | Signals | SL-first | TP1-first | Median +24h | Median +72h | Median MFE | Median MAE |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for v in ['early_core72','early_rr3_72','early_rr35_72','early_rr4_72','fast_core72','fast_rr3_72']:
        s=summary['variants'][v]; lines.append(f"| {v} | {s.get('signals',0)} | {s.get('sl_first_rate_pct','N/A')} | {s.get('tp1_first_rate_pct','N/A')} | {s.get('median_ret24h_pct','N/A')} | {s.get('median_ret72h_pct','N/A')} | {s.get('median_mfe_pct','N/A')} | {s.get('median_mae_pct','N/A')} |")
    lines += ['', '## Guardrail', '- This pass diagnoses the test harness. It does not automatically alter V2.5 thresholds.']
    (OUT/'v25_diagnostic_report.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2,default=str))

if __name__ == '__main__':
    run()
