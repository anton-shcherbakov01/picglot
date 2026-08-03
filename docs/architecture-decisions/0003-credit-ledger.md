# 3. An append-only credit ledger, not a mutable balance

**Status:** accepted · 2026-08-02

## Context

Billing needs to survive retried Celery tasks, duplicated payment webhooks,
double-clicked buttons and concurrent jobs. The naive `UPDATE wallets SET
balance = balance - n` cannot answer "why is my balance 3?" and cannot be made
safely idempotent.

## Decision

An append-only `credit_ledger_entries` table is the source of truth. Every row
carries `delta`, `balance_after`, a reason, optional job/payment references, and
a **unique** `idempotency_key`. `credit_wallets.balance` is a maintained running
sum, updated in the same transaction under a `SELECT … FOR UPDATE` on the wallet
row.

Keys are derived from what the operation _is_, not from when it happened:

```
job:{job_id}:charge
job:{job_id}:refund:{cumulative_amount}
payment:{payment_id}:credits
user:{user_id}:monthly:{YYYY-MM}
```

A repeat therefore finds the existing row and returns it unchanged.

## Consequences

- Charging the same job three times debits once. Verified by test.
- A replayed Stripe webhook grants credits once. Verified by construction and by
  the payment-processing path.
- `recompute_balance()` sums the ledger; the admin panel shows it next to the
  wallet, so a divergence is visible rather than silent.
- Refunds are capped at what was actually charged for that job.
- Ordinary spending cannot go below zero. A **clawback after a chargeback** may,
  deliberately: the credits were already spent, and a negative balance is more
  honest than absorbing the loss. The database keeps a sanity bound
  (`balance > -1000000`) so a runaway bug still trips.
- Storage grows with every transaction. At the expected volume this is
  negligible, and the audit value is worth it.
