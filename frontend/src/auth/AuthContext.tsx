import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api/client";

export interface CurrentUser {
  id: string;
  email: string;
  full_name: string;
  role: "super_admin" | "admin" | "member";
}

interface AuthContextValue {
  isAuthenticated: boolean;
  user: CurrentUser | null;
  isAdmin: boolean;
  isSuperAdmin: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(!!localStorage.getItem("access_token"));
  const [user, setUser] = useState<CurrentUser | null>(null);

  useEffect(() => {
    if (!isAuthenticated) {
      setUser(null);
      return;
    }
    api.get("/auth/me").then((res) => setUser(res.data)).catch(() => setUser(null));
  }, [isAuthenticated]);

  async function login(email: string, password: string) {
    const res = await api.post("/auth/login", { email, password });
    localStorage.setItem("access_token", res.data.access_token);
    setIsAuthenticated(true);
  }

  async function register(email: string, password: string, fullName: string) {
    await api.post("/auth/register", { email, password, full_name: fullName });
    await login(email, password);
  }

  function logout() {
    localStorage.removeItem("access_token");
    setIsAuthenticated(false);
    setUser(null);
  }

  return (
    <AuthContext.Provider value={{
        isAuthenticated, user, isAdmin: user?.role === "admin" || user?.role === "super_admin",
        isSuperAdmin: user?.role === "super_admin", login, register, logout,
      }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
