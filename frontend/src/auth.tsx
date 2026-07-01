import * as React from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, setUnauthorizedHandler } from "@/api/client";

interface AuthCtx {
  user: string | null;
  loading: boolean;
  setUser: (u: string | null) => void;
}

const Ctx = React.createContext<AuthCtx>({ user: null, loading: true, setUser: () => {} });

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    // истёкшая сессия (401 на любом запросе) → выкидываем на /login
    setUnauthorizedHandler(() => setUser(null));
    api
      .me()
      .then((r) => setUser(r.user))
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
    return () => setUnauthorizedHandler(null);
  }, []);

  return <Ctx.Provider value={{ user, loading, setUser }}>{children}</Ctx.Provider>;
}

export const useAuth = () => React.useContext(Ctx);

export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="p-8 text-slate-500">Загрузка…</div>;
  if (!user) return <Navigate to="/login" state={{ from: loc }} replace />;
  return <>{children}</>;
}
