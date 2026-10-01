import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "./AuthContext";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
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
          Log in with the account your super admin gave you
        </p>
        <form onSubmit={handleSubmit}>
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
          <div style={{ marginBottom: 16, position: "relative" }}>
            <input
              type={showPassword ? "text" : "password"}
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              style={{ width: "100%", paddingRight: 36 }}
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              aria-label={showPassword ? "Hide password" : "Show password"}
              aria-pressed={showPassword}
              style={{
                position: "absolute",
                right: 8,
                top: "50%",
                transform: "translateY(-50%)",
                background: "none",
                border: "none",
                padding: 4,
                width: "auto",
                cursor: "pointer",
                color: "#6b7280",
                display: "flex",
                alignItems: "center",
              }}
            >
              {showPassword ? (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path
                    d="M3 3l18 18M10.58 10.58a2 2 0 002.83 2.83M9.88 4.24A9.98 9.98 0 0112 4c5 0 9 4 10 8-.3 1.06-.84 2.13-1.56 3.11M6.6 6.6C4.6 8 3.2 10 2 12c1 4 5 8 10 8 1.5 0 2.9-.36 4.14-.98"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              ) : (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path
                    d="M2 12s4-8 10-8 10 8 10 8-4 8-10 8-10-8-10-8z"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinejoin="round"
                  />
                  <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.6" />
                </svg>
              )}
            </button>
          </div>
          {error && <p style={{ color: "#dc2626", fontSize: 13, marginTop: -8 }}>{error}</p>}
          <button type="submit" disabled={loading} style={{ width: "100%" }}>
            {loading ? "Please wait..." : "Log in"}
          </button>
        </form>
      </div>
    </div>
  );
}
