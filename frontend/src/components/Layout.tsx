import { Link, Outlet, useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/auth";
import { Button } from "./ui";

export function Layout() {
  const { user, setUser } = useAuth();
  const nav = useNavigate();
  async function logout() {
    await api.logout().catch(() => {});
    setUser(null);
    nav("/login");
  }
  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-4">
            <Link to="/" className="text-lg font-semibold">
              DialogScribe
            </Link>
            <Link to="/upload" className="text-sm text-slate-600 hover:text-slate-900">
              Загрузить
            </Link>
          </div>
          <div className="flex items-center gap-3 text-sm text-slate-500">
            <span>{user}</span>
            <Button variant="outline" onClick={logout}>
              Выйти
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  );
}
