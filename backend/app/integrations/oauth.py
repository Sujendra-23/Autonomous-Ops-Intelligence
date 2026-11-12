"""Provider OAuth protocols and destination discovery. Tokens never leave the server."""

import base64
import hashlib
import json
import re
import time
from urllib.parse import quote, urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.config import base_settings
from app.tenancy import CONNECTOR_FIELDS, workspace_settings

PROVIDERS = {
    "google_calendar": (
        "Google Calendar",
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
        "https://www.googleapis.com/auth/calendar.readonly https://www.googleapis.com/auth/calendar.events",
    ),
    "slack": (
        "Slack",
        "https://slack.com/oauth/v2/authorize",
        "https://slack.com/api/oauth.v2.access",
        "chat:write,incoming-webhook",
    ),
    "notion": (
        "Notion",
        "https://api.notion.com/v1/oauth/authorize",
        "https://api.notion.com/v1/oauth/token",
        "",
    ),
    "linear": (
        "Linear",
        "https://linear.app/oauth/authorize",
        "https://api.linear.app/oauth/token",
        "read,write",
    ),
    "jira": (
        "Jira",
        "https://auth.atlassian.com/authorize",
        "https://auth.atlassian.com/oauth/token",
        "read:jira-work write:jira-work offline_access",
    ),
    "discord": (
        "Discord",
        "https://discord.com/oauth2/authorize",
        "https://discord.com/api/oauth2/token",
        "webhook.incoming",
    ),
    "teams": (
        "Microsoft Teams",
        "",
        "",
        "offline_access User.Read Team.ReadBasic.All Channel.ReadBasic.All ChannelMessage.Send",
    ),
}
PROVIDER_FIELDS = {
    "google_calendar": (
        "google_calendar_client_id",
        "google_calendar_client_secret",
        "google_calendar_refresh_token",
        "google_calendar_id",
    ),
    "slack": ("slack_bot_token", "slack_default_channel"),
    "notion": ("notion_api_key", "notion_parent_page_id"),
    "linear": ("linear_api_key", "linear_team_id"),
    "jira": ("jira_base_url", "jira_email", "jira_api_token", "jira_project_key", "jira_cloud_id"),
    "discord": ("discord_webhook_url",),
    "teams": ("teams_webhook_url", "teams_team_id", "teams_channel_id"),
}


class OAuthError(Exception):
    """Only safe messages authored here may reach the client."""


def cipher():
    try:
        return Fernet(base_settings().connector_encryption_key.get_secret_value().encode())
    except (ValueError, TypeError):
        raise OAuthError("Your administrator needs to configure connector encryption.") from None


def credentials(provider):
    settings = base_settings()
    prefix = "google_calendar" if provider == "google_calendar" else provider
    return getattr(settings, prefix + "_client_id"), getattr(
        settings, prefix + "_client_secret"
    ).get_secret_value()


def endpoints(provider):
    if provider != "teams":
        return PROVIDERS[provider][1:3]
    tenant = base_settings().teams_tenant_id
    if not re.fullmatch(r"[A-Za-z0-9.-]+", tenant):
        raise OAuthError("Your administrator needs to configure a valid Microsoft tenant.")
    prefix = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    return prefix + "/authorize", prefix + "/token"


