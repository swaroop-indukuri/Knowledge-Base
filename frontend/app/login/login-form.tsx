"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { warmUpServer } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";

type Mode = "signin" | "signup" | "forgot";

function friendlyError(err: { code?: string; message: string }): string {
  switch (err.code) {
    case "invalid_credentials":
      return "Incorrect email or password.";
    case "email_not_confirmed":
      return "Your email isn't confirmed yet. Check your inbox for the confirmation link.";
    case "user_already_exists":
    case "email_exists":
      return "An account with this email already exists. Try signing in instead.";
    case "weak_password":
      return "That password is too weak. Use at least 6 characters.";
    case "over_email_send_rate_limit":
    case "over_request_rate_limit":
      return "Too many attempts. Please wait a minute and try again.";
    default:
      return err.message;
  }
}

const inputClass = "input";
const primaryButton = "btn-primary w-full";

export default function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const supabase = createClient();

  const [mode, setMode] = useState<Mode>("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(
    searchParams.get("error") ? "Sign-in failed or the link expired. Please try again." : null
  );
  const [info, setInfo] = useState<string | null>(null);
  const [confirmEmail, setConfirmEmail] = useState<string | null>(null);

  // Start waking a sleeping backend now, while the user types their credentials.
  useEffect(() => {
    warmUpServer();
  }, []);

  function switchMode(next: Mode) {
    setMode(next);
    setError(null);
    setInfo(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setInfo(null);

    try {
      if (mode === "signin") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) return setError(friendlyError(error));
        router.push("/");
        router.refresh();
      } else if (mode === "signup") {
        const { data, error } = await supabase.auth.signUp({
          email,
          password,
          options: { emailRedirectTo: `${location.origin}/auth/callback` },
        });
        if (error) return setError(friendlyError(error));
        // With email confirmation on, Supabase hides duplicates by returning a
        // user with no identities instead of an error.
        if (data.user && data.user.identities?.length === 0) {
          return setError(friendlyError({ code: "user_already_exists", message: "" }));
        }
        if (data.session) {
          router.push("/"); // confirmation is off: already signed in
          router.refresh();
        } else {
          setConfirmEmail(email);
        }
      } else {
        const { error } = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: `${location.origin}/auth/callback?next=/reset-password`,
        });
        if (error) return setError(friendlyError(error));
        setInfo("If an account exists for that email, a reset link is on its way.");
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleGoogle() {
    setError(null);
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${location.origin}/auth/callback` },
    });
    if (error) setError(friendlyError(error));
  }

  if (confirmEmail) {
    return (
      <main className="mx-auto mt-24 max-w-sm p-6 text-center">
        <h1 className="text-2xl font-bold">Check your email</h1>
        <p className="mt-3 text-sm text-gray-600 dark:text-gray-400">
          We sent a confirmation link to <strong>{confirmEmail}</strong>. Click it to
          activate your account, then come back and sign in.
        </p>
        <button
          className="mt-6 text-sm text-blue-600 hover:underline dark:text-blue-400"
          onClick={() => {
            setConfirmEmail(null);
            switchMode("signin");
          }}
        >
          Back to sign in
        </button>
      </main>
    );
  }

  const title =
    mode === "signin" ? "Sign in" : mode === "signup" ? "Create account" : "Reset password";

  return (
    <main className="mx-auto mt-24 max-w-sm p-6">
      <h1 className="mb-6 text-2xl font-bold">{title}</h1>

      <form onSubmit={handleSubmit} className="space-y-3">
        <input
          type="email"
          required
          autoComplete="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={inputClass}
        />
        {mode !== "forgot" && (
          <input
            type="password"
            required
            minLength={6}
            autoComplete={mode === "signin" ? "current-password" : "new-password"}
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className={inputClass}
          />
        )}

        {error && (
          <p role="alert" className="alert-error">
            {error}
          </p>
        )}
        {info && (
          <p className="rounded-md bg-green-50 px-3 py-2 text-sm text-green-700 dark:bg-green-950 dark:text-green-300">
            {info}
          </p>
        )}

        <button type="submit" disabled={loading} className={primaryButton}>
          {loading
            ? "Please wait…"
            : mode === "signin"
              ? "Sign in"
              : mode === "signup"
                ? "Sign up"
                : "Send reset link"}
        </button>
      </form>

      {mode === "signin" && (
        <button
          type="button"
          onClick={() => switchMode("forgot")}
          className="mt-3 text-sm text-blue-600 hover:underline dark:text-blue-400"
        >
          Forgot password?
        </button>
      )}

      {mode !== "forgot" && (
        <>
          <div className="my-5 flex items-center gap-3 text-xs text-gray-400">
            <div className="h-px flex-1 bg-gray-200 dark:bg-gray-800" />
            or
            <div className="h-px flex-1 bg-gray-200 dark:bg-gray-800" />
          </div>
          <button
            type="button"
            onClick={handleGoogle}
            className="btn-secondary w-full"
          >
            Continue with Google
          </button>
        </>
      )}

      <p className="mt-6 text-center text-sm text-gray-600 dark:text-gray-400">
        {mode === "signin" ? (
          <>
            No account?{" "}
            <button onClick={() => switchMode("signup")} className="text-blue-600 hover:underline dark:text-blue-400">
              Sign up
            </button>
          </>
        ) : (
          <>
            {mode === "signup" ? "Already have an account?" : "Remembered it?"}{" "}
            <button onClick={() => switchMode("signin")} className="text-blue-600 hover:underline dark:text-blue-400">
              Sign in
            </button>
          </>
        )}
      </p>
    </main>
  );
}
