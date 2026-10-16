import { NextResponse, type NextRequest } from "next/server";

/**
 * Runs before every page request (see `matcher`), on the server.
 *
 * It only checks whether a session cookie EXISTS: an optimistic, cheap check that
 * avoids flashing a protected page to logged-out users. The real verification
 * (signature, expiry, revocation) happens in the backend on every API call.
 */

const PUBLIC_PATHS = new Set(["/login", "/register"]);

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  // The access cookie expires after 15 min, the refresh cookie lives for days:
  // either one means "probably logged in" (the client refreshes when needed).
  const hasSession = request.cookies.has("access_token") || request.cookies.has("refresh_token");
  const isPublic = PUBLIC_PATHS.has(pathname);

  if (!hasSession && !isPublic) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", pathname + search); // come back here after login
    return NextResponse.redirect(loginUrl);
  }
  if (hasSession && isPublic) {
    return NextResponse.redirect(new URL("/", request.url));
  }
  return NextResponse.next();
}

export const config = {
  // Skip API calls (proxied to FastAPI, which does its own auth), Next internals
  // and static files (anything with a dot, e.g. favicon.ico).
  matcher: ["/((?!api/|_next/static|_next/image|.*\\..*).*)"],
};
