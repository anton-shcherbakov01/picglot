"""Workspaces: shared projects, seats, roles and invitations.

Authorisation lives here rather than in the router so every caller — HTTP,
worker, future CLI — goes through the same check. A user who is not a member
of a workspace must not be able to tell it exists, so lookups raise NOT_FOUND
rather than FORBIDDEN.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger
from picglot.core.security import hash_token
from picglot.db.models import User, Workspace, WorkspaceInvitation, WorkspaceMember
from picglot.domain.enums import WorkspaceRole

log = get_logger(__name__)

INVITATION_TTL_HOURS = 168  # 7 days


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:64] or "workspace"


def _unique_slug(session: Session, name: str) -> str:
    base = _slugify(name)
    slug = base
    for attempt in range(2, 50):
        exists = session.execute(
            select(Workspace.id).where(Workspace.slug == slug)
        ).scalar_one_or_none()
        if exists is None:
            return slug
        slug = f"{base}-{attempt}"
    return f"{base}-{secrets.token_hex(3)}"


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #
def membership(session: Session, workspace_id: str, user_id: str) -> WorkspaceMember | None:
    return session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    ).scalar_one_or_none()


def require_member(
    session: Session,
    workspace_id: str,
    user: User,
    *,
    required: WorkspaceRole = WorkspaceRole.VIEWER,
) -> tuple[Workspace, WorkspaceMember]:
    """Resolve a workspace the user may act in, at or above `required`."""
    workspace = session.execute(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.deleted_at.is_(None))
    ).scalar_one_or_none()
    member = membership(session, workspace_id, user.id) if workspace else None
    # Non-membership is indistinguishable from non-existence, on purpose.
    if workspace is None or member is None:
        raise AppError(code=ErrorCode.NOT_FOUND, details={"workspace": workspace_id})
    if not WorkspaceRole(member.role).can(required):
        raise AppError(
            code=ErrorCode.FORBIDDEN,
            details={"required_role": str(required), "role": member.role},
        )
    return workspace, member


def list_for_user(session: Session, user: User) -> list[tuple[Workspace, WorkspaceMember]]:
    rows = session.execute(
        select(Workspace, WorkspaceMember)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id, Workspace.deleted_at.is_(None))
        .order_by(Workspace.created_at.asc())
    ).all()
    return [(workspace, member) for workspace, member in rows]


def member_count(session: Session, workspace_id: str) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == workspace_id)
        ).scalar_one()
    )


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def create(
    session: Session, *, owner: User, name: str, billing_email: str | None = None
) -> Workspace:
    if not settings.feature_teams:
        raise AppError(code=ErrorCode.FEATURE_DISABLED, details={"feature": "teams"})

    workspace = Workspace(
        name=name.strip(),
        slug=_unique_slug(session, name),
        owner_id=owner.id,
        billing_email=billing_email or owner.email,
        plan_code=owner.plan_code,
    )
    session.add(workspace)
    session.flush()
    session.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            user_id=owner.id,
            role=WorkspaceRole.OWNER,
        )
    )
    session.flush()
    log.info("workspace.created", workspace_id=workspace.id)
    return workspace


def update(
    session: Session,
    workspace: Workspace,
    *,
    name: str | None = None,
    billing_email: str | None = None,
    retention_override_hours: int | None = None,
) -> Workspace:
    if name is not None:
        workspace.name = name.strip()
    if billing_email is not None:
        workspace.billing_email = billing_email
    if retention_override_hours is not None:
        workspace.retention_override_hours = retention_override_hours
    session.flush()
    return workspace


def soft_delete(session: Session, workspace: Workspace) -> None:
    workspace.deleted_at = datetime.now(UTC)
    session.flush()
    log.info("workspace.deleted", workspace_id=workspace.id)


# --------------------------------------------------------------------------- #
# Members
# --------------------------------------------------------------------------- #
def set_role(
    session: Session,
    workspace: Workspace,
    *,
    target_user_id: str,
    role: WorkspaceRole,
    acting_member: WorkspaceMember,
) -> WorkspaceMember:
    target = membership(session, workspace.id, target_user_id)
    if target is None:
        raise AppError(code=ErrorCode.NOT_FOUND, details={"user": target_user_id})

    # Ownership moves through transfer_ownership, which keeps exactly one owner.
    if role == WorkspaceRole.OWNER or WorkspaceRole(target.role) == WorkspaceRole.OWNER:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED, details={"reason": "use_transfer_ownership"}
        )
    # Nobody may promote someone above their own rank.
    if not WorkspaceRole(acting_member.role).can(role):
        raise AppError(code=ErrorCode.FORBIDDEN, details={"reason": "cannot_grant_above_own_role"})

    target.role = role
    session.flush()
    return target


def set_member_limit(
    session: Session, workspace: Workspace, *, target_user_id: str, monthly_credit_limit: int | None
) -> WorkspaceMember:
    target = membership(session, workspace.id, target_user_id)
    if target is None:
        raise AppError(code=ErrorCode.NOT_FOUND, details={"user": target_user_id})
    target.monthly_credit_limit = monthly_credit_limit
    session.flush()
    return target


def remove_member(session: Session, workspace: Workspace, *, target_user_id: str) -> None:
    target = membership(session, workspace.id, target_user_id)
    if target is None:
        raise AppError(code=ErrorCode.NOT_FOUND, details={"user": target_user_id})
    if WorkspaceRole(target.role) == WorkspaceRole.OWNER:
        raise AppError(
            code=ErrorCode.VALIDATION_FAILED, details={"reason": "owner_cannot_be_removed"}
        )
    session.delete(target)
    session.flush()


def transfer_ownership(session: Session, workspace: Workspace, *, new_owner_id: str) -> Workspace:
    """Hand the workspace over. The old owner stays on as an admin."""
    new_owner = membership(session, workspace.id, new_owner_id)
    if new_owner is None:
        raise AppError(code=ErrorCode.NOT_FOUND, details={"user": new_owner_id})
    if new_owner_id == workspace.owner_id:
        return workspace

    previous = membership(session, workspace.id, workspace.owner_id)
    if previous is not None:
        previous.role = WorkspaceRole.ADMIN
    new_owner.role = WorkspaceRole.OWNER
    workspace.owner_id = new_owner_id
    session.flush()
    log.info("workspace.ownership_transferred", workspace_id=workspace.id)
    return workspace


# --------------------------------------------------------------------------- #
# Invitations
# --------------------------------------------------------------------------- #
def list_invitations(session: Session, workspace_id: str) -> list[WorkspaceInvitation]:
    return list(
        session.execute(
            select(WorkspaceInvitation)
            .where(
                WorkspaceInvitation.workspace_id == workspace_id,
                WorkspaceInvitation.accepted_at.is_(None),
                WorkspaceInvitation.revoked_at.is_(None),
            )
            .order_by(WorkspaceInvitation.created_at.desc())
        ).scalars()
    )


def invite(
    session: Session,
    workspace: Workspace,
    *,
    email: str,
    role: WorkspaceRole,
    invited_by: User,
) -> tuple[WorkspaceInvitation, str]:
    """Create (or replace) an invitation. Returns the row and the raw token."""
    if role == WorkspaceRole.OWNER:
        raise AppError(code=ErrorCode.VALIDATION_FAILED, details={"reason": "cannot_invite_owner"})

    normalised = email.strip().lower()
    already = session.execute(
        select(WorkspaceMember)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace.id, func.lower(User.email) == normalised)
    ).scalar_one_or_none()
    if already is not None:
        raise AppError(code=ErrorCode.CONFLICT, details={"reason": "already_a_member"})

    # One live invitation per address: re-inviting replaces the old token so the
    # previous link stops working.
    existing = session.execute(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.workspace_id == workspace.id,
            func.lower(WorkspaceInvitation.email) == normalised,
        )
    ).scalar_one_or_none()

    raw = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(hours=INVITATION_TTL_HOURS)

    if existing is not None:
        existing.role = role
        existing.token_hash = hash_token(raw)
        existing.invited_by_id = invited_by.id
        existing.expires_at = expires
        existing.accepted_at = None
        existing.revoked_at = None
        session.flush()
        return existing, raw

    invitation = WorkspaceInvitation(
        workspace_id=workspace.id,
        email=normalised,
        role=role,
        token_hash=hash_token(raw),
        invited_by_id=invited_by.id,
        expires_at=expires,
    )
    session.add(invitation)
    session.flush()
    return invitation, raw


def revoke_invitation(session: Session, workspace_id: str, invitation_id: str) -> None:
    invitation = session.execute(
        select(WorkspaceInvitation).where(
            WorkspaceInvitation.id == invitation_id,
            WorkspaceInvitation.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if invitation is None:
        raise AppError(code=ErrorCode.NOT_FOUND, details={"invitation": invitation_id})
    invitation.revoked_at = datetime.now(UTC)
    session.flush()


def accept_invitation(session: Session, *, token: str, user: User) -> Workspace:
    invitation = session.execute(
        select(WorkspaceInvitation).where(WorkspaceInvitation.token_hash == hash_token(token))
    ).scalar_one_or_none()
    if invitation is None or invitation.revoked_at is not None:
        raise AppError(code=ErrorCode.TOKEN_INVALID)
    if invitation.accepted_at is not None:
        raise AppError(code=ErrorCode.TOKEN_INVALID, details={"reason": "already_accepted"})

    expires_at = invitation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at < datetime.now(UTC):
        raise AppError(code=ErrorCode.TOKEN_EXPIRED)

    # The invitation is addressed to a person, not to whoever holds the link.
    if user.email.strip().lower() != invitation.email.strip().lower():
        raise AppError(code=ErrorCode.FORBIDDEN, details={"reason": "invitation_address_mismatch"})

    workspace = session.execute(
        select(Workspace).where(
            Workspace.id == invitation.workspace_id, Workspace.deleted_at.is_(None)
        )
    ).scalar_one_or_none()
    if workspace is None:
        raise AppError(code=ErrorCode.NOT_FOUND, details={"workspace": invitation.workspace_id})

    if membership(session, workspace.id, user.id) is None:
        session.add(
            WorkspaceMember(
                workspace_id=workspace.id,
                user_id=user.id,
                role=invitation.role,
            )
        )
    invitation.accepted_at = datetime.now(UTC)
    session.flush()
    log.info("workspace.invitation_accepted", workspace_id=workspace.id)
    return workspace


def invitation_url(token: str, locale: str = "en") -> str:
    base = settings.public_web_url.rstrip("/")
    return f"{base}/{locale}/app/account?invitation={token}"


def describe(session: Session, workspace: Workspace, member: WorkspaceMember) -> dict[str, Any]:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "slug": workspace.slug,
        "plan_code": workspace.plan_code,
        "billing_email": workspace.billing_email,
        "owner_id": workspace.owner_id,
        "role": member.role,
        "member_count": member_count(session, workspace.id),
        "retention_override_hours": workspace.retention_override_hours,
        "created_at": workspace.created_at,
    }
