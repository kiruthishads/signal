import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";
import { API_BASE } from "@/lib/api-base";

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url);

  const code = searchParams.get("code");
  const next = searchParams.get("next") ?? "/auth/syncing";
  const error = searchParams.get("error");
  const errorDescription = searchParams.get("error_description");

  if (error) {
    const params = new URLSearchParams({
      error,
      reason: errorDescription ?? "",
    });

    return NextResponse.redirect(`${origin}/login?${params}`);
  }

  if (!code) {
    return NextResponse.redirect(
      `${origin}/login?error=missing_code`
    );
  }

  const cookieStore = await cookies();

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll: () => cookieStore.getAll(),
        setAll: (cookiesToSet) => {
          cookiesToSet.forEach(({ name, value, options }) =>
            cookieStore.set(name, value, options)
          );
        },
      },
    }
  );

  const {
    data: exchangeData,
    error: exchangeError,
  } = await supabase.auth.exchangeCodeForSession(code);

  if (exchangeError) {
    console.error(
      "[auth/callback] exchange error:",
      exchangeError.message
    );

    return NextResponse.redirect(
      `${origin}/login?error=exchange_failed`
    );
  }

  const session = exchangeData.session;

  if (session?.provider_token) {
    try {
      const response = await fetch(`${API_BASE}/auth/sync`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          provider_token: session.provider_token,
        }),
        cache: "no-store",
      });

      if (!response.ok) {
        // Status only: a 422 validation body can echo the request, which holds the GitHub token.
        console.error(
          "[auth/callback] backend sync failed:",
          response.status
        );
      }
    } catch (err) {
      console.error("[auth/callback] backend sync failed:", err);
    }
  } else {
    console.warn(
      "[auth/callback] no provider_token in exchange response"
    );
  }

  return NextResponse.redirect(`${origin}${next}`);
}