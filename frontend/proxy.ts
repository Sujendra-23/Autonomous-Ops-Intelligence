import { NextResponse, type NextRequest } from "next/server";

// Per-request nonce CSP (Next 16 "proxy", formerly middleware), as in the nginx config this replaces. Next injects inline scripts for hydration,
// so scripts are allowed by nonce ('strict-dynamic' lets those scripts load their chunks).
// style-src-attr allows the style="" attributes that server-rendered React inline styles produce.
export function proxy(request: NextRequest) {
  if (process.env.NODE_ENV !== "production") return NextResponse.next(); // dev needs eval and the cross-origin API

  const nonce = btoa(crypto.randomUUID());
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    "style-src 'self'",
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' data:",
    "connect-src 'self' https: wss:",
    "frame-src https:",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
  ].join("; ");

  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

// Pages only: API calls (proxied or handled) and static assets skip the proxy, so large
// uploads stream straight through to the backend.
export const config = {
  matcher: [{ source: "/((?!api/|_next/static|_next/image|favicon.ico).*)" }],
};
