"""Same-origin company login; rotating refresh credentials stay HttpOnly."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr

from app.config import base_settings
from app.identity_gateway import gateway_request, verify_gateway_identity

router = APIRouter()


def enabled(request: Request):
    settings = base_settings()
    if settings.auth_mode != "oidc" or settings.auth_provider != "gateway":
        raise HTTPException(404, "Company gateway sign-in is not configured")
    origin = request.headers.get("origin")
    if (
        origin
        and origin not in settings.cors_origin_list
        and origin != str(request.base_url).rstrip("/")
    ):
        raise HTTPException(403, "Sign in from the configured application")


class CompanySession(BaseModel):
    company_slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")


class CompanyLogin(CompanySession):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)


def cookie_name(slug: str) -> str:
    return f"aoi_gateway_refresh_{slug}"


async def accept_session(pair: dict, slug: str, response: Response):
    token = pair.get("access_token")
    refresh = pair.get("refresh_token")
    if not isinstance(token, str) or not isinstance(refresh, str) or not refresh:
        raise HTTPException(503, "Company identity service returned an invalid session")
    identity = await verify_gateway_identity(token)
    if identity.tenant_slug != slug:
        raise HTTPException(401, "Company session mismatch")
    response.set_cookie(
        cookie_name(slug),
        refresh,
        httponly=True,
        secure=base_settings().environment != "development",
        samesite="lax",
        path="/auth/gateway",
        max_age=30 * 24 * 60 * 60,
    )
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": token, "expires_at": identity.expires_at, "company_slug": slug}


@router.post("/login", dependencies=[Depends(enabled)])
async def login(payload: CompanyLogin, response: Response):
    pair = await gateway_request(
        "/v1/auth/login",
        tenant=payload.company_slug,
        payload={
            "email": payload.email,
            "password": payload.password.get_secret_value(),
        },
    )
    return await accept_session(pair, payload.company_slug, response)


@router.post("/session", dependencies=[Depends(enabled)])
async def refresh(payload: CompanySession, request: Request, response: Response):
    token = request.cookies.get(cookie_name(payload.company_slug))
    if not token or len(token) > 4096:
        raise HTTPException(401, "Sign in to your company workspace")
    pair = await gateway_request(
        "/v1/auth/refresh", tenant=payload.company_slug, payload={"refresh_token": token}
    )
    return await accept_session(pair, payload.company_slug, response)


@router.post("/logout", dependencies=[Depends(enabled)])
async def logout(payload: CompanySession, request: Request, response: Response):
    refresh_token = request.cookies.get(cookie_name(payload.company_slug))
    if refresh_token:
        try:
            pair = await gateway_request(
                "/v1/auth/refresh",
                tenant=payload.company_slug,
                payload={"refresh_token": refresh_token},
            )
            from app.auth import verify_claims

            claims = await verify_claims(pair["access_token"], ("EdDSA",))
            if claims.get("tsl") != payload.company_slug:
                raise HTTPException(401, "Company session mismatch")
            await gateway_request(
                "/v1/auth/logout",
                tenant=payload.company_slug,
                token=pair["access_token"],
                payload={},
            )
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
    response.delete_cookie(
        cookie_name(payload.company_slug),
        path="/auth/gateway",
        secure=base_settings().environment != "development",
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"signed_out": True}
