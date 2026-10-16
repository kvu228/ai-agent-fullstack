/**
 * Where to go after login: the `?next=` param set by proxy.ts, if it is safe.
 *
 * Only same-site paths are allowed. Otherwise /login?next=https://evil.example would
 * turn our login page into an "open redirect" that phishing links can abuse.
 * ("//evil.example" and "/\evil.example" are protocol-relative URLs to another site.)
 */
export function safeRedirectPath(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) {
    return "/";
  }
  return next;
}
