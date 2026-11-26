"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";

// Reached via the emailed link -> /auth/callback (which sets the session)
// -> here. The user is signed in at this point and just picks a new password.
export default function ResetPasswordPage() {
  const router = useRouter();
  const supabase = createClient();

  const [status, setStatus] = useState<"loading" | "ready" | "invalid">("loading");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => setStatus(data.user ? "ready" : "invalid"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) return setError("Passwords don't match.");

    setLoading(true);
    const { error } = await supabase.auth.updateUser({ password });
    setLoading(false);

    if (error) {
      setError(
        error.code === "same_password"
          ? "New password must be different from your old one."
          : error.code === "weak_password"
            ? "That password is too weak. Use at least 6 characters."
            : error.message
      );
      return;
    }
    router.push("/");
    router.refresh();
  }

  if (status === "loading") {
    return <main className="mx-auto mt-24 max-w-sm p-6 text-sm text-gray-500">Loading…</main>;
  }

  if (status === "invalid") {
    return (
      <main className="mx-auto mt-24 max-w-sm p-6 text-center">
        <h1 className="text-2xl font-bold">Link invalid or expired</h1>
        <p className="mt-3 text-sm text-gray-600 dark:text-gray-400">
          Request a new password reset link from the sign-in page.
        </p>
        <Link href="/login" className="mt-6 inline-block text-sm text-blue-600 hover:underline dark:text-blue-400">
          Back to sign in
        </Link>
      </main>
    );
  }

  return (
    <main className="mx-auto mt-24 max-w-sm p-6">
      <h1 className="mb-6 text-2xl font-bold">Set a new password</h1>
      <form onSubmit={handleSubmit} className="space-y-3">
        <input
          type="password"
          required
          minLength={6}
          autoComplete="new-password"
          placeholder="New password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="input"
        />
        <input
          type="password"
          required
          minLength={6}
          autoComplete="new-password"
          placeholder="Confirm new password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          className="input"
        />
        {error && (
          <p role="alert" className="alert-error">
            {error}
          </p>
        )}
        <button type="submit" disabled={loading} className="btn-primary w-full">
          {loading ? "Saving…" : "Update password"}
        </button>
      </form>
    </main>
  );
}
