import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "@/api/client";
import { useAuth } from "@/auth";
import { cn } from "@/lib/utils";
import { AtomBadge, Wordmark } from "./brand";
import { IconRecords, IconUpload, IconCloud, IconBook, IconSettings, IconLogout } from "./icons";

const NAV = [
  { to: "/", label: "Записи", icon: IconRecords, end: true },
  { to: "/upload", label: "Загрузить", icon: IconUpload, end: false },
  { to: "/sources", label: "Источники", icon: IconCloud, end: false },
  { to: "/glossary", label: "Словарь", icon: IconBook, end: false },
  { to: "/settings", label: "Настройки", icon: IconSettings, end: false },
];

export function Layout() {
  const { setUser } = useAuth();
  const nav = useNavigate();
  // Страница конкретного звонка — во всю ширину (волна/реплики), остальное — уже.
  const wide = useLocation().pathname.startsWith("/jobs/");
  async function logout() {
    await api.logout().catch(() => {});
    setUser(null);
    nav("/login");
  }

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-line bg-white/70 backdrop-blur md:flex">
        <NavLink to="/" className="flex items-center gap-2.5 px-5 py-5">
          <AtomBadge size={34} />
          <Wordmark />
        </NavLink>

        <nav className="flex-1 space-y-1 px-3 py-2">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-control px-3 py-2 text-sm transition-colors",
                  isActive
                    ? "bg-coral-soft font-medium text-coral-600"
                    : "text-ink-muted hover:bg-canvas hover:text-ink",
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon size={18} className={isActive ? "text-coral-500" : "text-ink-muted"} />
                  {label}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-line p-3">
          <button
            onClick={logout}
            className="flex w-full items-center justify-center gap-2 rounded-control px-3 py-2 text-sm text-ink-muted transition-colors hover:bg-coral-soft hover:text-coral-500"
          >
            <IconLogout size={16} />
            Выйти
          </button>
        </div>
      </aside>

      {/* Mobile top bar */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex items-center justify-between border-b border-line bg-white/80 px-4 py-3 backdrop-blur md:hidden">
          <NavLink to="/" className="flex items-center gap-2">
            <AtomBadge size={28} />
            <Wordmark />
          </NavLink>
          <div className="flex items-center gap-1">
            <NavLink to="/upload" className="rounded-control p-2 text-ink-muted hover:bg-canvas">
              <IconUpload size={18} />
            </NavLink>
            <NavLink to="/sources" className="rounded-control p-2 text-ink-muted hover:bg-canvas">
              <IconCloud size={18} />
            </NavLink>
            <NavLink to="/glossary" className="rounded-control p-2 text-ink-muted hover:bg-canvas">
              <IconBook size={18} />
            </NavLink>
            <NavLink to="/settings" className="rounded-control p-2 text-ink-muted hover:bg-canvas">
              <IconSettings size={18} />
            </NavLink>
            <button onClick={logout} className="rounded-control p-2 text-ink-muted hover:bg-canvas">
              <IconLogout size={18} />
            </button>
          </div>
        </header>

        <main
          className={cn(
            "mx-auto w-full flex-1 px-4 py-8 md:px-8",
            wide ? "max-w-[1900px]" : "max-w-[1200px]",
          )}
        >
          <Outlet />
        </main>
      </div>
    </div>
  );
}
