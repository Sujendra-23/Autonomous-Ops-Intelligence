import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import { setBearerToken } from "./api";

const authority = import.meta.env.VITE_AUTH_AUTHORITY as string | undefined;
const clientId = import.meta.env.VITE_AUTH_CLIENT_ID as string | undefined;
export const gatewayAuth = import.meta.env.VITE_AUTH_PROVIDER === "gateway";
export const authConfigured = gatewayAuth || Boolean(authority && clientId);
export const identity = !gatewayAuth && authority && clientId ? new UserManager({
  authority: authority!, client_id: clientId!,
  redirect_uri: `${window.location.origin}/auth/callback`,
  post_logout_redirect_uri: window.location.origin,
  response_type: "code", scope: "openid profile email",
  resource: import.meta.env.VITE_AUTH_AUDIENCE as string | undefined,
  userStore: new WebStorageStateStore({ store: window.sessionStorage }),
  automaticSilentRenew: false,
  extraQueryParams: import.meta.env.VITE_AUTH_AUDIENCE ? { audience: import.meta.env.VITE_AUTH_AUDIENCE } : undefined,
}) : null;

identity?.events.addUserLoaded(user => setBearerToken(user.access_token));
identity?.events.addAccessTokenExpired(() => { setBearerToken(""); window.dispatchEvent(new Event("aoi-session-expired")); });
identity?.events.addUserUnloaded(() => { setBearerToken(""); window.dispatchEvent(new Event("aoi-session-expired")); });

export async function restoreIdentity() {
  if (gatewayAuth) return restoreGateway();
  if (!identity) return false;
  const user = window.location.pathname === "/auth/callback"
    ? await identity.signinRedirectCallback() : await identity.getUser();
  if (window.location.pathname === "/auth/callback") {
    const returnTo = (user?.state as { returnTo?: unknown } | undefined)?.returnTo;
    const safePath = typeof returnTo === "string" && /^\/(?:meetings(?:\/[a-zA-Z0-9%-]+)?|dashboard|extension|weekly-summary|upload|tasks|decisions|intelligence|settings|pricing)?$/.test(returnTo);
    window.history.replaceState({}, "", safePath ? returnTo : "/");
  }
  if (!user || user.expired) return false;
  setBearerToken(user.access_token);
  return true;
}

type GatewaySession = { access_token: string; expires_at: number; company_slug: string };
let gatewayToken = "";
let refreshPromise: Promise<boolean> | null = null;
let refreshTimer: ReturnType<typeof setTimeout>;

async function companySession(path: string, payload: object): Promise<GatewaySession> {
  const response = await fetch(`/auth/gateway/${path}`, {
    method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(response.status === 401
    ? "Check your company code, email, and password, or sign in again."
    : response.status === 429 ? "Too many sign-in attempts. Please try again shortly."
    : "Company sign-in is unavailable. Please try again shortly.");
  return response.json();
}

function acceptGateway(session: GatewaySession) {
  gatewayToken = session.access_token;
  setBearerToken(gatewayToken);
  window.sessionStorage.setItem("aoi-company", session.company_slug);
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    void restoreGateway().then(ok => { if (!ok) window.dispatchEvent(new Event("aoi-session-expired")); });
  }, Math.max(1000, (session.expires_at * 1000 - Date.now()) - 30000));
}

export async function gatewaySignIn(company_slug: string, email: string, password: string) {
  acceptGateway(await companySession("login", { company_slug, email, password }));
}

async function restoreGateway(): Promise<boolean> {
  if (refreshPromise) return refreshPromise;
  const company_slug = window.sessionStorage.getItem("aoi-company");
  if (!company_slug) return false;
  const restore = async () => {
    try { acceptGateway(await companySession("session", { company_slug })); return true; }
    catch { gatewayToken = ""; setBearerToken(""); clearTimeout(refreshTimer); return false; }
  };
  // Refresh tokens are single-use. Serialize refreshes across React effects and
  // same-origin tabs so a legitimate duplicate cannot revoke the session family.
  const pending = (async () => navigator.locks
    ? await navigator.locks.request(`aoi-session-${company_slug}`, restore)
    : await restore())();
  refreshPromise = pending.finally(() => { refreshPromise = null; });
  return refreshPromise;
}

export async function signOut() {
  if (!gatewayAuth) { await identity?.signoutRedirect(); return; }
  clearTimeout(refreshTimer);
  const company_slug = window.sessionStorage.getItem("aoi-company");
  if (company_slug) await companySession("logout", { company_slug });
  gatewayToken = ""; setBearerToken("");
  window.sessionStorage.removeItem("aoi-company");
  window.dispatchEvent(new Event("aoi-session-expired"));
}

export async function copyGatewaySession() {
  if (!gatewayToken) throw new Error("Sign in again before connecting the extension.");
  await navigator.clipboard.writeText(gatewayToken);
}
