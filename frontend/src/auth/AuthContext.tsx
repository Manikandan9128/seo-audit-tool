import { createContext, useContext, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api/client";

interface AuthContextValue {
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthenticated, setIsAuthenticated] = useState(!!localStorage.getItem("access_token"));

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
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
