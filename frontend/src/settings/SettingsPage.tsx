import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api/client";

interface ApiKeyCardProps {
  title: string;
  description: ReactNode;
  keySet: boolean;
  masked: string | null;
  loading: boolean;
  saveUrl: string;
  testUrl: string;
  saveField: string;
  onSaved: (setFlag: boolean, masked: string | null) => void;
}

function ApiKeyCard({ title, description, keySet, masked, loading, saveUrl, testUrl, saveField, onSaved }: ApiKeyCardProps) {
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);

  async function save() {
    if (!value.trim()) return;
    setSaving(true);
    setError("");
    setMsg("");
    setTestResult(null);
    try {
      const res = await api.put(saveUrl, { [saveField]: value });
      onSaved(true, res.data[`${saveField}_masked`]);
      setValue("");
      setMsg("Saved — takes effect immediately, no restart needed.");
      setTestResult({ ok: res.data.test_ok, message: res.data.test_message });
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Couldn't save the key");
    } finally {
      setSaving(false);
    }
  }

  async function retest() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api.post(testUrl);
      setTestResult({ ok: res.data.test_ok, message: res.data.test_message });
    } catch (err: any) {
      setTestResult({ ok: false, message: err?.response?.data?.detail || "Test failed" });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="card">
      <h3 style={{ margin: 0, fontSize: 18 }}>{title}</h3>
      <p style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 6 }}>{description}</p>

      {loading ? (
        <p style={{ fontSize: 13 }}>Loading...</p>
      ) : (
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <p style={{ fontSize: 13, margin: 0 }}>
            Current: {keySet ? <code>{masked}</code> : <span style={{ color: "var(--text-muted)" }}>not set</span>}
          </p>
          {keySet && (
            <button className="secondary" onClick={retest} disabled={testing}>
              {testing ? "Testing..." : "Test key"}
            </button>
          )}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <input
          type="password"
          placeholder={`Paste new ${title.replace(" API Key", "")} API key`}
          style={{ flex: 1 }}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <button onClick={save} disabled={saving || !value.trim()}>
          {saving ? "Saving..." : "Save"}
        </button>
      </div>

      {msg && <p style={{ fontSize: 13, color: "var(--success)", marginTop: 8 }}>{msg}</p>}
      {error && <p style={{ fontSize: 13, color: "#991b1b", marginTop: 8 }}>{error}</p>}
      {testResult && (
        <p style={{ fontSize: 13, color: testResult.ok ? "var(--success)" : "#991b1b", marginTop: 8 }}>
          {testResult.ok ? "✓ " : "✗ "}
          {testResult.message}
        </p>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const [geminiSet, setGeminiSet] = useState(false);
  const [geminiMasked, setGeminiMasked] = useState<string | null>(null);
  const [xaiSet, setXaiSet] = useState(false);
  const [xaiMasked, setXaiMasked] = useState<string | null>(null);
  const [claudeSet, setClaudeSet] = useState(false);
  const [claudeMasked, setClaudeMasked] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const res = await api.get("/settings");
      setGeminiSet(res.data.gemini_api_key_set);
      setGeminiMasked(res.data.gemini_api_key_masked);
      setXaiSet(res.data.xai_api_key_set);
      setXaiMasked(res.data.xai_api_key_masked);
      setClaudeSet(res.data.claude_api_key_set);
      setClaudeMasked(res.data.claude_api_key_masked);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Couldn't load settings");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const eitherKeySet = geminiSet || xaiSet || claudeSet;

  return (
    <div style={{ maxWidth: 600, margin: "0 auto" }}>
      <h2 style={{ marginBottom: 8 }}>Settings</h2>
      <p style={{ color: "var(--text-muted)", fontSize: 13, marginBottom: 20 }}>
        Company Overview extraction and the Competitor Analysis AI summary need at least one of these keys — not
        all three. xAI is tried first (fast-recovering per-minute limit), then Gemini (its free-tier quota resets
        only once a day, so it's kept in reserve), then Claude last as a paid fallback. Any one key alone is enough.{" "}
        {!loading && (eitherKeySet ? <span style={{ color: "var(--success)" }}>✓ AI features are active.</span> : <span style={{ color: "#991b1b" }}>No key set yet — AI features are disabled.</span>)}
      </p>

      {error && <p style={{ fontSize: 13, color: "#991b1b", marginBottom: 12 }}>{error}</p>}

      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <ApiKeyCard
          title="Gemini API Key"
          description={
            <>
              Get a key at{" "}
              <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer">
                aistudio.google.com/apikey
              </a>
              .
            </>
          }
          keySet={geminiSet}
          masked={geminiMasked}
          loading={loading}
          saveUrl="/settings/gemini-api-key"
          testUrl="/settings/gemini-api-key/test"
          saveField="gemini_api_key"
          onSaved={(set, masked) => {
            setGeminiSet(set);
            setGeminiMasked(masked);
          }}
        />

        <ApiKeyCard
          title="xAI (Grok) API Key"
          description={
            <>
              Get a key at{" "}
              <a href="https://console.x.ai" target="_blank" rel="noreferrer">
                console.x.ai
              </a>
              .
            </>
          }
          keySet={xaiSet}
          masked={xaiMasked}
          loading={loading}
          saveUrl="/settings/xai-api-key"
          testUrl="/settings/xai-api-key/test"
          saveField="xai_api_key"
          onSaved={(set, masked) => {
            setXaiSet(set);
            setXaiMasked(masked);
          }}
        />

        <ApiKeyCard
          title="Claude API Key"
          description={
            <>
              Get a key at{" "}
              <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer">
                console.anthropic.com/settings/keys
              </a>
              .
            </>
          }
          keySet={claudeSet}
          masked={claudeMasked}
          loading={loading}
          saveUrl="/settings/claude-api-key"
          testUrl="/settings/claude-api-key/test"
          saveField="claude_api_key"
          onSaved={(set, masked) => {
            setClaudeSet(set);
            setClaudeMasked(masked);
          }}
        />
      </div>
    </div>
  );
}
