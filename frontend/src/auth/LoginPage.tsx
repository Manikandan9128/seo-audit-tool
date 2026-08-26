import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "./AuthContext";

export default function LoginPage() {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, password, fullName);
      }
      navigate("/clients");
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 380, margin: "60px auto" }}>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>SEO Audit Tool</h2>
        <p style={{ color: "#6b7280", marginTop: -8, fontSize: 14 }}>
          {mode === "login" ? "Log in to your account" : "Create an account"}
        </p>
        <form onSubmit={handleSubmit}>
          {mode === "register" && (
            <div style={{ marginBottom: 12 }}>
              <input
                placeholder="Full name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                required
                style={{ width: "100%" }}
              />
            </div>
          )}
          <div style={{ marginBottom: 12 }}>
            <input
              type="email"
              placeholder="Email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              style={{ width: "100%" }}
            />
          </div>
          <div style={{ marginBottom: 16 }}>
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              style={{ width: "100%" }}
            />
          </div>
          {error && <p style={{ color: "#dc2626", fontSize: 13, marginTop: -8 }}>{error}</p>}
          <button type="submit" disabled={loading} style={{ width: "100%" }}>
            {loading ? "Please wait..." : mode === "login" ? "Log in" : "Register"}
          </button>
        </form>
        <p
          style={{ marginTop: 16, marginBottom: 0, cursor: "pointer", color: "#2563eb", fontSize: 13 }}
          onClick={() => setMode(mode === "login" ? "register" : "login")}
        >
          {mode === "login" ? "Need an account? Register" : "Already have an account? Log in"}
        </p>
      </div>
    </div>
  );
}
