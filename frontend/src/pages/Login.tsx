import * as React from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/auth";
import { Button, Card, Input, ErrorCard } from "@/components/ui";
import { AtomBadge } from "@/components/brand";

export default function Login() {
  const { setUser } = useAuth();
  const nav = useNavigate();
  const [username, setUsername] = React.useState("admin");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.login(username, password);
      setUser(r.user);
      nav("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось войти");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="w-full max-w-sm animate-fade-up">
        <div className="mb-8 flex flex-col items-center text-center">
          <AtomBadge size={56} className="mb-4" />
          <h1 className="text-2xl font-semibold tracking-tightest text-ink">BloodAgents</h1>
          <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.16em] text-ink-muted">
            Транскрибация
          </p>
          <p className="mt-4 text-[15px] leading-snug text-ink-muted">
            Расшифровка созвонов с именами участников.
            <br />
            Каждый голос — на своём месте.
          </p>
        </div>

        <Card className="p-6">
          <form onSubmit={submit} className="space-y-3">
            <Input
              placeholder="Пользователь"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              aria-label="Пользователь"
            />
            <Input
              type="password"
              placeholder="Пароль"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              aria-label="Пароль"
            />
            {error && <ErrorCard title={error} />}
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? "Входим…" : "Войти"}
            </Button>
          </form>
        </Card>
      </div>
    </div>
  );
}
