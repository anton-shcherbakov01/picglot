"""Workspace membership, roles and invitations, over the real HTTP surface."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from picglot.core.ids import ulid

ACCEPT = "/api/v1/workspaces/invitations/accept"


def _new_email() -> str:
    return f"member-{ulid()[:10].lower()}@example.com"


def _signed_in_client(email: str) -> TestClient:
    """A second identity with its own cookie jar.

    `registered_client` returns the *same* object as `client`, so registering
    again on it would replace the first user's session rather than give us a
    second one to act as.
    """
    from picglot.main import app

    other = TestClient(app)
    response = other.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Str0ng!Passw0rd",
            "name": "Member",
            "accept_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    other.headers.update({"X-CSRF-Token": response.json()["csrf_token"]})
    return other


def _token_of(created) -> str:
    return created.json()["invitation_url"].rsplit("invitation=", 1)[1]


def _invite(client: TestClient, workspace_id: str, email: str, role: str = "editor"):
    return client.post(
        f"/api/v1/workspaces/{workspace_id}/invitations",
        json={"email": email, "role": role},
    )


@pytest.fixture
def workspace(registered_client: TestClient) -> dict:
    response = registered_client.post("/api/v1/workspaces", json={"name": "Acme Docs"})
    assert response.status_code == 201, response.text
    return response.json()


def test_creator_becomes_owner(registered_client: TestClient, workspace: dict):
    assert workspace["role"] == "owner"
    assert workspace["member_count"] == 1
    assert workspace["slug"] == "acme-docs"

    listed = registered_client.get("/api/v1/workspaces")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [workspace["id"]]


def test_slug_is_unique(registered_client: TestClient, workspace: dict):
    second = registered_client.post("/api/v1/workspaces", json={"name": "Acme Docs"})
    assert second.status_code == 201
    assert second.json()["slug"] != workspace["slug"]


def test_a_stranger_cannot_tell_the_workspace_exists(workspace: dict):
    """Non-membership must look like non-existence, not like a locked door."""
    stranger = _signed_in_client(_new_email())
    response = stranger.get(f"/api/v1/workspaces/{workspace['id']}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_invitation_round_trip(registered_client: TestClient, workspace: dict):
    invitee = _new_email()
    created = _invite(registered_client, workspace["id"], invitee)
    assert created.status_code == 201, created.text
    token = _token_of(created)

    pending = registered_client.get(f"/api/v1/workspaces/{workspace['id']}/invitations")
    assert [row["email"] for row in pending.json()] == [invitee]

    invited = _signed_in_client(invitee)
    accepted = invited.post(ACCEPT, json={"token": token})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["role"] == "editor"
    assert accepted.json()["member_count"] == 2

    # Accepting is single-use.
    again = invited.post(ACCEPT, json={"token": token})
    assert again.status_code == 401
    assert again.json()["error"]["code"] == "token_invalid"


def test_invitation_is_addressed_to_a_person_not_a_link_holder(
    registered_client: TestClient, workspace: dict
):
    created = _invite(registered_client, workspace["id"], _new_email())
    token = _token_of(created)

    someone_else = _signed_in_client(_new_email())
    response = someone_else.post(ACCEPT, json={"token": token})
    assert response.status_code == 403
    assert response.json()["error"]["details"]["reason"] == "invitation_address_mismatch"


def test_reinviting_replaces_the_previous_token(registered_client: TestClient, workspace: dict):
    invitee = _new_email()
    first = _invite(registered_client, workspace["id"], invitee, "viewer")
    second = _invite(registered_client, workspace["id"], invitee, "editor")
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["invitation_url"] != second.json()["invitation_url"]
    assert second.json()["role"] == "editor"


def test_revoked_invitation_stops_working(registered_client: TestClient, workspace: dict):
    invitee = _new_email()
    created = _invite(registered_client, workspace["id"], invitee)
    token = _token_of(created)
    revoked = registered_client.delete(
        f"/api/v1/workspaces/{workspace['id']}/invitations/{created.json()['id']}"
    )
    assert revoked.status_code == 204

    invited = _signed_in_client(invitee)
    response = invited.post(ACCEPT, json={"token": token})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_invalid"


def test_owner_cannot_be_removed_and_role_needs_transfer(
    registered_client: TestClient, workspace: dict
):
    owner_id = workspace["owner_id"]

    removed = registered_client.delete(f"/api/v1/workspaces/{workspace['id']}/members/{owner_id}")
    assert removed.status_code == 422
    assert removed.json()["error"]["details"]["reason"] == "owner_cannot_be_removed"

    demoted = registered_client.patch(
        f"/api/v1/workspaces/{workspace['id']}/members/{owner_id}",
        json={"role": "viewer"},
    )
    assert demoted.status_code == 422
    assert demoted.json()["error"]["details"]["reason"] == "use_transfer_ownership"


def test_viewer_cannot_invite(registered_client: TestClient, workspace: dict):
    invitee = _new_email()
    created = _invite(registered_client, workspace["id"], invitee, "viewer")
    invited = _signed_in_client(invitee)
    assert invited.post(ACCEPT, json={"token": _token_of(created)}).status_code == 200

    # A viewer can read the workspace...
    assert invited.get(f"/api/v1/workspaces/{workspace['id']}").status_code == 200
    # ...but not administer it.
    denied = _invite(invited, workspace["id"], _new_email())
    assert denied.status_code == 403


def test_transfer_ownership_leaves_exactly_one_owner(
    registered_client: TestClient, workspace: dict
):
    invitee = _new_email()
    created = _invite(registered_client, workspace["id"], invitee, "admin")
    invited = _signed_in_client(invitee)
    assert invited.post(ACCEPT, json={"token": _token_of(created)}).status_code == 200

    members = registered_client.get(f"/api/v1/workspaces/{workspace['id']}/members").json()
    new_owner_id = next(row["user_id"] for row in members if row["email"] == invitee)

    transferred = registered_client.post(
        f"/api/v1/workspaces/{workspace['id']}/transfer",
        json={"new_owner_id": new_owner_id},
    )
    assert transferred.status_code == 200

    after = registered_client.get(f"/api/v1/workspaces/{workspace['id']}/members").json()
    owners = [row for row in after if row["role"] == "owner"]
    assert len(owners) == 1
    assert owners[0]["user_id"] == new_owner_id
    # The previous owner keeps admin rather than being locked out of their own data.
    previous = next(row for row in after if row["user_id"] == workspace["owner_id"])
    assert previous["role"] == "admin"


def test_a_member_may_always_leave(registered_client: TestClient, workspace: dict):
    invitee = _new_email()
    created = _invite(registered_client, workspace["id"], invitee, "viewer")
    invited = _signed_in_client(invitee)
    assert invited.post(ACCEPT, json={"token": _token_of(created)}).status_code == 200

    me = invited.get("/api/v1/auth/me").json()
    left = invited.delete(f"/api/v1/workspaces/{workspace['id']}/members/{me['id']}")
    assert left.status_code == 204
    assert invited.get(f"/api/v1/workspaces/{workspace['id']}").status_code == 404


def test_inviting_an_existing_member_conflicts(registered_client: TestClient, workspace: dict):
    invitee = _new_email()
    created = _invite(registered_client, workspace["id"], invitee)
    invited = _signed_in_client(invitee)
    assert invited.post(ACCEPT, json={"token": _token_of(created)}).status_code == 200

    again = _invite(registered_client, workspace["id"], invitee)
    assert again.status_code == 409
    assert again.json()["error"]["details"]["reason"] == "already_a_member"


def test_member_role_and_credit_limit_can_be_set(registered_client: TestClient, workspace: dict):
    invitee = _new_email()
    created = _invite(registered_client, workspace["id"], invitee, "viewer")
    invited = _signed_in_client(invitee)
    assert invited.post(ACCEPT, json={"token": _token_of(created)}).status_code == 200

    members = registered_client.get(f"/api/v1/workspaces/{workspace['id']}/members").json()
    member_id = next(row["user_id"] for row in members if row["email"] == invitee)

    updated = registered_client.patch(
        f"/api/v1/workspaces/{workspace['id']}/members/{member_id}",
        json={"role": "editor", "monthly_credit_limit": 250},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["role"] == "editor"
    assert updated.json()["monthly_credit_limit"] == 250


def test_deleted_workspace_disappears(registered_client: TestClient, workspace: dict):
    assert registered_client.delete(f"/api/v1/workspaces/{workspace['id']}").status_code == 204
    assert registered_client.get(f"/api/v1/workspaces/{workspace['id']}").status_code == 404
    assert registered_client.get("/api/v1/workspaces").json() == []
