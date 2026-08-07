"""
Spend tracking and clean shutdown.

Running out of credit mid-wave is not a crash — it is an expected event with a
correct response: stop that engine, leave its remaining probes unrecorded so
`--resume` picks them up after a top-up, and keep the other engines running.
The failure mode this avoids is burning 60 probes' worth of wall-clock writing
error records that look like data.

Prices are USD per 1M tokens, plus a per-search fee. Kept deliberately simple —
this is a spend GUARD, not an accounting system. It over-estimates slightly by
design: better to stop early than to discover the wave died at probe 180.
"""

from __future__ import annotations

# model-prefix -> (input $/1M, output $/1M, per-search $)
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-5.6-sol":    (2.50, 15.00, 0.010),
    "gpt-5.6-terra":  (1.00,  6.00, 0.010),
    "gpt-5.6-luna":   (0.10,  0.60, 0.010),
    "claude-sonnet":  (2.00, 10.00, 0.010),
    "claude-haiku":   (1.00,  5.00, 0.010),
    "claude-opus":    (5.00, 25.00, 0.010),
    "sonar-pro":      (3.00, 15.00, 0.010),
    "sonar":          (1.00,  1.00, 0.008),
    "gemini-flash-lite": (0.10, 0.40, 0.0),
    "gemini-flash":   (1.50,  9.00, 0.0),
    "gemini":         (1.50,  9.00, 0.0),
}

# Error-text fingerprints meaning "you are out of money / entitlement", as opposed
# to "slow down". These must not be retried — no amount of waiting adds credit.
CREDIT_SIGNATURES = (
    "insufficient_quota", "insufficient quota", "exceeded your current quota",
    "billing", "credit balance is too low", "payment required", "402",
    "resource_exhausted", "quota",
)


def price_for(model: str) -> tuple[float, float, float]:
    for prefix in sorted(PRICES, key=len, reverse=True):
        if model.startswith(prefix):
            return PRICES[prefix]
    return (2.0, 10.0, 0.01)          # unknown model: assume expensive, fail safe


def estimate_cost(model: str, usage: dict, searched: bool) -> float:
    """Normalise the four providers' incompatible usage shapes into dollars."""
    pin, pout, psearch = price_for(model)
    u = usage or {}
    tin = (u.get("input_tokens") or u.get("prompt_tokens")
           or u.get("promptTokenCount") or u.get("input_token_count") or 0)
    tout = (u.get("output_tokens") or u.get("completion_tokens")
            or u.get("candidatesTokenCount") or u.get("output_token_count") or 0)
    if not tin and not tout:
        total = u.get("total_tokens") or u.get("totalTokenCount") or 0
        tin, tout = total * 0.85, total * 0.15      # search answers are input-heavy
    return (tin / 1e6) * pin + (tout / 1e6) * pout + (psearch if searched else 0.0)


def looks_like_out_of_credit(err: str | None) -> bool:
    return bool(err) and any(sig in err.lower() for sig in CREDIT_SIGNATURES)


class Budget:
    """Per-engine spend cap. Thread-safe enough: callers hold the writer lock."""

    def __init__(self, caps: dict[str, float] | None = None):
        self.caps = caps or {}
        self.spent: dict[str, float] = {}
        self.stopped: dict[str, str] = {}          # engine -> why

    def add(self, engine: str, cost: float) -> None:
        self.spent[engine] = self.spent.get(engine, 0.0) + cost
        cap = self.caps.get(engine)
        if cap and self.spent[engine] >= cap and engine not in self.stopped:
            self.stopped[engine] = f"hit the ${cap:.2f} cap (spent ${self.spent[engine]:.2f})"

    def halt(self, engine: str, why: str) -> None:
        self.stopped.setdefault(engine, why)

    def is_stopped(self, engine: str) -> bool:
        return engine in self.stopped

    def total(self) -> float:
        return sum(self.spent.values())

    def report(self) -> str:
        lines = ["", "SPEND (estimated)"]
        for e in sorted(self.spent):
            cap = self.caps.get(e)
            lines.append(f"  {e:<11} ${self.spent[e]:>6.2f}" + (f"  of ${cap:.2f} cap" if cap else ""))
        lines.append(f"  {'TOTAL':<11} ${self.total():>6.2f}")
        if self.stopped:
            lines += ["", "!! STOPPED EARLY — top up, then re-run the SAME command with --resume:"]
            for e, why in self.stopped.items():
                lines.append(f"   {e:<11} {why}")
            lines.append("   Unrun probes were not recorded, so --resume will pick them up cleanly.")
        return "\n".join(lines)
