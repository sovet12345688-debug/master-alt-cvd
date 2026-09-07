from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "r13_frozen_config.json"
MANIFEST_PATH = ROOT / "r13_freeze_manifest.json"


class ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class EntryDecision:
    action: str
    route: str | None
    risk_state: str
    entry: float | None
    stop: float | None
    target: float | None
    risk: float | None
    quality_score: float | None
    independent_reasons: int | None
    safety_score: float | None
    reason: str


def _finite(v: Any) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _f(row: Mapping[str, Any], key: str) -> float:
    v = row.get(key)
    if not _finite(v):
        raise ContractError(f"Missing/non-finite required field: {key}")
    return float(v)


def _b(row: Mapping[str, Any], key: str, default: bool = False) -> bool:
    return bool(row.get(key, default))


def _close_loc(row: Mapping[str, Any]) -> float:
    lo, hi, close = _f(row, "low"), _f(row, "high"), _f(row, "close")
    if hi <= lo:
        return 0.5
    return (close - lo) / (hi - lo)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_frozen_contract() -> tuple[dict[str, Any], dict[str, Any]]:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    actual = _sha256(CONFIG_PATH)
    expected = manifest["canonical_config_sha256"]
    if actual != expected:
        raise ContractError(f"Frozen config SHA mismatch: {actual} != {expected}")
    if cfg.get("model") != "MASTER_BTC_TREND_V3_R1_3":
        raise ContractError("Wrong model in frozen config")
    if cfg.get("status") != "RESEARCH_BASELINE_FROZEN_PRE_OOS":
        raise ContractError("R1.3 is not in frozen pre-OOS status")
    return cfg, manifest