def redirect_uri(provider):
    settings = base_settings()
    base = settings.connector_oauth_redirect_base.rstrip("/")
    parsed = urlsplit(base)
    if (
        (
            parsed.scheme != "https"
            and not (
                settings.environment == "development"
                and parsed.scheme == "http"
                and parsed.hostname in {"localhost", "127.0.0.1"}
            )
        )
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise OAuthError("Your administrator needs to configure the public OAuth callback URL.")
    return base + f"/oauth/connectors/{provider}/callback"


def setup_error(provider):
    if not all(credentials(provider)):
        return (
            f"{PROVIDERS[provider][0]} sign-in is not configured on this server. "
            "A workspace administrator must add this provider's OAuth client ID and secret "
            "and register the callback URL before you can sign in."
        )
    try:
        cipher()
        redirect_uri(provider)
        endpoints(provider)
    except OAuthError as exc:
        return str(exc)
    return None


def available(provider):
    return setup_error(provider) is None


def state_key(state):
    return "aoi:oauth:state:" + hashlib.sha256(state.encode()).hexdigest()


def ticket_key(ticket):
    return "aoi:oauth:ticket:" + hashlib.sha256(ticket.encode()).hexdigest()


def authorization(provider, state, verifier):
    client_id, _ = credentials(provider)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(provider),
        "response_type": "code",
        "state": state,
    }
    scope = PROVIDERS[provider][3]
    if scope:
        params["scope"] = scope
    if provider in {"google_calendar", "linear", "teams"}:
        params.update(
            code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode(),
            code_challenge_method="S256",
        )
    if provider == "google_calendar":
        params.update(access_type="offline", prompt="consent")
    elif provider == "notion":
        params["owner"] = "user"
    elif provider == "jira":
        params.update(audience="api.atlassian.com", prompt="consent")
    return endpoints(provider)[0] + "?" + urlencode(params)


async def request_json(client, method, url, **kwargs):
    try:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict) and (
            body.get("error") or body.get("errors") or body.get("ok") is False
        ):
            raise OAuthError(
                "The provider rejected access. Reconnect and approve the requested permissions."
            )
        return body
    except (httpx.HTTPError, ValueError):
        raise OAuthError(
            "The provider could not be reached or denied access. Please reconnect and try again."
        ) from None


async def exchange(client, provider, *, code=None, verifier=None, refresh_token=None):
    client_id, secret = credentials(provider)
    payload = {"grant_type": "refresh_token" if refresh_token else "authorization_code"}
    if refresh_token:
        payload["refresh_token"] = refresh_token
    else:
        payload.update(code=code, redirect_uri=redirect_uri(provider))
        if provider in {"google_calendar", "linear", "teams"}:
            payload["code_verifier"] = verifier
    kwargs = {}
    if provider in {"notion", "slack", "discord"}:
        kwargs["auth"] = (client_id, secret)
    else:
        payload.update(client_id=client_id, client_secret=secret)
    kwargs["json" if provider in {"notion", "jira"} else "data"] = payload
    body = await request_json(client, "POST", endpoints(provider)[1], **kwargs)
    if not isinstance(body, dict) or not body.get("access_token"):
        raise OAuthError("The provider did not return an access token. Please reconnect.")
    return body


def grant(body):
    value = {"access_token": body["access_token"], "refresh_token": body.get("refresh_token", "")}
    if body.get("expires_in"):
        value["expires_at"] = time.time() + int(body["expires_in"])
    return value


def grants(settings):
    return json.loads(settings.connector_oauth.get_secret_value())


def save_settings(row, settings, metadata):
    settings.connector_oauth = SecretStr(json.dumps(metadata))
    values = {
        name: (
            getattr(settings, name).get_secret_value()
            if isinstance(getattr(settings, name), SecretStr)
            else getattr(settings, name)
        )
        for name in CONNECTOR_FIELDS
    }
    row.connector_ciphertext = cipher().encrypt(json.dumps(values).encode()).decode()


def choice(identifier, label, values):
    return {"id": str(identifier), "label": str(label)[:200], "values": values}


def notion_title(page):
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return (
                "".join(t.get("plain_text", "") for t in prop.get("title", [])) or "Untitled page"
            )
    return "Untitled page"


