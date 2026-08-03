"""Workspaces: shared projects, seats, roles and invitations."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from picglot.api.deps import CsrfProtected, CurrentUser, DbSession
from picglot.core.config import settings
from picglot.core.errors import AppError, ErrorCode
from picglot.core.logging import get_logger
from picglot.db.models import User, WorkspaceMember
from picglot.domain.enums import WorkspaceRole
from picglot.schemas import (
    WorkspaceAcceptRequest,
    WorkspaceCreateRequest,
    WorkspaceInvitationCreatedOut,
    WorkspaceInvitationOut,
    WorkspaceInviteRequest,
    WorkspaceMemberOut,
    WorkspaceMemberUpdate,
    WorkspaceOut,
    WorkspaceTransferRequest,
    WorkspaceUpdateRequest,
)
from picglot.services import notifications
from picglot.services import workspaces as workspace_service

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1/workspaces", tags=["workspaces"])


def _require_feature() -> None:
    if not settings.feature_teams:
        raise AppError(code=ErrorCode.FEATURE_DISABLED, details={"feature": "teams"})


# --------------------------------------------------------------------------- #
# Workspaces
# --------------------------------------------------------------------------- #
@router.get("", response_model=list[WorkspaceOut], summary="Workspaces you belong to")
def list_workspaces(session: DbSession, user: CurrentUser) -> list[WorkspaceOut]:
    _require_feature()
    return [
        WorkspaceOut.model_validate(workspace_service.describe(session, workspace, member))
        for workspace, member in workspace_service.list_for_user(session, user)
    ]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(
    payload: WorkspaceCreateRequest,
    session: DbSession,
    user: CurrentUser,
    _csrf: CsrfProtected,
) -> WorkspaceOut:
    _require_feature()
    workspace = workspace_service.create(
        session,
        owner=user,
        name=payload.name,
        billing_email=str(payload.billing_email) if payload.billing_email else None,
    )
    member = workspace_service.membership(session, workspace.id, user.id)
    assert member is not None  # just created alongside the workspace
    session.commit()
    return WorkspaceOut.model_validate(workspace_service.describe(session, workspace, member))


@router.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(workspace_id: str, session: DbSession, user: CurrentUser) -> WorkspaceOut:
    _require_feature()
    workspace, member = workspace_service.require_member(session, workspace_id, user)
    return WorkspaceOut.model_validate(workspace_service.describe(session, workspace, member))


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(
    workspace_id: str,
    payload: WorkspaceUpdateRequest,
    session: DbSession,
    user: CurrentUser,
    _csrf: CsrfProtected,
) -> WorkspaceOut:
    _require_feature()
    workspace, member = workspace_service.require_member(
        session, workspace_id, user, required=WorkspaceRole.ADMIN
    )
    workspace_service.update(
        session,
        workspace,
        name=payload.name,
        billing_email=str(payload.billing_email) if payload.billing_email else None,
        retention_override_hours=payload.retention_override_hours,
    )
    session.commit()
    return WorkspaceOut.model_validate(workspace_service.describe(session, workspace, member))


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(
    workspace_id: str, session: DbSession, user: CurrentUser, _csrf: CsrfProtected
) -> Response:
    _require_feature()
    workspace, _ = workspace_service.require_member(
        session, workspace_id, user, required=WorkspaceRole.OWNER
    )
    workspace_service.soft_delete(session, workspace)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Members
# --------------------------------------------------------------------------- #
@router.get("/{workspace_id}/members", response_model=list[WorkspaceMemberOut])
def list_members(
    workspace_id: str, session: DbSession, user: CurrentUser
) -> list[WorkspaceMemberOut]:
    _require_feature()
    workspace_service.require_member(session, workspace_id, user)
    rows = session.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.joined_at.asc())
    ).all()
    return [
        WorkspaceMemberOut(
            user_id=member.user_id,
            email=account.email,
            name=account.name,
            role=member.role,
            monthly_credit_limit=member.monthly_credit_limit,
            joined_at=member.joined_at,
        )
        for member, account in rows
    ]


@router.patch("/{workspace_id}/members/{user_id}", response_model=WorkspaceMemberOut)
def update_member(
    workspace_id: str,
    user_id: str,
    payload: WorkspaceMemberUpdate,
    session: DbSession,
    user: CurrentUser,
    _csrf: CsrfProtected,
) -> WorkspaceMemberOut:
    _require_feature()
    workspace, acting = workspace_service.require_member(
        session, workspace_id, user, required=WorkspaceRole.ADMIN
    )
    if payload.role is not None:
        workspace_service.set_role(
            session,
            workspace,
            target_user_id=user_id,
            role=payload.role,
            acting_member=acting,
        )
    if payload.monthly_credit_limit is not None:
        workspace_service.set_member_limit(
            session,
            workspace,
            target_user_id=user_id,
            monthly_credit_limit=payload.monthly_credit_limit,
        )
    session.commit()

    member = workspace_service.membership(session, workspace_id, user_id)
    if member is None:
        raise AppError(code=ErrorCode.NOT_FOUND, details={"user": user_id})
    account = session.get(User, user_id)
    return WorkspaceMemberOut(
        user_id=member.user_id,
        email=account.email if account else "",
        name=account.name if account else None,
        role=member.role,
        monthly_credit_limit=member.monthly_credit_limit,
        joined_at=member.joined_at,
    )


@router.delete("/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    workspace_id: str,
    user_id: str,
    session: DbSession,
    user: CurrentUser,
    _csrf: CsrfProtected,
) -> Response:
    _require_feature()
    # Leaving is always allowed; removing someone else needs admin.
    required = WorkspaceRole.VIEWER if user_id == user.id else WorkspaceRole.ADMIN
    workspace, _ = workspace_service.require_member(session, workspace_id, user, required=required)
    workspace_service.remove_member(session, workspace, target_user_id=user_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{workspace_id}/transfer", response_model=WorkspaceOut)
def transfer_ownership(
    workspace_id: str,
    payload: WorkspaceTransferRequest,
    session: DbSession,
    user: CurrentUser,
    _csrf: CsrfProtected,
) -> WorkspaceOut:
    _require_feature()
    workspace, _ = workspace_service.require_member(
        session, workspace_id, user, required=WorkspaceRole.OWNER
    )
    workspace_service.transfer_ownership(session, workspace, new_owner_id=payload.new_owner_id)
    session.commit()
    member = workspace_service.membership(session, workspace_id, user.id)
    if member is None:
        raise AppError(code=ErrorCode.NOT_FOUND, details={"workspace": workspace_id})
    return WorkspaceOut.model_validate(workspace_service.describe(session, workspace, member))


# --------------------------------------------------------------------------- #
# Invitations
# --------------------------------------------------------------------------- #
@router.get("/{workspace_id}/invitations", response_model=list[WorkspaceInvitationOut])
def list_invitations(
    workspace_id: str, session: DbSession, user: CurrentUser
) -> list[WorkspaceInvitationOut]:
    _require_feature()
    workspace_service.require_member(session, workspace_id, user, required=WorkspaceRole.ADMIN)
    return [
        WorkspaceInvitationOut.model_validate(invitation, from_attributes=True)
        for invitation in workspace_service.list_invitations(session, workspace_id)
    ]


@router.post(
    "/{workspace_id}/invitations",
    response_model=WorkspaceInvitationCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
def create_invitation(
    workspace_id: str,
    payload: WorkspaceInviteRequest,
    session: DbSession,
    user: CurrentUser,
    _csrf: CsrfProtected,
) -> WorkspaceInvitationCreatedOut:
    _require_feature()
    workspace, acting = workspace_service.require_member(
        session, workspace_id, user, required=WorkspaceRole.ADMIN
    )
    if not WorkspaceRole(acting.role).can(payload.role):
        raise AppError(code=ErrorCode.FORBIDDEN, details={"reason": "cannot_grant_above_own_role"})

    invitation, token = workspace_service.invite(
        session,
        workspace,
        email=str(payload.email),
        role=payload.role,
        invited_by=user,
    )
    url = workspace_service.invitation_url(token, locale=user.locale)
    notifications.on_invitation(
        session, str(payload.email), workspace_name=workspace.name, url=url, locale=user.locale
    )
    session.commit()
    return WorkspaceInvitationCreatedOut(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        invitation_url=url,
    )


@router.delete(
    "/{workspace_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT
)
def revoke_invitation(
    workspace_id: str,
    invitation_id: str,
    session: DbSession,
    user: CurrentUser,
    _csrf: CsrfProtected,
) -> Response:
    _require_feature()
    workspace_service.require_member(session, workspace_id, user, required=WorkspaceRole.ADMIN)
    workspace_service.revoke_invitation(session, workspace_id, invitation_id)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invitations/accept", response_model=WorkspaceOut)
def accept_invitation(
    payload: WorkspaceAcceptRequest,
    session: DbSession,
    user: CurrentUser,
    _csrf: CsrfProtected,
) -> WorkspaceOut:
    _require_feature()
    workspace = workspace_service.accept_invitation(session, token=payload.token, user=user)
    member = workspace_service.membership(session, workspace.id, user.id)
    if member is None:
        raise AppError(code=ErrorCode.NOT_FOUND, details={"workspace": workspace.id})
    session.commit()
    return WorkspaceOut.model_validate(workspace_service.describe(session, workspace, member))
