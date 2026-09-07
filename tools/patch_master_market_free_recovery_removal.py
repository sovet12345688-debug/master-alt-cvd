from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANON = ROOT / 'master_prompts' / 'master_market_v1_2_current.md'
CONTRACT = ROOT / 'state' / 'master_market_v1_2_contract.json'
KST = timezone(timedelta(hours=9))


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise SystemExit(f'PATCH_FAIL missing canonical target: {label}')
    return text.replace(old, new, 1)


def patch_canonical() -> None:
    text = CANON.read_text(encoding='utf-8')

    text = replace_required(
        text,
        'Fed/QE-QT, US Net Liquidity, TGA, Fed Reserves, Treasury/QRA, Treasury Buyback actual accepted/settlement when available, 2Y/10Y/30Y, 10Y real yield, EFFR, SOFR, DXY, WTI, Brent, Global M2, Nasdaq, S&P500, geopolitics, regulation.',
        'Fed/QE-QT, US Net Liquidity, TGA, Fed Reserves, Treasury/QRA, Treasury Buyback actual accepted/settlement when available, 2Y/10Y/30Y, 10Y real yield, EFFR, SOFR, DXY, WTI, Brent, Nasdaq, S&P500, geopolitics, regulation.',
        'macro required map remove Global M2',
    )
    text = replace_required(
        text,
        'BTC ETF and ETH ETF 1D/3D/5D/20D; institutional flow; USDT/USDC/total stablecoin supply; Mint/Burn; Treasury balance; Treasury→Exchange; Exchange Balance; `Mint→Treasury→Exchange→Spot Buy` chain.',
        'BTC ETF and ETH ETF 1D/3D/5D/20D; institutional flow; USDT/USDC/total stablecoin supply. Stablecoin supply is potential dry powder only and must not be presented as confirmed spot buying.',
        'crypto capital flow remove wallet-tracking chain',
    )
    text = replace_required(
        text,
        'Axes when available: US Net Liquidity, TGA change, Fed Reserves, 10Y real yield, DXY, Treasury/QRA, Buyback, Global M2, ETF, Stablecoin Flow. If at least one confirmed weight exists, renormalize confirmed weights and output a partial numeric score.',
        'Axes when available: US Net Liquidity, TGA change, Fed Reserves, 10Y real yield, DXY, Treasury/QRA, Buyback, ETF, Stablecoin Flow. If at least one confirmed weight exists, renormalize confirmed weights and output a partial numeric score.',
        'liquidity lead remove Global M2',
    )
    text = replace_required(
        text,
        'ETF: today/3D/5D/20D where confirmed. Stablecoin supply increase != actual buy. Without Treasury→Exchange/spot-buy confirmation, classify as potential dry powder only.',
        'ETF: today/3D/5D/20D where confirmed. Stablecoin supply increase != actual buy. USDT/USDC/total stablecoin supply changes are potential dry powder only; do not infer actual spot buying from supply changes alone.',
        'ETF stablecoin policy',
    )
    text = replace_required(
        text,
        'LEVEL1: BTC/ETH large position reversal; >=$50M new/increase/decrease; liq-distance collapse; liquidation cascade; major exchange in/out; major stablecoin Treasury→Exchange; hack/exploit; policy/macro/oil shock; major unlock/supply shock.',
        'LEVEL1: BTC/ETH large position reversal; >=$50M new/increase/decrease; liq-distance collapse; liquidation cascade; major exchange in/out; major confirmed stablecoin supply shock/exit; hack/exploit; policy/macro/oil shock; major unlock/supply shock.',
        'urgent watch stablecoin removed field',
    )
    text = replace_required(
        text,
        'BTC ETF/ETH ETF today/3D/5D/20D; USDT/USDC/total supply/Mint/Burn/Treasury/Exchange; Crypto Money Inflow/100; ALT Money Inflow/100. Mini-trend only when >=3 actual OFFICIAL points.',
        'BTC ETF/ETH ETF today/3D/5D/20D; USDT/USDC/total stablecoin supply; Crypto Money Inflow/100; ALT Money Inflow/100. Mini-trend only when >=3 actual OFFICIAL points.',
        'screen3 removed fields',
    )
    # SCREEN2 implementation line has existed in this exact compact form in current FINAL.
    text = text.replace(
        'Must attempt: global liquidity, 2Y/10Y/30Y, 10Y real yield, DXY, WTI, Brent, Fed/TGA/Reserves/QRA/Buyback/M2/equities as applicable. Keep missing rows as N/A.',
        'Must attempt: global liquidity, 2Y/10Y/30Y, 10Y real yield, DXY, WTI, Brent, Fed/TGA/Reserves/QRA/Buyback/equities as applicable. Keep missing required rows as N/A.',
    )

    marker = '## WHALE RULES\n'
    block = '''## FREE RECOVERY + EXPLICIT REMOVAL LOCK — ADDED 2026-09-07\n\nUser explicitly approved the following recovery/removal sequence; it is now part of the canonical data contract.\n\n1) SCREEN4 free Bitget derivatives recovery\n- Read `derivatives/output/latest_microstructure.json` when fresh and engine/schema guard passes.\n- Expected free public fields = `CVD | Taker Buy/Sell | Long/Short | Liquidation | Basis | Depth | Volume` for BTC/ETH on the same Bitget futures venue.\n- These fields remain required. If a current collector/API call fails, keep the affected field N/A and explain it under the screen-level N/A 안내; do not silently delete it.\n\n2) Free macro/liquidity recovery\n- Read `market_vault/output/latest_macro_liquidity.json` when fresh and engine/schema guard passes.\n- Use its official-source evidence for `US Net Liquidity proxy | Fed total assets/reserve balances/reverse repo | TGA | Treasury/QRA | actual Treasury Buyback accepted/offered`.\n- US Net Liquidity is explicitly a formula proxy, not an official Fed-published index. Preserve the formula/source label.\n- Its same-source history may fill 1D/3D/7D only after actual comparable observations exist; no backfill/interpolation.\n\n3) ETF20D + stablecoin supply recovery\n- Read `market_vault/output/latest_etf_flows.json` when fresh and engine/schema guard passes for BTC/ETH ETF `1D/3D/5D/20D`. A public GitHub mirror may be used only as transport/cache for Farside-derived history; when material, current OFFICIAL should cross-check the latest trading-date/value against a public independent/primary/secondary confirmation.\n- Read `market_vault/output/latest_summary.json` when fresh for `USDT_SUPPLY | USDC_SUPPLY | STABLECOIN_TOTAL_SUPPLY` and same-source historical comparisons.\n- Stablecoin supply is dry-powder context only. Supply change alone never proves actual spot buying.\n\n4) Explicitly removed required/output fields\nBy explicit user command, the following are REMOVED from MASTER MARKET required data and user-visible output: `Global M2`, stablecoin `Mint/Burn`, stablecoin issuer `Treasury balance`, `Treasury→Exchange`, `Exchange Balance`, and `Mint→Treasury→Exchange→Spot Buy` confirmation chain.\n- Removed fields must NOT be printed as recurring N/A rows and must NOT trigger N/A 안내.\n- `stablecoin issuer Treasury balance` removal is NOT the same as U.S. Treasury `TGA`; TGA remains mandatory macro/liquidity data.\n- Existing collectors/files may remain in the repository for compatibility/history, but MASTER MARKET must not require or display the removed fields unless the user explicitly restores them.\n- Do not silently replace a removed field with a different metric under the same name.\n\n'''
    if '## FREE RECOVERY + EXPLICIT REMOVAL LOCK — ADDED 2026-09-07' not in text:
        if marker not in text:
            raise SystemExit('PATCH_FAIL WHALE marker missing')
        text = text.replace(marker, block + marker, 1)

    # Add direct implementation paths if absent.
    impl_marker = '- Use `derivatives/output/latest_summary.json` when fresh for venue-locked Price/OI/Funding and 1H/4H/24H changes.\n'
    impl_add = (
        impl_marker
        + '- Use `derivatives/output/latest_microstructure.json` when fresh for same-venue Bitget CVD/Taker Buy-Sell/Long-Short/Liquidation/Basis/Depth/Volume.\n'
        + '- Use `market_vault/output/latest_macro_liquidity.json` when fresh for the free official-source US Net Liquidity proxy/Fed/QRA/actual Buyback evidence.\n'
        + '- Use `market_vault/output/latest_etf_flows.json` when fresh for BTC/ETH ETF 1D/3D/5D/20D; its mirror is transport/cache only, not a new score/source owner.\n'
    )
    if '- Use `derivatives/output/latest_microstructure.json`' not in text:
        if impl_marker not in text:
            raise SystemExit('PATCH_FAIL implementation marker missing')
        text = text.replace(impl_marker, impl_add, 1)

    # Explicitly forbid old removed rows in final user-visible locks.
    final_marker = 'Easy Korean, minimal English. No actual Entry. Price rise alone cannot raise positive score. Required items never silently disappear. Missing required data = N/A row/block.\n'
    final_new = final_marker + 'Explicitly removed fields are not required items: do not show `Global M2` or the five removed stablecoin wallet-tracking rows as N/A.\n'
    if 'Explicitly removed fields are not required items:' not in text:
        if final_marker not in text:
            raise SystemExit('PATCH_FAIL final lock marker missing')
        text = text.replace(final_marker, final_new, 1)

    CANON.write_text(text, encoding='utf-8')