async def destinations(client, provider, token, body=None, selected=None):
    """Discover allowed destinations; never guess which of several should receive data."""
    headers = {"Authorization": f"Bearer {token}"}
    body = body or {}
    if provider == "google_calendar":
        if not body.get("refresh_token"):
            raise OAuthError("Google did not grant offline access. Reconnect and allow access.")
        # Validate the primary calendar before enabling meeting import.
        await request_json(
            client,
            "GET",
            "https://www.googleapis.com/calendar/v3/calendars/primary",
            headers=headers,
        )
        client_id, secret = credentials(provider)
        return [
            choice(
                "primary",
                "Primary calendar",
                {
                    "google_calendar_client_id": client_id,
                    "google_calendar_client_secret": secret,
                    "google_calendar_refresh_token": body["refresh_token"],
                    "google_calendar_id": "primary",
                },
            )
        ]
    if provider == "slack":
        webhook = body.get("incoming_webhook", {})
        if not webhook.get("channel_id"):
            raise OAuthError("Select a Slack channel during authorization, then reconnect.")
        return [
            choice(
                webhook["channel_id"],
                webhook.get("channel", "Slack channel"),
                {"slack_bot_token": token, "slack_default_channel": webhook["channel_id"]},
            )
        ]
    if provider == "discord":
        webhook = body.get("webhook", {})
        url = webhook.get("url")
        if not url and webhook.get("id") and webhook.get("token"):
            url = f"https://discord.com/api/webhooks/{webhook['id']}/{webhook['token']}"
        if not url:
            raise OAuthError("Select a Discord channel during authorization, then reconnect.")
        return [
            choice(
                webhook.get("channel_id", "channel"),
                webhook.get("name", "Discord channel"),
                {"discord_webhook_url": url},
            )
        ]
    if provider == "linear":
        result = await request_json(
            client,
            "POST",
            "https://api.linear.app/graphql",
            headers=headers,
            json={"query": "{ teams { nodes { id name } } }"},
        )
        return [
            choice(
                t["id"],
                t["name"],
                {"linear_api_key": token, "linear_team_id": t["id"], "task_sync_enabled": True},
            )
            for t in result.get("data", {}).get("teams", {}).get("nodes", [])
        ]
    if provider == "notion":
        headers["Notion-Version"] = "2022-06-28"
        pages, cursor = [], None
        for _ in range(20):
            payload = {"filter": {"value": "page", "property": "object"}, "page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            result = await request_json(
                client, "POST", "https://api.notion.com/v1/search", headers=headers, json=payload
            )
            pages.extend(
                p
                for p in result.get("results", [])
                if not p.get("archived") and not p.get("in_trash")
            )
            cursor = result.get("next_cursor")
            if not result.get("has_more") or not cursor:
                break
        # Prefer the duplicated template, or shared roots rather than their child pages.
        if body.get("duplicated_template_id"):
            pages = [
                p
                for p in pages
                if p["id"].replace("-", "") == body["duplicated_template_id"].replace("-", "")
            ]
        ids = {p["id"] for p in pages}
        roots = [p for p in pages if p.get("parent", {}).get("page_id") not in ids]
        return [
            choice(
                p["id"],
                notion_title(p),
                {"notion_api_key": token, "notion_parent_page_id": p["id"]},
            )
            for p in roots
        ]
    if provider == "jira":
        if not selected:
            sites = await request_json(
                client,
                "GET",
                "https://api.atlassian.com/oauth/token/accessible-resources",
                headers=headers,
            )
            return [
                choice(s["id"], s["name"], {"jira_cloud_id": s["id"], "jira_base_url": s["url"]})
                for s in sites
                if "write:jira-work" in s.get("scopes", [])
            ]
        cloud = quote(selected["jira_cloud_id"], safe="")
        projects, start = [], 0
        for _ in range(20):
            data = await request_json(
                client,
                "GET",
                f"https://api.atlassian.com/ex/jira/{cloud}/rest/api/3/project/search",
                headers=headers,
                params={"startAt": start, "maxResults": 100},
            )
            projects.extend(data.get("values", []))
            if data.get("isLast", True):
                break
            start += len(data.get("values", []))
        return [
            choice(
                p["key"],
                f"{p['name']} ({p['key']})",
                {**selected, "jira_project_key": p["key"], "task_sync_enabled": True},
            )
            for p in projects
        ]
    if provider == "teams":
        if not selected:
            data = await request_json(
                client, "GET", "https://graph.microsoft.com/v1.0/me/joinedTeams", headers=headers
            )
            return [
                choice(t["id"], t["displayName"], {"teams_team_id": t["id"]})
                for t in data.get("value", [])
            ]
        team = quote(selected["teams_team_id"], safe="")
        data = await request_json(
            client,
            "GET",
            f"https://graph.microsoft.com/v1.0/teams/{team}/channels",
            headers=headers,
        )
        return [
            choice(c["id"], c["displayName"], {**selected, "teams_channel_id": c["id"]})
            for c in data.get("value", [])
            if c.get("membershipType", "standard") == "standard"
        ]
    raise OAuthError("Unsupported connector.")


