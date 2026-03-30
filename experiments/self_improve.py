"""Self-improve module — strategy router and performance memory."""

import json, logging
from pathlib import Path
from typing import Optional
from collections import defaultdict

from core.evaluator import Evaluator, StrategyScorecard

log = logging.getLogger(__name__)

STATE_PATH = Path("experiments/strategy_state.json")


def load() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {
        "allocations": {},     # strategy -> allocation weight
        "rounds_no_improve": {},  # strategy -> count of rounds without improvement
        "variant_of": {},       # strategy -> parent strategy name
        "archived": [],         # list of archived strategy names
        "suggestions": [],      # proposed new strategy variants
    }


def save(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


class StrategyRouter:
    """
    Decides which strategies to run based on performance.

    Rules:
    - If strategy Sharpe < 0 for 20+ trades: reduce allocation by 50%
    - If strategy Sharpe > 2.0 for 10+ trades: increase allocation
    - If win_rate < 40% over 30 trades: reduce threshold
    - If a strategy is 3x worse than best over 20 rounds: disable temporarily
    - If all strategies performing poorly: suggest trying a variant

    Allocation weights are normalized and used to control how often
    each strategy's signals are acted on.
    """

    def __init__(self, evaluator: Evaluator):
        self.evaluator = evaluator
        self.state = load()
        self._alloc_cache = {}

    def compute_allocations(self) -> dict[str, float]:
        """Compute normalized allocation weights for each active strategy."""
        scorecards = self.evaluator.scorecards
        if not scorecards:
            return {}

        active = {k: v for k, v in scorecards.items()
                   if v.status == "active"}
        if not active:
            return {}

        # Base weight from Sharpe (clipped to avoid negative weights)
        raw = {}
        for name, sc in active.items():
            # Sharpe contribution
            sharpe_w = max(0, sc.sharpe) if sc.sharpe != 0 else 0.1
            # Win rate contribution
            wr_w = sc.win_rate if sc.total_trades > 0 else 0.5
            # Signal rate contribution (prefer strategies that actually signal)
            sig_w = min(sc.signal_rate / 0.05, 1.0) if sc.signal_rate > 0 else 0.01

            raw[name] = sharpe_w * 0.5 + wr_w * 0.3 + sig_w * 0.2

        # Normalize
        total = sum(raw.values())
        if total > 0:
            self._alloc_cache = {k: v / total for k, v in raw.items()}
        else:
            self._alloc_cache = {k: 1.0 / len(raw) for k in raw}

        return self._alloc_cache

    def should_act(self, strategy: str, confidence: float) -> bool:
        """Decide whether to act on a signal given current allocations."""
        alloc = self._alloc_cache.get(strategy, 0.1)
        # Higher confidence + higher allocation = more likely to act
        threshold = 0.3 / (alloc + 0.01)  # inverse relationship
        return confidence >= max(0.5, threshold)

    def apply_self_improve(self):
        """Apply self-improve rules to strategy state."""
        state = self.state
        scorecards = self.evaluator.scorecards

        for name, sc in scorecards.items():
            if sc.status == "archived":
                if name not in state["archived"]:
                    state["archived"].append(name)
                continue

            # Rule 1: Sharpe < 0 for 50+ trades → archive
            if sc.sharpe < 0 and sc.total_trades >= 50:
                self._archive(name, "sharpe negative for 50+ trades", sc)
                continue

            # Rule 2: Win rate < 40% over 30 trades → pause
            if sc.win_rate < 0.40 and sc.total_trades >= 30:
                self._pause(name, f"win_rate {sc.win_rate:.1%} < 40%", sc)
                continue

            # Rule 3: Drawdown > 25% → pause
            if sc.max_drawdown < -0.25:
                self._pause(name, f"drawdown {sc.max_drawdown:.1%} < -25%", sc)
                continue

            # Rule 4: Sharpe > 2.0 for 10+ trades → increase allocation
            if sc.sharpe > 2.0 and sc.total_trades >= 10:
                current = state["allocations"].get(name, 1.0)
                state["allocations"][name] = min(current * 1.5, 3.0)
                log.info("[self-improve] %s sharpe=%.2f — increased allocation to %.2f",
                         name, sc.sharpe, state["allocations"][name])

            # Rule 5: Strategy 3x worse than best for 20 rounds → disable
            best = self.evaluator.get_best_strategy()
            if best and name != best.strategy:
                rounds = state["rounds_no_improve"].get(name, 0) + 1
                state["rounds_no_improve"][name] = rounds
                if rounds >= 20 and sc.sharpe < best.sharpe / 3:
                    self._pause(name, f"3x worse than {best.strategy} for 20 rounds", sc)
            else:
                state["rounds_no_improve"][name] = 0

        # Rule 6: Generate variant suggestion if all strategies performing poorly
        active_sharpe = [sc.sharpe for sc in scorecards.values() if sc.status == "active"]
        if active_sharpe and max(active_sharpe) < 0.5:
            self._suggest_variant(scorecards)

        save(state)

    def _archive(self, name: str, reason: str, sc: StrategyScorecard):
        log.warning("[self-improve] ARCHIVING %s: %s (sharpe=%.2f, trades=%d)",
                   name, reason, sc.sharpe, sc.total_trades)
        sc.status = "archived"
        sc.pause_reason = reason
        self.state["archived"].append(name)

    def _pause(self, name: str, reason: str, sc: StrategyScorecard):
        log.warning("[self-improve] PAUSING %s: %s", name, reason)
        sc.status = "paused"
        sc.pause_reason = reason

    def _suggest_variant(self, scorecards: dict[str, StrategyScorecard]):
        suggestions = []
        for name, sc in scorecards.items():
            if sc.status == "archived":
                # Try a variant with stricter thresholds
                suggestions.append({
                    "type": "variant",
                    "parent": name,
                    "change": "increase_min_edge by 0.02",
                    "reason": f"parent archived due to {sc.pause_reason}",
                })
        if suggestions and len(suggestions) > len(self.state["suggestions"]):
            self.state["suggestions"] = suggestions[-5:]  # keep last 5
            log.info("[self-improve] New variant suggestions: %s", suggestions)
