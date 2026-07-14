"""Reliability engine (PRD 7.5).

Signals: community reports, charge session outcomes, operator API status.
Model: evidence counter with exponential time decay toward a neutral prior.

    score = (a + positive_weight) / (a + b + positive_weight + negative_weight)

with a Beta(a, b) prior (a=b=2 → neutral 0.5, resistant to single reports).
Signal weights decay with a half-life so stale evidence loses influence and
broken chargers "heal" back toward uncertainty rather than staying condemned.
"""
import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Charger, ChargerStatus, ReliabilityScore, Report, utcnow
from typing import Optional

PRIOR_A = 2.0
PRIOR_B = 2.0

SIGNAL_WEIGHTS = {
    "working": +1.0,
    "check_in": +0.5,
    "session_success": +1.5,
    "broken": -2.0,
    "session_failure": -2.0,
    "ice_blocked": -0.3,   # not the charger's fault, mild negative for usability
    "queue": 0.0,          # informational; doesn't change working-probability
}


def decay_factor(age_days: float) -> float:
    return math.pow(0.5, age_days / settings.reliability_half_life_days)


async def recompute(db: AsyncSession, charger_id: str) -> ReliabilityScore:
    """Recompute the score from the full (recent) signal history."""
    now = utcnow()
    reports = (
        await db.execute(
            select(Report).where(Report.charger_id == charger_id).order_by(Report.created_at.desc()).limit(200)
        )
    ).scalars().all()

    rel = (
        await db.execute(select(ReliabilityScore).where(ReliabilityScore.charger_id == charger_id))
    ).scalar_one_or_none()
    if rel is None:
        rel = ReliabilityScore(charger_id=charger_id)
        db.add(rel)

    pos, neg = rel.baseline_pos, rel.baseline_neg
    n_pos, n_neg = 0, 0
    last_verified = None
    for r in reports:
        w = SIGNAL_WEIGHTS.get(r.report_type, 0.0)
        age = max((now - r.created_at).total_seconds() / 86400.0, 0.0)
        dw = abs(w) * decay_factor(age)
        if w > 0:
            pos += dw
            n_pos += 1
            if last_verified is None or r.created_at > last_verified:
                last_verified = r.created_at
        elif w < 0:
            neg += dw
            n_neg += 1

    score = (PRIOR_A + pos) / (PRIOR_A + PRIOR_B + pos + neg)
    rel.score = round(score, 4)
    rel.positive_signals = n_pos
    rel.negative_signals = n_neg
    if last_verified:
        rel.last_verified_at = last_verified

    # Reflect strongly negative evidence in charger status
    charger = (await db.execute(select(Charger).where(Charger.id == charger_id))).scalar_one()
    if score < 0.3 and neg > pos:
        charger.status = ChargerStatus.BROKEN.value
    elif score >= 0.5 and charger.status == ChargerStatus.BROKEN.value:
        charger.status = ChargerStatus.ONLINE.value

    await db.commit()
    return rel


def effective_score(rel: Optional[ReliabilityScore]) -> float:
    """Score with staleness decay applied at read time (no reports → drifts to 0.5)."""
    if rel is None:
        return 0.5
    if rel.last_verified_at is None:
        return min(rel.score, 0.5)
    age = max((utcnow() - rel.last_verified_at).total_seconds() / 86400.0, 0.0)
    d = decay_factor(age)
    return round(rel.score * d + 0.5 * (1 - d), 4)
