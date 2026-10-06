import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import { setBearerToken } from "./api";

const authority = import.meta.env.VITE_AUTH_AUTHORITY as string | undefined;
const clientId = import.meta.env.VITE_AUTH_CLIENT_ID as string | undefined;
export const authConfigured = Boolean(authority && clientId);
export const identity = authConfigured ? new UserManager({
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
  if (!identity) return false;
  const user = window.location.pathname === "/auth/callback"
    ? await identity.signinRedirectCallback() : await identity.getUser();
  if (window.location.pathname === "/auth/callback") window.history.replaceState({}, "", "/");
  if (!user || user.expired) return false;
  setBearerToken(user.access_token);
  return true;
}