class R13Engine:
    """Deterministic execution layer for the frozen MASTER BTC TREND V3 R1.3 contract.

    This module does not download or replay historical data. It consumes already-closed
    daily/1H/4H features and applies only the frozen execution contract.
    """

    def __init__(self) -> None:
        self.cfg, self.manifest = load_frozen_contract()
        self.retest = self.cfg["entry_routes"]["RETEST"]
        self.ignition = self.cfg["entry_routes"]["IGNITION"]

    # ---------- daily permission / risk ----------
    def execution_eligible(self, engine: str, stage: int) -> bool:
        if engine not in {"LR", "LC", "SR", "SC"}:
            raise ContractError(f"Unknown engine: {engine}")
        stage = int(stage)
        if engine in {"LC", "SC"}:
            return stage >= 2
        return stage >= 1

    def risk_state(self, daily: Mapping[str, Any]) -> str:
        if _b(daily, "TRANSITION_CONFLICT") or _b(daily, "severe_risk_veto"):
            return "BLOCK"
        caution = any(
            _b(daily, k)
            for k in (
                "FALSE_BOTTOM_RISK",
                "FALSE_TOP_RISK",
                "long_overextended",
                "short_overextended",
            )
        )
        return "CAUTION" if caution else "OPEN"

    def route_allowed(self, route: str, state: str) -> bool:
        if route not in {"RETEST", "IGNITION"}:
            raise ContractError(f"Unknown route: {route}")
        return state in set(self.cfg["entry_routes"][route]["allowed_risk_states"])

    # ---------- inherited R1.2 definitions ----------
    # Directional candle and taker sign semantics intentionally inherit Step6 R1.2.
    def directional_candle(self, row: Mapping[str, Any], direction: str) -> bool:
        o, c, loc = _f(row, "open"), _f(row, "close"), _close_loc(row)
        if direction == "long":
            return c > o and loc >= 0.60
        return c < o and loc <= 0.40

    def ema_alignment(self, row: Mapping[str, Any], direction: str) -> bool:
        c, ema = _f(row, "close"), _f(row, "ema20")
        return c >= ema if direction == "long" else c <= ema

    def taker_directional(self, row: Mapping[str, Any], direction: str) -> bool:
        t = _f(row, "taker_imb")
        return t >= 0.0 if direction == "long" else t <= 0.0

    def structure_break_prior3(self, row: Mapping[str, Any], direction: str) -> bool:
        c = _f(row, "close")
        if direction == "long":
            return c > _f(row, "prior_high3")
        return c < _f(row, "prior_low3")

    def daily_transition_alignment(self, daily: Mapping[str, Any], direction: str) -> bool:
        return _b(daily, "up_transition") if direction == "long" else _b(daily, "down_transition")

    # ---------- RETEST route ----------
    def zone_touch(self, row: Mapping[str, Any], zone_lo: float, zone_hi: float) -> bool:
        return _f(row, "low") <= float(zone_hi) and _f(row, "high") >= float(zone_lo)

    def retest_reaction(self, row: Mapping[str, Any], direction: str, zone_lo: float, zone_hi: float) -> tuple[float, int]:
        c, o, loc = _f(row, "close"), _f(row, "open"), _close_loc(row)
        vol = _f(row, "vol_ratio20") >= 1.0
        taker = self.taker_directional(row, direction)
        ema = self.ema_alignment(row, direction)
        if direction == "long":
            defense = c >= zone_lo
            directional = c > o and loc >= 0.60
            reclaim = c >= zone_hi
            structure = self.structure_break_prior3(row, direction)
        else:
            defense = c <= zone_hi
            directional = c < o and loc <= 0.40
            reclaim = c <= zone_lo
            structure = self.structure_break_prior3(row, direction)
        score = 20 * defense + 20 * directional + 20 * reclaim + 15 * ema + 10 * vol + 10 * taker + 5 * structure
        reasons = int(directional) + int(reclaim) + int(ema) + int(vol or taker) + int(structure)
        return float(score), int(reasons)

    def retest_persistence(self, nxt: Mapping[str, Any], direction: str, zone_lo: float, zone_hi: float, daily_atr: float) -> bool:
        # Inherited R1.2 Step6 persistence buffer: 0.25 Daily ATR.
        buffer_atr = 0.25
        if direction == "long":
            return _f(nxt, "low") >= zone_lo - buffer_atr * daily_atr and _f(nxt, "close") >= zone_lo
        return _f(nxt, "high") <= zone_hi + buffer_atr * daily_atr and _f(nxt, "close") <= zone_hi

    def retest_stop(self, entry: float, direction: str, zone_lo: float, zone_hi: float, daily_atr: float) -> tuple[float, float, bool]:
        if direction == "long":
            stop = min(zone_lo, entry - 0.45 * daily_atr) - 0.35 * daily_atr
            risk = entry - stop
        else:
            stop = max(zone_hi, entry + 0.45 * daily_atr) + 0.35 * daily_atr
            risk = stop - entry
        valid = _finite(risk) and risk > 0 and risk / entry <= float(self.retest["max_stop_distance_pct"])
        return float(stop), float(risk), bool(valid)

    # ---------- IGNITION route ----------
    def ignition_quality(self, row: Mapping[str, Any], daily: Mapping[str, Any], direction: str) -> tuple[float, int, dict[str, bool]]:
        w = self.ignition["ignition_quality_score"]
        components = {
            "structure_break_prior3": self.structure_break_prior3(row, direction),
            "directional_candle_quality": self.directional_candle(row, direction),
            "ema20_alignment": self.ema_alignment(row, direction),
            "volume_ratio20_ge_1": _f(row, "vol_ratio20") >= 1.0,
            "taker_imbalance_directional": self.taker_directional(row, direction),
            "daily_transition_alignment": self.daily_transition_alignment(daily, direction),
        }
        score = sum(float(w[k]) for k, passed in components.items() if passed)
        reasons = sum(int(v) for v in components.values())
        return float(score), int(reasons), components

    def ignition_persistence(self, candidate: Mapping[str, Any], nxt: Mapping[str, Any], direction: str) -> bool:
        h1_atr = _f(candidate, "atr14_1h")
        buf = float(self.ignition["breakout_persistence_buffer_h1_atr"]) * h1_atr
        if direction == "long":
            ref = _f(candidate, "prior_high3")
            return _f(nxt, "low") >= ref - buf and _f(nxt, "close") >= ref
        ref = _f(candidate, "prior_low3")
        return _f(nxt, "high") <= ref + buf and _f(nxt, "close") <= ref

    def ignition_stop(self, entry: float, candidate: Mapping[str, Any], direction: str) -> tuple[float, float, bool]:
        h1_atr = _f(candidate, "atr14_1h")
        buffer_atr = 0.25 * h1_atr
        if direction == "long":
            stop = _f(candidate, "prior_low3") - buffer_atr
            risk = entry - stop
        else:
            stop = _f(candidate, "prior_high3") + buffer_atr
            risk = stop - entry
        valid = _finite(risk) and risk > 0 and risk / entry <= float(self.ignition["max_stop_distance_pct"])
        return float(stop), float(risk), bool(valid)

    # ---------- common hard gates ----------
    def nonchase(self, entry: float, direction: str, zone_lo: float, zone_hi: float, daily_atr: float, route: str) -> bool:
        lim = float(self.cfg["entry_routes"][route]["nonchase_daily_atr_max"])
        if direction == "long":
            return entry <= zone_hi + lim * daily_atr
        return entry >= zone_lo - lim * daily_atr

    def safety_score(self, route_risk_allowed: bool, conflict: bool, stop_valid: bool, nonchase: bool, complete_bar: bool = True) -> float:
        # R1.2 Step6 formula inherited; `route_risk_allowed` replaces binary daily new_risk_ok.
        return float(25 * route_risk_allowed + 20 * (not conflict) + 20 * stop_valid + 20 * nonchase + 15 * complete_bar)

    def _target(self, entry: float, direction: str, risk: float, route: str) -> float:
        rr = float(self.cfg["entry_routes"][route]["fixed_rr"])
        return entry + rr * risk if direction == "long" else entry - rr * risk

    def evaluate_pair(
        self,
        *,
        engine: str,
        stage: int,
        direction: str,
        daily: Mapping[str, Any],
        candidate: Mapping[str, Any],
        nxt: Mapping[str, Any],
        zone_lo: float,
        zone_hi: float,
        daily_atr: float,
    ) -> EntryDecision:
        if direction not in {"long", "short"}:
            raise ContractError("direction must be long or short")
        if not self.execution_eligible(engine, stage):
            return EntryDecision("WATCH", None, self.risk_state(daily), None, None, None, None, None, None, None, "ENGINE_STAGE_WATCH_ONLY")

        state = self.risk_state(daily)
        if state == "BLOCK":
            return EntryDecision("WAIT", None, state, None, None, None, None, None, None, None, "RISK_STATE_BLOCK")

        touched = self.zone_touch(candidate, zone_lo, zone_hi)

        # Deterministic route precedence: a touched causal zone is RETEST territory.
        if touched and self.route_allowed("RETEST", state):
            score, reasons = self.retest_reaction(candidate, direction, zone_lo, zone_hi)
            if score >= float(self.retest["reaction_score_min"]) and reasons >= int(self.retest["independent_reasons_min"]):
                if self.retest_persistence(nxt, direction, zone_lo, zone_hi, daily_atr):
                    entry = _f(nxt, "close")
                    stop, risk, stop_ok = self.retest_stop(entry, direction, zone_lo, zone_hi, daily_atr)
                    nc = self.nonchase(entry, direction, zone_lo, zone_hi, daily_atr, "RETEST")
                    safety = self.safety_score(True, _b(daily, "TRANSITION_CONFLICT"), stop_ok, nc, True)
                    if stop_ok and nc and safety >= float(self.retest["safety_score_min"]):
                        return EntryDecision("ENTER", "RETEST", state, entry, stop, self._target(entry, direction, risk, "RETEST"), risk, score, reasons, safety, "RETEST_PASS")
            # If the causal zone was touched but RETEST failed, do not relabel the same event as IGNITION.
            return EntryDecision("WAIT", "RETEST", state, None, None, None, None, score, reasons, None, "RETEST_FAILED")

        if self.route_allowed("IGNITION", state):
            score, reasons, _ = self.ignition_quality(candidate, daily, direction)
            if score >= float(self.ignition["ignition_quality_min"]) and reasons >= int(self.ignition["independent_reasons_min"]):
                if self.ignition_persistence(candidate, nxt, direction):
                    entry = _f(nxt, "close")
                    stop, risk, stop_ok = self.ignition_stop(entry, candidate, direction)
                    nc = self.nonchase(entry, direction, zone_lo, zone_hi, daily_atr, "IGNITION")
                    safety = self.safety_score(True, _b(daily, "TRANSITION_CONFLICT"), stop_ok, nc, True)
                    if stop_ok and nc and safety >= float(self.ignition["safety_score_min"]):
                        return EntryDecision("ENTER", "IGNITION", state, entry, stop, self._target(entry, direction, risk, "IGNITION"), risk, score, reasons, safety, "IGNITION_PASS")
            return EntryDecision("WAIT", "IGNITION", state, None, None, None, None, score, reasons, None, "IGNITION_FAILED")

        return EntryDecision("WAIT", None, state, None, None, None, None, None, None, None, "NO_ALLOWED_ROUTE")

    def resolve_fixed_rr_leg(self, bars: Sequence[Mapping[str, Any]], direction: str, stop: float, target: float) -> tuple[str, float | None]:
        for bar in bars:
            if direction == "long":
                tp = _f(bar, "high") >= target
                sl = _f(bar, "low") <= stop
            else:
                tp = _f(bar, "low") <= target
                sl = _f(bar, "high") >= stop
            if tp and sl:
                return "AMBIGUOUS_SAME_BAR", None
            if tp:
                return "TP_3R", 3.0
            if sl:
                return "SL_1R", -1.0
        return "CENSORED_UNRESOLVED", None

    def state_machine_contract(self) -> dict[str, Any]:
        return {
            "states": list(self.cfg["state_machine"]),
            "four_hour_add": dict(self.cfg["four_hour_add"]),
            "one_day_allocation": "LOG_ONLY_NO_THIRD_LEG",
        }


if __name__ == "__main__":
    e = R13Engine()
    print(json.dumps({
        "model": e.cfg["model"],
        "status": "IMPLEMENTATION_LOAD_PASS",
        "config_sha256": _sha256(CONFIG_PATH),
        "oos_replay_performed": False,
    }, ensure_ascii=False, indent=2))
