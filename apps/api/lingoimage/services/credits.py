"""Credit wallet and append-only ledger.

Correctness rules, all enforced here rather than by convention at call sites:

* Every mutation writes a ledger row and updates the wallet **in one
  transaction**, with the wallet row locked ``FOR UPDATE`` first.
* Every mutation carries an idempotency key with a unique index behind it. A
  retried task, a duplicated webhook or a double-clicked button can therefore
  never charge twice — the second attempt finds the existing row and returns it.
* The balance can never go negative: the check happens inside the same lock.
* Refunds are capped at what was actually charged for that job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.core.logging import get_logger
from lingoimage.core.metrics import credits_consumed_total, credits_refunded_total
from lingoimage.db.models import CreditLedgerEntry, CreditWallet, Job, User, Workspace
from lingoimage.domain.enums import LedgerReason

log = get_logger(__name__)


@dataclass(slots=True)
class LedgerOutcome:
    entry: CreditLedgerEntry
    balance: int
    duplicate: bool = False


def get_or_create_wallet(
    session: Session, *, user_id: str | None = None, workspace_id: str | None = None
) -> CreditWallet:
    if not (user_id or workspace_id):
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED, internal="a wallet needs a user or a workspace"
        )
    statement = select(CreditWallet)
    statement = (
        statement.where(CreditWallet.workspace_id == workspace_id)
        if workspace_id
        else statement.where(CreditWallet.user_id == user_id)
    )
    wallet = session.execute(statement).scalar_one_or_none()
    if wallet is not None:
        return wallet

    wallet = CreditWallet(user_id=None if workspace_id else user_id, workspace_id=workspace_id)
    session.add(wallet)
    try:
        session.flush()
    except IntegrityError:
        # Lost a race with a concurrent request — take the row that won.
        session.rollback()
        wallet = session.execute(statement).scalar_one()
    return wallet


def _lock_wallet(session: Session, wallet_id: str) -> CreditWallet:
    """Serialise concurrent mutations of one wallet."""
    statement = select(CreditWallet).where(CreditWallet.id == wallet_id)
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        statement = statement.with_for_update()
    wallet = session.execute(statement).scalar_one_or_none()
    if wallet is None:
        raise AppError(code=ErrorCode.NOT_FOUND, internal=f"wallet {wallet_id} disappeared")
    return wallet


def balance_of(session: Session, wallet: CreditWallet) -> int:
    return int(wallet.balance)


def recompute_balance(session: Session, wallet_id: str) -> int:
    """Sum the ledger — used by the admin panel to prove the wallet is consistent."""
    total = session.execute(
        select(func.coalesce(func.sum(CreditLedgerEntry.delta), 0)).where(
            CreditLedgerEntry.wallet_id == wallet_id
        )
    ).scalar_one()
    return int(total)


def apply(
    session: Session,
    wallet: CreditWallet,
    *,
    delta: int,
    reason: LedgerReason | str,
    idempotency_key: str,
    job_id: str | None = None,
    payment_id: str | None = None,
    actor_user_id: str | None = None,
    note: str | None = None,
    metadata: dict[str, Any] | None = None,
    allow_negative: bool = False,
) -> LedgerOutcome:
    """Move ``delta`` credits. Positive grants, negative charges."""
    if delta == 0:
        raise AppError(code=ErrorCode.VALIDATION_FAILED, internal="ledger delta must not be zero")

    existing = session.execute(
        select(CreditLedgerEntry).where(CreditLedgerEntry.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        log.info("credits.idempotent_replay", key=idempotency_key[:40], delta=existing.delta)
        return LedgerOutcome(entry=existing, balance=int(existing.balance_after), duplicate=True)

    locked = _lock_wallet(session, wallet.id)
    new_balance = int(locked.balance) + delta
    if new_balance < 0 and not allow_negative:
        raise AppError(
            code=ErrorCode.INSUFFICIENT_CREDITS,
            details={"required": abs(delta), "available": int(locked.balance)},
        )

    entry = CreditLedgerEntry(
        wallet_id=locked.id,
        delta=delta,
        balance_after=new_balance,
        reason=str(reason),
        job_id=job_id,
        payment_id=payment_id,
        actor_user_id=actor_user_id,
        note=note,
        idempotency_key=idempotency_key,
        metadata_json=metadata or {},
    )
    session.add(entry)
    locked.balance = new_balance
    if delta > 0:
        locked.lifetime_granted = int(locked.lifetime_granted) + delta
    else:
        locked.lifetime_spent = int(locked.lifetime_spent) - delta

    try:
        session.flush()
    except IntegrityError:
        # Either another transaction inserted the same idempotency key between
        # our check and this flush (benign — return the winner), or the entry
        # references something that does not exist (a real bug: re-raise).
        session.rollback()
        duplicate = session.execute(
            select(CreditLedgerEntry).where(CreditLedgerEntry.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if duplicate is None:
            raise
        return LedgerOutcome(entry=duplicate, balance=int(duplicate.balance_after), duplicate=True)

    if delta < 0:
        credits_consumed_total.labels(reason=str(reason)).inc(-delta)
    elif str(reason) == LedgerReason.JOB_REFUND:
        credits_refunded_total.labels(reason=str(reason)).inc(delta)

    log.info(
        "credits.applied",
        wallet=locked.id,
        delta=delta,
        balance=new_balance,
        reason=str(reason),
    )
    return LedgerOutcome(entry=entry, balance=new_balance)


# --------------------------------------------------------------------------- #
# Job charging
# --------------------------------------------------------------------------- #
def charge_job(session: Session, job: Job, amount: int) -> LedgerOutcome | None:
    """Debit a job once. Safe to call again on retry — it will not double charge."""
    if amount <= 0:
        return None
    wallet = wallet_for_job(session, job)
    outcome = apply(
        session,
        wallet,
        delta=-amount,
        reason=LedgerReason.JOB_CHARGE,
        idempotency_key=f"job:{job.id}:charge",
        job_id=job.id,
        note=f"{job.type} · {job.pages_total} page(s)",
        metadata={"job_type": job.type, "pages": job.pages_total},
    )
    if not outcome.duplicate:
        job.credits_charged = amount
    return outcome


def refund_job(
    session: Session,
    job: Job,
    amount: int | None = None,
    *,
    reason: LedgerReason = LedgerReason.JOB_REFUND,
    note: str | None = None,
    actor_user_id: str | None = None,
) -> LedgerOutcome | None:
    """Refund up to what the job was charged. Idempotent per (job, reason)."""
    charged = int(job.credits_charged or 0)
    already = int(job.credits_refunded or 0)
    refundable = max(0, charged - already)
    amount = refundable if amount is None else min(amount, refundable)
    if amount <= 0:
        return None

    wallet = wallet_for_job(session, job)
    outcome = apply(
        session,
        wallet,
        delta=amount,
        reason=reason,
        idempotency_key=f"job:{job.id}:refund:{already + amount}",
        job_id=job.id,
        actor_user_id=actor_user_id,
        note=note or f"refund for {job.error_code or 'incomplete job'}",
        metadata={"job_type": job.type, "original_charge": charged},
    )
    if not outcome.duplicate:
        job.credits_refunded = already + amount
    return outcome


def wallet_for_job(session: Session, job: Job) -> CreditWallet:
    if job.workspace_id:
        return get_or_create_wallet(session, workspace_id=job.workspace_id)
    if job.user_id:
        return get_or_create_wallet(session, user_id=job.user_id)
    raise AppError(code=ErrorCode.VALIDATION_FAILED, internal=f"job {job.id} has no billable owner")


def ensure_balance(session: Session, wallet: CreditWallet, required: int) -> None:
    """Pre-flight check so the UI can quote a price before work starts."""
    if required <= 0:
        return
    if int(wallet.balance) < required:
        raise AppError(
            code=ErrorCode.INSUFFICIENT_CREDITS,
            details={"required": required, "available": int(wallet.balance)},
        )


# --------------------------------------------------------------------------- #
# Grants
# --------------------------------------------------------------------------- #
def grant_signup_bonus(session: Session, user: User, amount: int) -> LedgerOutcome | None:
    if amount <= 0:
        return None
    wallet = get_or_create_wallet(session, user_id=user.id)
    return apply(
        session,
        wallet,
        delta=amount,
        reason=LedgerReason.SIGNUP_GRANT,
        idempotency_key=f"user:{user.id}:signup",
        note="welcome credits",
    )


def grant_monthly(
    session: Session,
    *,
    user: User | None = None,
    workspace: Workspace | None = None,
    amount: int,
    period: str | None = None,
) -> LedgerOutcome | None:
    """Idempotent per calendar month, so a re-run of the scheduler is harmless."""
    if amount <= 0:
        return None
    period = period or datetime.now(UTC).strftime("%Y-%m")
    if workspace is not None:
        wallet = get_or_create_wallet(session, workspace_id=workspace.id)
        key = f"workspace:{workspace.id}:monthly:{period}"
    elif user is not None:
        wallet = get_or_create_wallet(session, user_id=user.id)
        key = f"user:{user.id}:monthly:{period}"
    else:
        raise AppError(code=ErrorCode.VALIDATION_FAILED, internal="grant needs a target")

    outcome = apply(
        session,
        wallet,
        delta=amount,
        reason=LedgerReason.MONTHLY_GRANT,
        idempotency_key=key,
        note=f"monthly allowance {period}",
    )
    if not outcome.duplicate:
        wallet.monthly_grant_at = datetime.now(UTC)
    return outcome


def grant_purchase(
    session: Session,
    wallet: CreditWallet,
    *,
    amount: int,
    payment_id: str,
    note: str | None = None,
) -> LedgerOutcome:
    return apply(
        session,
        wallet,
        delta=amount,
        reason=LedgerReason.PURCHASE,
        idempotency_key=f"payment:{payment_id}:credits",
        payment_id=payment_id,
        note=note or "credit pack",
    )


def revoke_for_refund(
    session: Session,
    wallet: CreditWallet,
    *,
    amount: int,
    payment_id: str,
    actor_user_id: str | None = None,
) -> LedgerOutcome:
    """Money was refunded, so the granted credits come back out.

    Allowed to go negative: the user may already have spent them, and a
    negative balance is more honest than silently absorbing the loss.
    """
    return apply(
        session,
        wallet,
        delta=-abs(amount),
        reason=LedgerReason.REFUND,
        idempotency_key=f"payment:{payment_id}:revoke",
        payment_id=payment_id,
        actor_user_id=actor_user_id,
        note="credits withdrawn after payment refund",
        allow_negative=True,
    )


def manual_adjustment(
    session: Session,
    wallet: CreditWallet,
    *,
    delta: int,
    actor_user_id: str,
    reason_text: str,
) -> LedgerOutcome:
    """Support tooling. A reason is mandatory and lands in the audit log."""
    if not reason_text.strip():
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED,
            details={"field": "reason"},
            internal="manual adjustments require a reason",
        )
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return apply(
        session,
        wallet,
        delta=delta,
        reason=LedgerReason.MANUAL_ADJUSTMENT,
        idempotency_key=f"manual:{wallet.id}:{stamp}",
        actor_user_id=actor_user_id,
        note=reason_text[:255],
        allow_negative=True,
    )


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def history(
    session: Session, wallet_id: str, *, limit: int = 50, offset: int = 0
) -> list[CreditLedgerEntry]:
    return list(
        session.execute(
            select(CreditLedgerEntry)
            .where(CreditLedgerEntry.wallet_id == wallet_id)
            .order_by(CreditLedgerEntry.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).scalars()
    )


def usage_summary(session: Session, wallet_id: str, *, days: int = 30) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(
        select(
            CreditLedgerEntry.reason,
            func.sum(CreditLedgerEntry.delta),
            func.count(CreditLedgerEntry.id),
        )
        .where(CreditLedgerEntry.wallet_id == wallet_id, CreditLedgerEntry.created_at >= since)
        .group_by(CreditLedgerEntry.reason)
    ).all()

    spent = sum(-int(total) for _reason, total, _count in rows if int(total) < 0)
    granted = sum(int(total) for _reason, total, _count in rows if int(total) > 0)
    return {
        "period_days": days,
        "spent": spent,
        "granted": granted,
        "by_reason": [
            {"reason": reason, "delta": int(total), "entries": int(count)}
            for reason, total, count in rows
        ],
    }
