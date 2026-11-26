import { Suspense } from "react";
import LoginForm from "./login-form";

export default function LoginPage() {
  // Suspense is required because LoginForm reads useSearchParams().
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
