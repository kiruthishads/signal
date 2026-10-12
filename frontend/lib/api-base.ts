/**
 * Versioned backend base URL, e.g. "https://api.example.com/v1".
 *
 * NEXT_PUBLIC_API_URL may be set with or without a trailing "/v1"; both
 * resolve to the same base so every caller hits the versioned routes.
 */
export function resolveApiBase(raw: string | undefined): string {
  const origin = (raw ?? "http://localhost:8000")
    .trim()
    .replace(/\/+$/, "")
    .replace(/\/v1$/, "");
  return `${origin}/v1`;
}

export const API_BASE = resolveApiBase(process.env.NEXT_PUBLIC_API_URL);
