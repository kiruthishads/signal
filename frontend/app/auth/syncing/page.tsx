"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase";
import { motion } from "framer-motion";

export default function SyncingPage() {
  const router = useRouter();
  const called = useRef(false);

  useEffect(() => {
    if (called.current) return;
    called.current = true;

    (async () => {
      try {
        const supabase = createClient();

        const {
          data: { session },
        } = await supabase.auth.getSession();

        if (!session) {
          router.replace("/login?error=no_session");
          return;
        }

        const apiBase = `${
          (
            process.env.NEXT_PUBLIC_API_URL ??
            "http://localhost:8000"
          ).replace(/\/+$/, "")
        }/v1`;

        const response = await fetch(`${apiBase}/auth/sync`, {
          method: "POST",
          headers: {
            Authorization: `Bearer ${session.access_token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            provider_token: session.provider_token ?? null,
          }),
          cache: "no-store",
        });

        if (!response.ok) {
          console.error(
            "[auth/syncing] sync failed:",
            response.status,
            await response.text()
          );
        }
      } catch (error) {
        console.error("[auth/syncing] error:", error);
      } finally {
        router.replace("/dashboard");
      }
    })();
  }, [router]);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-4">
      <motion.div
        className="w-8 h-8 rounded-full border-2 border-signal border-t-transparent"
        animate={{ rotate: 360 }}
        transition={{
          repeat: Infinity,
          duration: 0.8,
          ease: "linear",
        }}
      />
      <p className="text-sm text-muted-foreground">
        Setting up your profile…
      </p>
    </div>
  );
}