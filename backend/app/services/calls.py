"""Room-scoped LiveKit credentials and bounded server calls. No media is recorded."""

import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import httpx
import jwt
from fastapi import HTTPException

from app.config import get_settings
from app.models.meeting import MeetingRoom
from app.tenancy import principal_context


def enabled() -> bool:
    s = get_settings()
    return bool(s.livekit_url and s.livekit_api_key and s.livekit_api_secret.get_secret_value())


def signed_token(video: dict, *, identity: str | None = None, name: str | None = None) -> str:
    s = get_settings()
    if not enabled():
        raise HTTPException(
            503, "Calls are not connected yet. Ask your administrator to enable AOI calls."
        )
    now = int(time.time())
    return jwt.encode(
        {
            "iss": s.livekit_api_key,
            "sub": identity or "aoi-server",
            "nbf": now - 5,
            "exp": now + 120,
            "video": video,
            **({"name": name} if name else {}),
        },
        s.livekit_api_secret.get_secret_value(),
        algorithm="HS256",
    )


async def rpc(method: str, body: dict, grant: dict) -> dict:
    s = get_settings()
    token = signed_token(grant)
    parsed = urlsplit(s.livekit_url)
    base = s.livekit_api_url or urlunsplit(
        ("https" if parsed.scheme == "wss" else "http", parsed.netloc, "", "", "")
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{base.rstrip('/')}/twirp/livekit.RoomService/{method}",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            503, "The call service is unavailable. Check the connection and try again."
        ) from exc


async def join(room: MeetingRoom, display_name: str) -> dict:
    # Provision a bounded room before issuing any participant credential. Huddles
    # can reuse their AOI record after LiveKit removes an empty media session.
    await rpc(
        "CreateRoom",
        {
            "name": room.media_room,
            "emptyTimeout": 300,
            "departureTimeout": 60,
            "maxParticipants": 50,
        },
        {"roomCreate": True},
    )
    principal = principal_context.get()
    identity = f"{principal.account_id if principal else 'local'}-{uuid.uuid4()}"
    sources = ["microphone", "screen_share", "screen_share_audio"]
    if room.kind == "meeting":
        sources.append("camera")
    token = signed_token(
        {
            "roomJoin": True,
            "room": room.media_room,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
            "canPublishSources": sources,
        },
        identity=identity,
        name=display_name,
    )
    return {"token": token, "server_url": get_settings().livekit_url}


async def presence(rooms: list[MeetingRoom]) -> dict[str, int] | None:
    if not enabled():
        return None
    if not rooms:
        return {}
    try:
        result = await rpc(
            "ListRooms", {"names": [room.media_room for room in rooms]}, {"roomList": True}
        )
        return {
            room["name"]: int(room.get("num_participants", room.get("numParticipants", 0)))
            for room in result.get("rooms", [])
        }
    except HTTPException:
        return None
