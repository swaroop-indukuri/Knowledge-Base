import { createClient } from "@/lib/supabase/server";
import SignOutButton from "./sign-out-button";

export default async function Header() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user) return null;

  // Google sign-in populates full_name/name in user_metadata; email users don't have it.
  const meta = user.user_metadata ?? {};
  const displayName: string = meta.full_name || meta.name || user.email || "Signed in";

  return (
    <header className="flex items-center justify-between border-b border-gray-200 bg-white px-6 py-3 dark:border-gray-800 dark:bg-gray-900">
      <span className="font-semibold">Knowledge Management Demo</span>
      <div className="flex items-center gap-3">
        <span className="truncate text-sm text-gray-600 dark:text-gray-400">{displayName}</span>
        <SignOutButton />
      </div>
    </header>
  );
}
