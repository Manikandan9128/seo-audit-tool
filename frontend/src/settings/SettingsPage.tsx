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
  multiline?: boolean;
}

function ApiKeyCard({ title, description, keySet, masked, loading, saveUrl, testUrl, saveField, onSaved, multiline }: ApiKeyCardProps) {
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

      <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: multiline ? "flex-start" : "center" }}>
        {multiline ? (
          <textarea
            placeholder={`Paste the full ${title} JSON here`}
            style={{ flex: 1, minHeight: 90, fontFamily: "monospace", fontSize: 12 }}
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        ) : (
          <input
            type="password"
            placeholder={`Paste new ${title.replace(" API Key", "")} API key`}
            style={{ flex: 1 }}
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        )}
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

function SheetsOAuthCard({
  email,
  clientId,
  loading,
  onChanged,
}: {
  email: string | null;
  clientId: string | null;
  loading: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [error, setError] = useState("");
  const [clientIdInput, setClientIdInput] = useState("");
  const [clientSecretInput, setClientSecretInput] = useState("");
  const [savingClient, setSavingClient] = useState(false);

  const redirectUri = `${window.location.origin}/api/settings/google-sheets-oauth/callback`;

  async function saveClient() {
    if (!clientIdInput.trim() || !clientSecretInput.trim()) return;
    setSavingClient(true);
    setError("");
    try {
      await api.put("/settings/google-sheets-oauth-client", {
        google_sheets_oauth_client_id: clientIdInput.trim(),
        google_sheets_oauth_client_secret: clientSecretInput.trim(),
      });
      setClientIdInput("");
      setClientSecretInput("");
      onChanged();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Couldn't save the OAuth client");
    } finally {
      setSavingClient(false);
    }
  }

  async function connect() {
    setBusy(true);
    setError("");
    try {
      const res = await api.get("/settings/google-sheets-oauth/connect");
      window.location.href = res.data.auth_url;
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Couldn't start Google connect");
      setBusy(false);
    }
  }

  async function disconnect() {
    if (!confirm("Disconnect this Google account? Competitor keyword Sheets will stop generating until reconnected or a service account is configured instead.")) return;
    setBusy(true);
    setError("");
    try {
      await api.post("/settings/google-sheets-oauth/disconnect");
      onChanged();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Couldn't disconnect");
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    setBusy(true);
    setTestResult(null);
    try {
      const res = await api.post("/settings/google-sheets-oauth/test");
      setTestResult({ ok: res.data.test_ok, message: res.data.test_message });
    } catch (err: any) {
      setTestResult({ ok: false, message: err?.response?.data?.detail || "Test failed" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h3 style={{ margin: 0, fontSize: 18 }}>Google Account for Sheets (recommended)</h3>
      <p style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 6 }}>
        Connect your own Google account to create competitor keyword Sheets directly under it — no service account,
        no shared folder, no storage-quota edge cases. This is the simplest path, especially with a plain personal
        Gmail account (a bare service account has no Drive storage of its own and can fail even when set up
        correctly).
      </p>

      {!clientId && !loading && (
        <div style={{ background: "var(--card-bg-alt, #f7f7f7)", border: "1px solid var(--card-border, #e5e5e5)", borderRadius: 8, padding: 12, marginTop: 10 }}>
          <p style={{ fontSize: 13, margin: 0, fontWeight: 600 }}>One-time setup: create a Web application OAuth client</p>
          <ol style={{ fontSize: 13, margin: "6px 0 10px 18px", padding: 0 }}>
            <li>Google Cloud Console → APIs & Services → Credentials → Create Credentials → OAuth client ID</li>
            <li>Application type: <strong>Web application</strong></li>
            <li>
              Authorized redirect URI — paste exactly:
              <br />
              <code style={{ wordBreak: "break-all" }}>{redirectUri}</code>
            </li>
            <li>Create → copy the Client ID and Client Secret it gives you, paste below</li>
          </ol>
          <input
            type="text"
            placeholder="Client ID"
            style={{ width: "100%", marginBottom: 6 }}
            value={clientIdInput}
            onChange={(e) => setClientIdInput(e.target.value)}
          />
          <div style={{ display: "flex", gap: 8 }}>
            <input
              type="password"
              placeholder="Client Secret"
              style={{ flex: 1 }}
              value={clientSecretInput}
              onChange={(e) => setClientSecretInput(e.target.value)}
            />
            <button onClick={saveClient} disabled={savingClient || !clientIdInput.trim() || !clientSecretInput.trim()}>
              {savingClient ? "Saving..." : "Save"}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <p style={{ fontSize: 13 }}>Loading...</p>
      ) : (
        <p style={{ fontSize: 13, margin: "10px 0 0" }}>
          Current:{" "}
          {email ? <code>{email}</code> : <span style={{ color: "var(--text-muted)" }}>not connected</span>}
        </p>
      )}

      {clientId && (
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button onClick={connect} disabled={busy}>
            {email ? "Reconnect" : "Connect Google Account"}
          </button>
          {email && (
            <>
              <button className="secondary" onClick={test} disabled={busy}>
                Test key
              </button>
              <button className="secondary" onClick={disconnect} disabled={busy}>
                Disconnect
              </button>
            </>
          )}
        </div>
      )}
      {error && <p style={{ fontSize: 13, color: "#991b1b", marginTop: 8 }}>{error}</p>}
      {testResult && (
        <p style={{ fontSize: 13, marginTop: 8, color: testResult.ok ? "var(--success)" : "#991b1b" }}>
          {testResult.ok ? "✓ " : "✗ "}
          {testResult.message}
        </p>
      )}
    </div>
  );
}

function DriveFolderCard({
  value,
  loading,
  onSaved,
}: {
  value: string | null;
  loading: boolean;
  onSaved: (folderId: string | null, testResult: { ok: boolean; message: string }) => void;
}) {
  const [input, setInput] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    if (!input.trim()) return;
    setSaving(true);
    setError("");
    try {
      const res = await api.put("/settings/google-drive-folder-id", { google_drive_folder_id: input.trim() });
      onSaved(res.data.google_drive_folder_id, { ok: res.data.test_ok, message: res.data.test_message });
      setInput("");
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Couldn't save the folder ID");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card">
      <h3 style={{ margin: 0, fontSize: 18 }}>Google Drive Folder ID</h3>
      <p style={{ color: "var(--text-muted)", fontSize: 13, marginTop: 6 }}>
        A bare service account has no Drive storage of its own, so it can't create Sheets directly. Create a folder
        in your own Google Drive, share it with the service account's email above (Editor access), then paste the
        folder's ID here — it's the part of the folder's URL after <code>/folders/</code>.
      </p>

      {loading ? (
        <p style={{ fontSize: 13 }}>Loading...</p>
      ) : (
        <p style={{ fontSize: 13, margin: 0 }}>
          Current: {value ? <code>{value}</code> : <span style={{ color: "var(--text-muted)" }}>not set</span>}
        </p>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <input
          type="text"
          placeholder="Paste the Drive folder ID"
          style={{ flex: 1 }}
          value={input}
          onChange={(e) => setInput(e.target.value)}
        />
        <button onClick={save} disabled={saving || !input.trim()}>
          {saving ? "Saving..." : "Save"}
        </button>
      </div>
      {error && <p style={{ fontSize: 13, color: "#991b1b", marginTop: 8 }}>{error}</p>}
    </div>
  );
}

export default function SettingsPage() {
  const [geminiSet, setGeminiSet] = useState(false);
  const [geminiMasked, setGeminiMasked] = useState<string | null>(null);
  const [groqSet, setGroqSet] = useState(false);
  const [groqMasked, setGroqMasked] = useState<string | null>(null);
  const [claudeSet, setClaudeSet] = useState(false);
  const [claudeMasked, setClaudeMasked] = useState<string | null>(null);
  const [gsaSet, setGsaSet] = useState(false);
  const [gsaMasked, setGsaMasked] = useState<string | null>(null);
  const [driveFolderId, setDriveFolderId] = useState<string | null>(null);
  const [folderTestResult, setFolderTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [sheetsOauthEmail, setSheetsOauthEmail] = useState<string | null>(null);
  const [sheetsOauthClientId, setSheetsOauthClientId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    setError("");
    try {
      const res = await api.get("/settings");
      setGeminiSet(res.data.gemini_api_key_set);
      setGeminiMasked(res.data.gemini_api_key_masked);
      setGroqSet(res.data.groq_api_key_set);
      setGroqMasked(res.data.groq_api_key_masked);
      setClaudeSet(res.data.claude_api_key_set);
      setClaudeMasked(res.data.claude_api_key_masked);
      setGsaSet(res.data.google_service_account_json_set);
      setGsaMasked(res.data.google_service_account_json_masked);
      setDriveFolderId(res.data.google_drive_folder_id);
      setSheetsOauthEmail(res.data.google_sheets_oauth_email);
      setSheetsOauthClientId(res.data.google_sheets_oauth_client_id);
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Couldn't load settings");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  const eitherKeySet = geminiSet || groqSet || claudeSet;

  return (
    <div style={{ maxWidth: 600, margin: "0 auto" }}>
      <h2 style={{ marginBottom: 8 }}>Settings</h2>
      <p style={{ color: "var(--text-muted)", fontSize: 13, marginBottom: 20 }}>
        Company Overview extraction and the Competitor Analysis AI summary need at least one of these keys — not
        all three. Groq is tried first (fast-recovering per-minute limit), then Gemini (its free-tier quota resets
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
          title="Groq API Key"
          description={
            <>
              Get a key at{" "}
              <a href="https://console.groq.com/keys" target="_blank" rel="noreferrer">
                console.groq.com/keys
              </a>
              . Free tier, much higher per-minute limit than Gemini's.
            </>
          }
          keySet={groqSet}
          masked={groqMasked}
          loading={loading}
          saveUrl="/settings/groq-api-key"
          testUrl="/settings/groq-api-key/test"
          saveField="groq_api_key"
          onSaved={(set, masked) => {
            setGroqSet(set);
            setGroqMasked(masked);
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

        <SheetsOAuthCard email={sheetsOauthEmail} clientId={sheetsOauthClientId} loading={loading} onChanged={load} />

        <p style={{ fontSize: 13, color: "var(--text-muted)", marginTop: -8 }}>
          Advanced / Google Workspace domains only — skip this if you connected your Google account above:
        </p>

        <ApiKeyCard
          title="Google Service Account JSON"
          description={
            <>
              Enables full (uncapped) competitor keyword lists as linked Google Sheets in the report, instead of a
              table capped at ~14 rows. Create one at{" "}
              <a href="https://console.cloud.google.com/iam-admin/serviceaccounts" target="_blank" rel="noreferrer">
                console.cloud.google.com
              </a>{" "}
              with the Sheets API and Drive API enabled, then paste the downloaded key's JSON content here.
            </>
          }
          keySet={gsaSet}
          masked={gsaMasked}
          loading={loading}
          saveUrl="/settings/google-service-account-json"
          testUrl="/settings/google-service-account-json/test"
          saveField="google_service_account_json"
          multiline
          onSaved={(set, masked) => {
            setGsaSet(set);
            setGsaMasked(masked);
          }}
        />

        <DriveFolderCard
          value={driveFolderId}
          loading={loading}
          onSaved={(folderId, test) => {
            setDriveFolderId(folderId);
            setFolderTestResult(test);
          }}
        />
        {folderTestResult && (
          <p style={{ fontSize: 13, marginTop: -8, color: folderTestResult.ok ? "var(--success)" : "#991b1b" }}>
            {folderTestResult.ok ? "✓ " : "✗ "}
            {folderTestResult.message}
          </p>
        )}
      </div>
    </div>
  );
}
