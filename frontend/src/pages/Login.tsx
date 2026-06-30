import * as React from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/auth";
import { Button, Card, Input } from "@/components/ui";

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
      setError(err instanceof Error ? err.message : "Ошибка входа");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm p-6">
        <h1 className="mb-1 text-xl font-semibold">DialogScribe</h1>
        <p className="mb-4 text-sm text-slate-500">Вход</p>
        <form onSubmit={submit} className="space-y-3">
          <Input
            placeholder="Пользователь"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
          />
          <Input
            type="password"
            placeholder="Пароль"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
          {error && <div className="text-sm text-red-600">{error}</div>}
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "Вход…" : "Войти"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
