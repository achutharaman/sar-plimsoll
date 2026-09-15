import { useState, type FormEvent } from "react";
import { useAuth } from "../auth";
import { Button, Notice } from "../components/ui";

export function SignIn() {
  const { config, signIn, error: configError } = useAuth();
  const dev = config?.auth_mode === "dev";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email.trim(), password);
    } catch {
      setError("Sign-in failed. Check the email and password.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center px-4 py-10">
      <Brand />
      <p className="mt-2 text-sm text-ink-2">
        Code review with a deterministic 1–10 score. Every point lost traces to a finding.
      </p>
      <form onSubmit={submit} className="mt-6 space-y-3 rounded-xl border border-line bg-surface p-5">
        {configError && <Notice>{configError}</Notice>}
        <label className="block text-sm">
          <span className="text-ink-2">{dev ? "Dev user (uid or uid:admin)" : "Email"}</span>
          <input
            className="mt-1 h-10 w-full rounded-lg border border-line bg-page px-3 text-ink"
            type={dev ? "text" : "email"}
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        {!dev && (
          <label className="block text-sm">
            <span className="text-ink-2">Password</span>
            <input
              className="mt-1 h-10 w-full rounded-lg border border-line bg-page px-3 text-ink"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
        )}
        {error && <Notice>{error}</Notice>}
        <Button type="submit" disabled={busy} className="w-full">
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </main>
  );
}

export function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
        <circle cx="16" cy="16" r="11" fill="none" stroke="var(--accent)" strokeWidth="3" />
        <path d="M3 16h26" stroke="var(--accent)" strokeWidth="3" />
      </svg>
      <span className="text-lg font-semibold tracking-tight text-ink">Plimsoll</span>
    </div>
  );
}
