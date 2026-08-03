"use client";

import { useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";
import { formatDate, formatMoney, formatNumber } from "@/lib/format";

import { ErrorNote, Notice, type SectionProps } from "./AccountPanel";

interface Plan {
  code: string;
  name: string;
  monthly_credits: number;
  price_usd_cents: number;
  price_rub_kopecks: number;
}

interface Subscription {
  id: string;
  plan_code: string;
  status: string;
  provider: string;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
}

interface Payment {
  id: string;
  amount_minor: number;
  currency: string;
  status: string;
  kind: string;
  credits_granted: number;
  invoice_url: string | null;
  created_at: string;
}

export function AccountBilling({ locale, messages, onError }: SectionProps) {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => {
    Promise.all([
      apiFetch<{ plans: Plan[] } | Plan[]>("/api/v1/billing/plans"),
      apiFetch<Subscription | null>("/api/v1/billing/subscription"),
      apiFetch<Payment[]>("/api/v1/billing/payments"),
    ])
      .then(([planData, sub, paymentData]) => {
        setPlans(Array.isArray(planData) ? planData : (planData?.plans ?? []));
        setSubscription(sub);
        setPayments(paymentData);
        setError(null);
      })
      .catch((failure) => {
        if (onError(failure)) return;
        setError((failure as ApiError).message);
      });
  };

  useEffect(load, [onError]);

  const checkout = async (planCode: string) => {
    setBusy(true);
    setError(null);
    try {
      const result = await apiFetch<{ url?: string | null }>(
        "/api/v1/billing/checkout",
        {
          method: "POST",
          json: { kind: "subscription", plan_code: planCode },
        },
      );
      // The provider hosts the payment page; we never collect card details.
      if (result.url) window.location.href = result.url;
      else setError(messages.errors.internal_error);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!window.confirm(`${messages.common.confirm}?`)) return;
    setBusy(true);
    try {
      const updated = await apiFetch<Subscription>(
        "/api/v1/billing/subscription/cancel",
        {
          method: "POST",
        },
      );
      setSubscription(updated);
      setNotice(messages.editor.saved);
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-4">
      {error && <ErrorNote message={error} />}
      {notice && <Notice message={notice} />}

      <div className="card p-5">
        <h2 className="text-sm font-semibold">{messages.pricing.current}</h2>
        {subscription ? (
          <dl className="mt-3 grid gap-3 sm:grid-cols-4">
            <div>
              <dt className="text-xs uppercase text-muted">Plan</dt>
              <dd className="text-sm font-medium">{subscription.plan_code}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-muted">Status</dt>
              <dd className="text-sm">{subscription.status}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-muted">Provider</dt>
              <dd className="text-sm">{subscription.provider}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase text-muted">Renews</dt>
              <dd className="text-sm">
                {subscription.current_period_end
                  ? formatDate(subscription.current_period_end, locale)
                  : "—"}
              </dd>
            </div>
          </dl>
        ) : (
          <p className="mt-2 text-sm text-muted">{messages.pricing.free}</p>
        )}

        {subscription && !subscription.cancel_at_period_end && (
          <button
            type="button"
            className="btn-secondary mt-4 text-xs"
            disabled={busy}
            onClick={cancel}
          >
            {messages.common.cancel}
          </button>
        )}
        {subscription?.cancel_at_period_end && (
          <p className="mt-3 text-xs text-warn">
            {messages.common.confirm}:{" "}
            {formatDate(subscription.current_period_end ?? "", locale)}
          </p>
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {plans.map((plan) => {
          const current = subscription?.plan_code === plan.code;
          return (
            <div key={plan.code} className="card p-5">
              <h3 className="font-semibold">{plan.name}</h3>
              <p className="mt-1 text-sm text-muted">
                {formatNumber(plan.monthly_credits, locale)} ·{" "}
                {messages.pricing.monthly}
              </p>
              <p className="mt-2 text-lg font-semibold">
                {formatMoney(plan.price_usd_cents, "USD", locale)}
              </p>
              <button
                type="button"
                className="btn-primary mt-4 w-full text-sm"
                disabled={busy || current || plan.price_usd_cents === 0}
                onClick={() => void checkout(plan.code)}
              >
                {current ? messages.pricing.current : messages.pricing.upgrade}
              </button>
            </div>
          );
        })}
      </div>

      <div className="card p-5">
        <h2 className="text-sm font-semibold">Invoices</h2>
        {payments.length === 0 ? (
          <p className="mt-3 text-sm text-muted">{messages.dashboard.empty}</p>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase text-muted">
                  <th className="py-2 pr-3 font-medium">Date</th>
                  <th className="py-2 pr-3 font-medium">Kind</th>
                  <th className="py-2 pr-3 font-medium">Status</th>
                  <th className="py-2 pr-3 text-right font-medium">Amount</th>
                  <th className="py-2 pr-3 text-right font-medium">Credits</th>
                  <th className="py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {payments.map((payment) => (
                  <tr key={payment.id} className="border-t border-border">
                    <td className="whitespace-nowrap py-2 pr-3 text-xs text-muted">
                      {formatDate(payment.created_at, locale)}
                    </td>
                    <td className="py-2 pr-3">
                      {payment.kind.replace(/_/g, " ")}
                    </td>
                    <td className="py-2 pr-3">{payment.status}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">
                      {formatMoney(
                        payment.amount_minor,
                        payment.currency,
                        locale,
                      )}
                    </td>
                    <td className="py-2 pr-3 text-right tabular-nums">
                      {formatNumber(payment.credits_granted, locale)}
                    </td>
                    <td className="py-2 text-right">
                      {payment.invoice_url && (
                        <a
                          href={payment.invoice_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-accent underline"
                        >
                          {messages.result.download}
                        </a>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
