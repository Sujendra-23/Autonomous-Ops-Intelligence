import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import { setBearerToken } from "./api";

// Literal process.env.NEXT_PUBLIC_* reads are required: Next inlines them at build time.
const authority = process.env.NEXT_PUBLIC_AUTH_AUTHORITY;
const clientId = process.env.NEXT_PUBLIC_AUTH_CLIENT_ID;
const audience = process.env.NEXT_PUBLIC_AUTH_AUDIENCE;
export const authConfigured = Boolean(authority && clientId);

// The OIDC client touches window and sessionStorage, so it is built on first use in the browser
// rather than at import time (client components are also rendered on the server).
let manager: UserManager | null | undefined;
export function getIdentity(): UserManager | null {
  if (!authConfigured || typeof window === "undefined") return null;
  if (manager === undefined) {
    manager = new UserManager({
      authority: authority!, client_id: clientId!,
      redirect_uri: `${window.location.origin}/auth/callback`,
      post_logout_redirect_uri: window.location.origin,
      response_type: "code", scope: "openid profile email",
      resource: audience,
      userStore: new WebStorageStateStore({ store: window.sessionStorage }),
      automaticSilentRenew: false,
      extraQueryParams: audience ? { audience } : undefined,
    });
    manager.events.addUserLoaded(user => setBearerToken(user.access_token));
    manager.events.addAccessTokenExpired(() => { setBearerToken(""); window.dispatchEvent(new Event("aoi-session-expired")); });
    manager.events.addUserUnloaded(() => { setBearerToken(""); window.dispatchEvent(new Event("aoi-session-expired")); });
  }
  return manager;
}

export async function restoreIdentity() {
  const identity = getIdentity();
  if (!identity) return false;
  const user = window.location.pathname === "/auth/callback"
    ? await identity.signinRedirectCallback() : await identity.getUser();
  if (window.location.pathname === "/auth/callback") window.history.replaceState({}, "", "/");
  if (!user || user.expired) return false;
  setBearerToken(user.access_token);
  return true;
}