def patch_contract() -> None:
    c = json.loads(CONTRACT.read_text(encoding='utf-8'))
    c['schema_version'] = '1.6'
    c['updated_kst'] = datetime.now(KST).replace(microsecond=0).isoformat()

    macro = c['required_data']['macro_liquidity']
    c['required_data']['macro_liquidity'] = [x for x in macro if x != 'Global M2']

    removed_crypto = {
        'Mint/Burn',
        'Treasury balances',
        'Treasury-to-Exchange',
        'Exchange Balance',
        'Mint->Treasury->Exchange->Spot Buy confirmation chain',
    }
    crypto = c['required_data']['crypto_capital_flow']
    c['required_data']['crypto_capital_flow'] = [x for x in crypto if x not in removed_crypto]

    c['source_policy']['stablecoin'] = 'USDT/USDC/total stablecoin supply are potential dry-powder context only. Supply increase/decrease alone is not confirmed spot buying/selling. Removed wallet-tracking fields are not required and must not be shown as recurring N/A.'

    s3 = c['required_output']['screen3']
    s3 = [x for x in s3 if x != 'stablecoin supply/mint/burn/treasury/exchange']
    if 'USDT/USDC/total stablecoin supply; same-source history when available' not in s3:
        s3.insert(1, 'USDT/USDC/total stablecoin supply; same-source history when available')
    if 'removed wallet-tracking fields and Global M2 must not appear as recurring N/A' not in s3:
        s3.append('removed wallet-tracking fields and Global M2 must not appear as recurring N/A')
    c['required_output']['screen3'] = s3

    c['free_recovery_policy'] = {
        'enabled': True,
        'score_weight_change': 0,
        'derivatives_microstructure': {
            'output': 'derivatives/output/latest_microstructure.json',
            'required_engine': 'MASTER_DERIVATIVES_MICROSTRUCTURE_V2',
            'venue': 'Bitget USDT Futures',
            'fields': ['CVD', 'Taker Buy/Sell', 'Long/Short', 'Liquidation', 'Basis', 'Depth', 'Volume'],
            'source_type': 'free public Bitget market APIs; no trading auth',
            'failure_behavior': 'Keep only actually affected required fields as N/A and explain under SCREEN4 N/A 안내; never cross-fill venue or guess.'
        },
        'macro_liquidity': {
            'output': 'market_vault/output/latest_macro_liquidity.json',
            'required_engine': 'MASTER_MARKET_FREE_MACRO_LIQUIDITY_V2',
            'fields': ['US_NET_LIQUIDITY_PROXY', 'FED_TOTAL_ASSETS', 'FED_RESERVE_BALANCES', 'FED_RRP_TOTAL', 'TGA_CLOSING_BALANCE', 'QRA_NET_MARKETABLE_BORROWING_CURRENT_Q', 'QRA_END_CASH_BALANCE_CURRENT_Q', 'QRA_NET_MARKETABLE_BORROWING_NEXT_Q', 'QRA_END_CASH_BALANCE_NEXT_Q', 'TREASURY_BUYBACK_ACTUAL_ACCEPTED'],
            'source_type': 'free official Federal Reserve + U.S. Treasury sources',
            'net_liquidity_label_rule': 'Must identify US Net Liquidity as a proxy formula, not an official Fed-published index.',
            'history_windows': ['1D', '3D', '7D'],
            'history_rule': 'Actual same-source observations only; no backfill/interpolation.'
        },
        'etf_flows': {
            'output': 'market_vault/output/latest_etf_flows.json',
            'required_engine': 'MASTER_MARKET_FREE_ETF_FLOW_V1',
            'assets': ['BTC', 'ETH'],
            'windows': ['1D', '3D', '5D', '20D'],
            'source_policy': 'Farside-derived public history may use a public GitHub mirror as transport/cache only; latest material date/value should be cross-checked in OFFICIAL.'
        },
        'stablecoin_supply': {
            'output': 'market_vault/output/latest_summary.json',
            'metrics': ['USDT_SUPPLY', 'USDC_SUPPLY', 'STABLECOIN_TOTAL_SUPPLY'],
            'role': 'potential dry powder only; not confirmed actual spot buying'
        }
    }

    removed = [
        'Global M2',
        'stablecoin Mint/Burn',
        'stablecoin issuer Treasury balance',
        'stablecoin Treasury-to-Exchange',
        'stablecoin Exchange Balance',
        'stablecoin Mint->Treasury->Exchange->Spot Buy confirmation chain',
    ]
    c['explicitly_removed_fields'] = {
        'enabled': True,
        'authorized_by_user': True,
        'removed_from_required_data': removed,
        'removed_from_user_visible_output': removed,
        'must_not_show_as_na': True,
        'must_not_trigger_na_explanation': True,
        'tga_preservation_rule': 'U.S. Treasury TGA is NOT removed. Only stablecoin issuer Treasury-balance/wallet-tracking fields are removed.',
        'restore_rule': 'Restore only on explicit user command.'
    }
    c['na_explanation_policy']['removed_field_behavior'] = 'Explicitly removed fields are outside required output and must not be displayed as N/A or included in N/A 안내.'

    # Keep a machine-readable WATCH rule aligned with the removal.
    c['stablecoin_watch_policy'] = {
        'wallet_tracking_removed': True,
        'allowed_watch_basis': 'major confirmed stablecoin supply shock/exit or other independently confirmed remaining MASTER evidence',
        'forbidden_basis': ['Mint/Burn alone', 'Treasury-to-Exchange field', 'Exchange Balance field', 'Mint->Treasury->Exchange->Spot Buy chain']
    }

    CONTRACT.write_text(json.dumps(c, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def validate() -> None:
    text = CANON.read_text(encoding='utf-8')
    c = json.loads(CONTRACT.read_text(encoding='utf-8'))

    if c.get('schema_version') != '1.6':
        raise SystemExit('VALIDATE_FAIL schema')
    if 'Global M2' in c['required_data']['macro_liquidity']:
        raise SystemExit('VALIDATE_FAIL Global M2 still required')
    forbidden_crypto = {'Mint/Burn','Treasury balances','Treasury-to-Exchange','Exchange Balance','Mint->Treasury->Exchange->Spot Buy confirmation chain'}
    if forbidden_crypto & set(c['required_data']['crypto_capital_flow']):
        raise SystemExit('VALIDATE_FAIL stablecoin removed field still required')
    for keep in ('USDT supply','USDC supply','total stablecoin supply'):
        if keep not in c['required_data']['crypto_capital_flow']:
            raise SystemExit(f'VALIDATE_FAIL stablecoin keep missing: {keep}')
    if 'TGA' not in c['required_data']['macro_liquidity']:
        raise SystemExit('VALIDATE_FAIL TGA removed accidentally')
    fr = c.get('free_recovery_policy') or {}
    if set(fr.get('derivatives_microstructure',{}).get('fields') or []) != {'CVD','Taker Buy/Sell','Long/Short','Liquidation','Basis','Depth','Volume'}:
        raise SystemExit('VALIDATE_FAIL derivative 7 fields')
    if fr.get('macro_liquidity',{}).get('required_engine') != 'MASTER_MARKET_FREE_MACRO_LIQUIDITY_V2':
        raise SystemExit('VALIDATE_FAIL macro engine')
    if fr.get('etf_flows',{}).get('required_engine') != 'MASTER_MARKET_FREE_ETF_FLOW_V1':
        raise SystemExit('VALIDATE_FAIL ETF engine')
    if not c.get('explicitly_removed_fields',{}).get('must_not_show_as_na'):
        raise SystemExit('VALIDATE_FAIL removed N/A policy')

    required_canon = [
        '## ABSOLUTE ANTI-OMISSION LOCK',
        '## N/A EXPLANATION & RECOVERY LOCK — ADDED 2026-09-07',
        '## CHANGE-WINDOW LOCK — 1D / 3D / 7D',
        '## FREE RECOVERY + EXPLICIT REMOVAL LOCK — ADDED 2026-09-07',
        'derivatives/output/latest_microstructure.json',
        'market_vault/output/latest_macro_liquidity.json',
        'market_vault/output/latest_etf_flows.json',
        'USDT_SUPPLY | USDC_SUPPLY | STABLECOIN_TOTAL_SUPPLY',
        '### WHALE SIDE TRAFFIC-LIGHT DISPLAY LOCK — ADDED 2026-09-06',
        '### EXPERT VIEW TRAFFIC-LIGHT DISPLAY LOCK — ADDED 2026-09-07',
        '판정`은 반드시 3번째 열',
        'on-chain secondary-confirmation output block on 2026-09-05',
        '🕒 MASTER MARKET V1.2 | 실행완료:',
    ]
    for x in required_canon:
        if x not in text:
            raise SystemExit(f'VALIDATE_FAIL canonical lock missing: {x}')
    # Global M2 may appear only in explicit removal-history language, never active requirements/axes/screen2.
    bad_active = [
        'WTI, Brent, Global M2, Nasdaq',
        'Buyback, Global M2, ETF',
        'Buyback/M2/equities',
        'USDT/USDC/total supply/Mint/Burn/Treasury/Exchange',
        'major stablecoin Treasury→Exchange',
    ]
    for x in bad_active:
        if x in text:
            raise SystemExit(f'VALIDATE_FAIL removed active canonical phrase: {x}')
    print('MASTER_MARKET_FREE_RECOVERY_REMOVAL_PATCH=PASS')


if __name__ == '__main__':
    patch_canonical()
    patch_contract()
    validate()
