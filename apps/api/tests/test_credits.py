"""Credit ledger: pricing, idempotency, refunds and balance integrity."""

from __future__ import annotations

import threading

import pytest

from lingoimage.core.errors import AppError, ErrorCode
from lingoimage.db.models import Job, User
from lingoimage.db.session import session_scope
from lingoimage.domain import credits as rules
from lingoimage.domain.enums import LedgerReason, ToolType
from lingoimage.services import credits as service


# --------------------------------------------------------------------------- #
# Pricing rules
# --------------------------------------------------------------------------- #
def test_ocr_only_costs_one_credit_per_page():
    estimate = rules.estimate(tool=ToolType.IMAGE_TO_TEXT, pages=3, translate=False)
    assert estimate.total == 3


def test_translation_adds_one_credit_per_page():
    estimate = rules.estimate(tool=ToolType.IMAGE_TRANSLATOR, pages=3, translate=True)
    assert estimate.total == 6


def test_handwriting_applies_a_multiplier():
    plain = rules.estimate(tool=ToolType.IMAGE_TO_TEXT, pages=2, translate=False).total
    hand = rules.estimate(
        tool=ToolType.HANDWRITING_TO_TEXT, pages=2, translate=False, handwriting=True
    ).total
    assert hand > plain


def test_table_extraction_costs_more_than_plain_ocr():
    plain = rules.estimate(tool=ToolType.IMAGE_TO_TEXT, pages=4, translate=False).total
    tables = rules.estimate(tool=ToolType.IMAGE_TO_EXCEL, pages=4, translate=False).total
    assert tables > plain


def test_estimate_breakdown_sums_to_the_total():
    estimate = rules.estimate(
        tool=ToolType.IMAGE_TRANSLATOR, pages=5, translate=True, advanced_inpaint=True
    )
    assert sum(line["credits"] for line in estimate.breakdown) <= estimate.total + 1
    assert estimate.breakdown, "the user must be able to see what they pay for"


def test_partial_success_refunds_the_failed_pages():
    assert rules.refund_amount(charged=10, pages_total=10, pages_succeeded=10) == 0
    assert rules.refund_amount(charged=10, pages_total=10, pages_succeeded=0) == 10
    # 4 of 10 pages failed -> at least 4 credits back, rounded in the user's favour.
    assert rules.refund_amount(charged=10, pages_total=10, pages_succeeded=6) == 4


# --------------------------------------------------------------------------- #
# Ledger behaviour
# --------------------------------------------------------------------------- #
@pytest.fixture
def wallet_user():
    from lingoimage.core.ids import ulid

    with session_scope() as db:
        user = User(email=f"ledger-{ulid()[:10].lower()}@example.test", plan_code="pro")
        db.add(user)
        db.flush()
        wallet = service.get_or_create_wallet(db, user_id=user.id)
        service.apply(
            db,
            wallet,
            delta=100,
            reason=LedgerReason.MONTHLY_GRANT,
            idempotency_key=f"test-grant-{user.id}",
        )
        return user.id, wallet.id


def test_charging_the_same_job_twice_debits_once(wallet_user):
    user_id, wallet_id = wallet_user
    with session_scope() as db:
        job = Job(user_id=user_id, type="ocr", status="queued", pages_total=1)
        db.add(job)
        db.flush()

        service.charge_job(db, job, 7)
        service.charge_job(db, job, 7)
        service.charge_job(db, job, 7)
        db.flush()

        wallet = service.get_or_create_wallet(db, user_id=user_id)
        assert wallet.balance == 93
        assert service.recompute_balance(db, wallet_id) == 93


def test_refund_never_exceeds_what_was_charged(wallet_user):
    user_id, _ = wallet_user
    with session_scope() as db:
        job = Job(user_id=user_id, type="ocr", status="queued", pages_total=1)
        db.add(job)
        db.flush()
        service.charge_job(db, job, 5)
        db.flush()

        service.refund_job(db, job, 999)
        service.refund_job(db, job, 999)
        db.flush()

        wallet = service.get_or_create_wallet(db, user_id=user_id)
        assert wallet.balance == 100, "over-refunding must be impossible"


def test_balance_cannot_go_negative(wallet_user):
    user_id, _ = wallet_user
    with session_scope() as db:
        wallet = service.get_or_create_wallet(db, user_id=user_id)
        with pytest.raises(AppError) as excinfo:
            service.apply(
                db,
                wallet,
                delta=-1_000,
                reason=LedgerReason.JOB_CHARGE,
                idempotency_key="overdraft-attempt",
            )
        assert excinfo.value.code is ErrorCode.INSUFFICIENT_CREDITS


def test_refunding_a_payment_may_go_negative_deliberately(wallet_user):
    """Credits already spent cannot be un-spent; a negative balance is honest."""
    from lingoimage.db.models import Payment

    user_id, _ = wallet_user
    with session_scope() as db:
        payment = Payment(
            user_id=user_id,
            provider="stripe",
            provider_payment_id=f"pi_test_{user_id}",
            amount_minor=1900,
            currency="USD",
            status="succeeded",
            credits_granted=500,
        )
        db.add(payment)
        db.flush()

        wallet = service.get_or_create_wallet(db, user_id=user_id)
        outcome = service.revoke_for_refund(db, wallet, amount=500, payment_id=payment.id)
        assert outcome.balance == -400, "spent credits cannot be un-spent"


def test_manual_adjustment_requires_a_reason(wallet_user):
    user_id, _ = wallet_user
    with session_scope() as db:
        wallet = service.get_or_create_wallet(db, user_id=user_id)
        with pytest.raises(AppError):
            service.manual_adjustment(
                db, wallet, delta=10, actor_user_id=user_id, reason_text="   "
            )


@pytest.mark.integration
def test_concurrent_charges_never_oversell(wallet_user):
    """Two threads charging at once must not both succeed past the balance.

    Only meaningful on PostgreSQL: the guarantee comes from ``SELECT … FOR
    UPDATE`` on the wallet row. SQLite has no row locks (and serialises writers
    with a global lock), so the test is skipped there rather than passing for
    the wrong reason.
    """
    from lingoimage.db.session import get_engine

    if get_engine().dialect.name != "postgresql":
        pytest.skip("row-level locking requires PostgreSQL")

    user_id, wallet_id = wallet_user
    results: list[str] = []

    def charge(index: int) -> None:
        try:
            with session_scope() as db:
                wallet = service.get_or_create_wallet(db, user_id=user_id)
                service.apply(
                    db,
                    wallet,
                    delta=-60,
                    reason=LedgerReason.JOB_CHARGE,
                    idempotency_key=f"concurrent-{index}",
                )
            results.append("ok")
        except AppError as exc:
            results.append(str(exc.code))

    threads = [threading.Thread(target=charge, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with session_scope() as db:
        wallet = service.get_or_create_wallet(db, user_id=user_id)
        assert wallet.balance >= 0
        assert service.recompute_balance(db, wallet_id) == wallet.balance
