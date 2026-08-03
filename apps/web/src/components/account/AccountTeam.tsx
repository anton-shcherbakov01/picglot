"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";
import { formatDate } from "@/lib/format";

import {
  ErrorNote,
  Notice,
  OneTimeSecret,
  type SectionProps,
} from "./AccountPanel";

interface Workspace {
  id: string;
  name: string;
  slug: string;
  plan_code: string;
  billing_email: string | null;
  owner_id: string;
  role: string;
  member_count: number;
  created_at: string;
}

interface Member {
  user_id: string;
  email: string;
  name: string | null;
  role: string;
  monthly_credit_limit: number | null;
  joined_at: string;
}

interface Invitation {
  id: string;
  email: string;
  role: string;
  expires_at: string;
  created_at: string;
}

const ROLES = ["viewer", "editor", "admin"] as const;
/** Mirrors WorkspaceRole.rank on the server; the API is still the enforcer. */
const RANK = { viewer: 0, editor: 1, admin: 2, owner: 3 } as const;

function rankOf(role: string): number {
  return RANK[role as keyof typeof RANK] ?? 0;
}

export function AccountTeam({ locale, messages, onError }: SectionProps) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<string>("editor");
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  const active = workspaces.find((item) => item.id === activeId) ?? null;
  const myRank = active ? rankOf(active.role) : 0;
  const canAdminister = myRank >= RANK.admin;

  const loadWorkspaces = useCallback(async () => {
    try {
      const rows = await apiFetch<Workspace[]>("/api/v1/workspaces");
      setWorkspaces(rows);
      setActiveId((current) => current ?? rows[0]?.id ?? null);
      setError(null);
    } catch (failure) {
      if (onError(failure)) return;
      // Teams can be switched off by configuration; say so rather than erroring.
      if ((failure as ApiError).code === "feature_disabled")
        setUnavailable(true);
      else setError((failure as ApiError).message);
    }
  }, [onError]);

  useEffect(() => {
    void loadWorkspaces();
  }, [loadWorkspaces]);

  const loadDetail = useCallback(
    async (workspaceId: string, administering: boolean) => {
      try {
        const [memberRows, inviteRows] = await Promise.all([
          apiFetch<Member[]>(`/api/v1/workspaces/${workspaceId}/members`),
          administering
            ? apiFetch<Invitation[]>(
                `/api/v1/workspaces/${workspaceId}/invitations`,
              )
            : Promise.resolve([] as Invitation[]),
        ]);
        setMembers(memberRows);
        setInvitations(inviteRows);
      } catch (failure) {
        if (!onError(failure)) setError((failure as ApiError).message);
      }
    },
    [onError],
  );

  useEffect(() => {
    if (activeId) void loadDetail(activeId, canAdminister);
  }, [activeId, canAdminister, loadDetail]);

  // An invitation link lands here with ?invitation=<token>; redeem it once.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("invitation");
    if (!token) return;
    apiFetch<Workspace>("/api/v1/workspaces/invitations/accept", {
      method: "POST",
      json: { token },
    })
      .then((workspace) => {
        setNotice(`${messages.dashboard.team}: ${workspace.name}`);
        setActiveId(workspace.id);
        void loadWorkspaces();
      })
      .catch((failure) => {
        if (!onError(failure)) setError((failure as ApiError).message);
      })
      .finally(() => {
        params.delete("invitation");
        const query = params.toString();
        window.history.replaceState(
          {},
          "",
          `${window.location.pathname}${query ? `?${query}` : ""}`,
        );
      });
    // Redeeming must happen exactly once per page load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (failure) {
      if (!onError(failure)) setError((failure as ApiError).message);
    } finally {
      setBusy(false);
    }
  };

  if (unavailable) {
    return (
      <p className="text-sm text-muted">{messages.errors.feature_disabled}</p>
    );
  }

  return (
    <div className="grid gap-4">
      {error && <ErrorNote message={error} />}
      {notice && <Notice message={notice} />}
      {inviteLink && (
        <OneTimeSecret
          label={messages.dashboard.invitations}
          value={inviteLink}
          messages={messages}
          onDismiss={() => setInviteLink(null)}
        />
      )}

      <form
        className="card flex flex-wrap items-end gap-3 p-5"
        onSubmit={(event) => {
          event.preventDefault();
          void run(async () => {
            const created = await apiFetch<Workspace>("/api/v1/workspaces", {
              method: "POST",
              json: { name: newName.trim() },
            });
            setNewName("");
            setActiveId(created.id);
            await loadWorkspaces();
          });
        }}
      >
        <div className="min-w-48 flex-1">
          <label className="label" htmlFor="workspace-name">
            {messages.dashboard.newWorkspace}
          </label>
          <input
            id="workspace-name"
            className="input"
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            required
            minLength={2}
            maxLength={160}
          />
        </div>
        <button type="submit" className="btn-primary text-sm" disabled={busy}>
          {messages.common.save}
        </button>
      </form>

      {workspaces.length === 0 ? (
        <p className="text-sm text-muted">{messages.dashboard.empty}</p>
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            {workspaces.map((workspace) => (
              <button
                key={workspace.id}
                type="button"
                onClick={() => setActiveId(workspace.id)}
                aria-current={workspace.id === activeId ? "true" : undefined}
                className={
                  workspace.id === activeId
                    ? "btn-secondary text-xs"
                    : "btn-ghost text-xs"
                }
              >
                {workspace.name}
                <span className="ml-2 text-muted">{workspace.role}</span>
              </button>
            ))}
          </div>

          {active && (
            <>
              <div className="card p-5">
                <h2 className="text-sm font-semibold">{active.name}</h2>
                <p className="mt-1 text-xs text-muted">
                  {active.slug} · {active.plan_code} · {active.member_count} ·{" "}
                  {formatDate(active.created_at, locale)}
                </p>
                {active.role === "owner" && (
                  <button
                    type="button"
                    className="btn-ghost mt-3 text-xs text-danger"
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        if (
                          !window.confirm(
                            `${messages.dashboard.deleteForever}?`,
                          )
                        )
                          return;
                        await apiFetch(`/api/v1/workspaces/${active.id}`, {
                          method: "DELETE",
                        });
                        setActiveId(null);
                        await loadWorkspaces();
                      })
                    }
                  >
                    {messages.dashboard.deleteForever}
                  </button>
                )}
              </div>

              {canAdminister && (
                <form
                  className="card flex flex-wrap items-end gap-3 p-5"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void run(async () => {
                      const created = await apiFetch<{
                        invitation_url: string;
                      }>(`/api/v1/workspaces/${active.id}/invitations`, {
                        method: "POST",
                        json: { email: inviteEmail.trim(), role: inviteRole },
                      });
                      setInviteEmail("");
                      setInviteLink(created.invitation_url);
                      await loadDetail(active.id, true);
                    });
                  }}
                >
                  <div className="min-w-48 flex-1">
                    <label className="label" htmlFor="invite-email">
                      {messages.dashboard.invitations}
                    </label>
                    <input
                      id="invite-email"
                      type="email"
                      className="input"
                      value={inviteEmail}
                      onChange={(event) => setInviteEmail(event.target.value)}
                      required
                    />
                  </div>
                  <div>
                    <label className="label" htmlFor="invite-role">
                      {messages.dashboard.role}
                    </label>
                    <select
                      id="invite-role"
                      className="input w-auto"
                      value={inviteRole}
                      onChange={(event) => setInviteRole(event.target.value)}
                    >
                      {ROLES.filter((role) => RANK[role] <= myRank).map(
                        (role) => (
                          <option key={role} value={role}>
                            {role}
                          </option>
                        ),
                      )}
                    </select>
                  </div>
                  <button
                    type="submit"
                    className="btn-primary text-sm"
                    disabled={busy}
                  >
                    {messages.dashboard.invite}
                  </button>
                </form>
              )}

              <div className="card p-5">
                <h2 className="text-sm font-semibold">
                  {messages.dashboard.team}
                </h2>
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-xs uppercase text-muted">
                        <th className="py-2 pr-3 font-medium">
                          {messages.auth.email}
                        </th>
                        <th className="py-2 pr-3 font-medium">
                          {messages.dashboard.role}
                        </th>
                        <th className="py-2 pr-3 font-medium">
                          {messages.dashboard.credits
                            .replace("{count}", "")
                            .trim()}
                        </th>
                        <th className="py-2 font-medium" />
                      </tr>
                    </thead>
                    <tbody>
                      {members.map((member) => {
                        const isOwner = member.role === "owner";
                        return (
                          <tr
                            key={member.user_id}
                            className="border-t border-border"
                          >
                            <td className="py-2 pr-3">
                              {member.name ?? member.email}
                              <span className="block text-xs text-muted">
                                {member.email}
                              </span>
                            </td>
                            <td className="py-2 pr-3">
                              {isOwner || !canAdminister ? (
                                member.role
                              ) : (
                                <select
                                  className="input w-auto py-1 text-xs"
                                  value={member.role}
                                  aria-label={messages.dashboard.role}
                                  onChange={(event) =>
                                    void run(async () => {
                                      await apiFetch(
                                        `/api/v1/workspaces/${active.id}/members/${member.user_id}`,
                                        {
                                          method: "PATCH",
                                          json: { role: event.target.value },
                                        },
                                      );
                                      await loadDetail(
                                        active.id,
                                        canAdminister,
                                      );
                                    })
                                  }
                                >
                                  {ROLES.filter(
                                    (role) => RANK[role] <= myRank,
                                  ).map((role) => (
                                    <option key={role} value={role}>
                                      {role}
                                    </option>
                                  ))}
                                </select>
                              )}
                            </td>
                            <td className="py-2 pr-3 tabular-nums">
                              {member.monthly_credit_limit ?? "—"}
                            </td>
                            <td className="py-2 text-right">
                              {active.role === "owner" && !isOwner && (
                                <button
                                  type="button"
                                  className="btn-ghost text-xs"
                                  disabled={busy}
                                  onClick={() =>
                                    void run(async () => {
                                      if (
                                        !window.confirm(
                                          `${messages.common.confirm}?`,
                                        )
                                      )
                                        return;
                                      await apiFetch(
                                        `/api/v1/workspaces/${active.id}/transfer`,
                                        {
                                          method: "POST",
                                          json: {
                                            new_owner_id: member.user_id,
                                          },
                                        },
                                      );
                                      await loadWorkspaces();
                                      await loadDetail(
                                        active.id,
                                        canAdminister,
                                      );
                                    })
                                  }
                                >
                                  {messages.dashboard.transferOwnership}
                                </button>
                              )}
                              {!isOwner &&
                                (canAdminister ||
                                  member.email === active.billing_email) && (
                                  <button
                                    type="button"
                                    className="btn-ghost text-xs text-danger"
                                    disabled={busy}
                                    onClick={() =>
                                      void run(async () => {
                                        await apiFetch(
                                          `/api/v1/workspaces/${active.id}/members/${member.user_id}`,
                                          { method: "DELETE" },
                                        );
                                        await loadWorkspaces();
                                        await loadDetail(
                                          active.id,
                                          canAdminister,
                                        );
                                      })
                                    }
                                  >
                                    {messages.common.delete}
                                  </button>
                                )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {canAdminister && invitations.length > 0 && (
                <div className="card p-5">
                  <h2 className="text-sm font-semibold">
                    {messages.dashboard.invitations}
                  </h2>
                  <ul className="mt-3 grid gap-2 text-sm">
                    {invitations.map((invitation) => (
                      <li
                        key={invitation.id}
                        className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2 first:border-0 first:pt-0"
                      >
                        <span>
                          {invitation.email}
                          <span className="ml-2 text-xs text-muted">
                            {invitation.role} ·{" "}
                            {formatDate(invitation.expires_at, locale)}
                          </span>
                        </span>
                        <button
                          type="button"
                          className="btn-ghost text-xs text-danger"
                          disabled={busy}
                          onClick={() =>
                            void run(async () => {
                              await apiFetch(
                                `/api/v1/workspaces/${active.id}/invitations/${invitation.id}`,
                                { method: "DELETE" },
                              );
                              await loadDetail(active.id, true);
                            })
                          }
                        >
                          {messages.common.delete}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