async def advance(client, provider, entry, selected=None):
    """Auto-complete a unique destination; expose a picker for ambiguous grants."""
    if selected is None:
        options = entry["choices"]
    else:
        options = await destinations(client, provider, entry["access_token"], selected=selected)
    if not options:
        raise OAuthError(
            "No available destination. Give the connector access to a page, team, "
            "project or channel, then reconnect."
        )
    # Jira and Teams discovery has a site/team stage followed by project/channel.
    if len(options) == 1:
        selected = options[0]["values"]
        if (provider == "jira" and not selected.get("jira_project_key")) or (
            provider == "teams" and not selected.get("teams_channel_id")
        ):
            return await advance(client, provider, entry, selected)
        entry.update(status="connected", destination=options[0]["label"], choices=[])
        return selected
    entry.update(status="select_destination", choices=options)
    return {}


async def access_token(provider, *, rejected_token=None):
    """Refresh rotating grants under a workspace row lock, including in worker jobs."""
    from app.config import get_settings
    from app.database import SessionLocal
    from app.models.account import Workspace
    from app.tenancy import LEGACY_WORKSPACE, settings_context, workspace_context

    metadata = grants(get_settings())
    entry = metadata.get(provider)
    if not entry or entry.get("status") != "connected":
        return None
    if (
        entry.get("expires_at", float("inf")) > time.time() + 120
        and entry["access_token"] != rejected_token
    ):
        return entry["access_token"]
    async with SessionLocal() as db:
        row = await db.get(
            Workspace, workspace_context.get() or LEGACY_WORKSPACE, with_for_update=True
        )
        if row is None:
            raise OAuthError("Workspace connection was removed. Reconnect the provider.")
        settings = workspace_settings(row.connector_ciphertext)
        metadata = grants(settings)
        entry = metadata.get(provider)
        if not entry or entry.get("status") != "connected":
            raise OAuthError("Workspace connection was removed. Reconnect the provider.")
        if (
            entry.get("expires_at", float("inf")) <= time.time() + 120
            or entry["access_token"] == rejected_token
        ):
            if not entry.get("refresh_token"):
                raise OAuthError("Your provider session expired. Reconnect the provider.")
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                body = await exchange(client, provider, refresh_token=entry["refresh_token"])
            updated = grant(body)
            if not updated["refresh_token"]:
                updated["refresh_token"] = entry["refresh_token"]
            entry.update(updated)
            field = {
                "linear": "linear_api_key",
                "notion": "notion_api_key",
                "slack": "slack_bot_token",
            }.get(provider)
            if field:
                setattr(settings, field, SecretStr(entry["access_token"]))
            save_settings(row, settings, metadata)
            await db.commit()
        settings_context.set(settings)
        return entry["access_token"]


async def notion_request(client, method, url, *, headers, **kwargs):
    """Notion does not always return an expiry; refresh once on a rejected OAuth token."""
    response = await client.request(method, url, headers=headers, **kwargs)
    if response.status_code == 401:
        token = await access_token("notion", rejected_token=headers["Authorization"][7:])
        if token:
            response = await client.request(
                method, url, headers={**headers, "Authorization": f"Bearer {token}"}, **kwargs
            )
    return response


async def linear_authorization(fallback):
    token = await access_token("linear")
    return f"Bearer {token}" if token else fallback


async def jira_connection(settings):
    token = await access_token("jira")
    if token:
        return (
            f"https://api.atlassian.com/ex/jira/{quote(settings.jira_cloud_id, safe='')}",
            {"Authorization": f"Bearer {token}"},
            None,
        )
    return (
        settings.jira_base_url.rstrip("/"),
        {},
        (settings.jira_email, settings.jira_api_token.get_secret_value()),
    )
