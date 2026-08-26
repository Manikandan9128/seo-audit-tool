import { useState } from "react";

export interface CompanyOverview {
  company_name: string | null;
  description: string | null;
  products: string[];
  solutions: string[];
  industries: string[];
  kpis: string[];
  registration_info: string | null;
  contact: string | null;
  products_by_category?: Record<string, string[]>;
  target_country?: string | null;
  primary_buyers?: string[];
  daily_users?: string[];
  beneficiaries?: string[];
  target_market?: string | null;
}

function toLines(items: string[] | undefined): string {
  return (items || []).join("\n");
}

function fromLines(text: string): string[] {
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function CompanyOverviewEditor({
  overview,
  onChange,
}: {
  overview: CompanyOverview;
  onChange: (next: CompanyOverview) => void;
}) {
  const [productsText, setProductsText] = useState(toLines(overview.products));
  const [solutionsText, setSolutionsText] = useState(toLines(overview.solutions));
  const [kpisText, setKpisText] = useState(toLines(overview.kpis));
  const [industriesText, setIndustriesText] = useState(toLines(overview.industries));
  const [primaryBuyersText, setPrimaryBuyersText] = useState(toLines(overview.primary_buyers));
  const [dailyUsersText, setDailyUsersText] = useState(toLines(overview.daily_users));
  const [beneficiariesText, setBeneficiariesText] = useState(toLines(overview.beneficiaries));

  function set<K extends keyof CompanyOverview>(key: K, value: CompanyOverview[K]) {
    onChange({ ...overview, [key]: value });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <p style={{ fontSize: 12, color: "var(--text-muted)", margin: 0 }}>
        Review and edit before generating — this is exactly what goes on the "About" and "Products &amp; Services" slides.
      </p>

      <div>
        <label style={{ fontSize: 13, fontWeight: 600 }}>Company name</label>
        <input
          style={{ width: "100%", marginTop: 4 }}
          value={overview.company_name || ""}
          onChange={(e) => set("company_name", e.target.value)}
        />
      </div>

      <div>
        <label style={{ fontSize: 13, fontWeight: 600 }}>About / description</label>
        <textarea
          style={{ width: "100%", marginTop: 4, minHeight: 90, fontFamily: "inherit", fontSize: 13 }}
          value={overview.description || ""}
          onChange={(e) => set("description", e.target.value)}
        />
      </div>

      <div>
        <label style={{ fontSize: 13, fontWeight: 600 }}>Products &amp; services (one per line)</label>
        <textarea
          style={{ width: "100%", marginTop: 4, minHeight: 90, fontFamily: "inherit", fontSize: 13 }}
          value={productsText}
          onChange={(e) => {
            setProductsText(e.target.value);
            set("products", fromLines(e.target.value));
          }}
        />
      </div>

      <div>
        <label style={{ fontSize: 13, fontWeight: 600 }}>Solutions / service areas (one per line)</label>
        <textarea
          style={{ width: "100%", marginTop: 4, minHeight: 70, fontFamily: "inherit", fontSize: 13 }}
          value={solutionsText}
          onChange={(e) => {
            setSolutionsText(e.target.value);
            set("solutions", fromLines(e.target.value));
          }}
        />
      </div>

      <div>
        <label style={{ fontSize: 13, fontWeight: 600 }}>Industries served (one per line)</label>
        <textarea
          style={{ width: "100%", marginTop: 4, minHeight: 50, fontFamily: "inherit", fontSize: 13 }}
          value={industriesText}
          onChange={(e) => {
            setIndustriesText(e.target.value);
            set("industries", fromLines(e.target.value));
          }}
        />
      </div>

      <div>
        <label style={{ fontSize: 13, fontWeight: 600 }}>KPIs (one per line)</label>
        <textarea
          style={{ width: "100%", marginTop: 4, minHeight: 60, fontFamily: "inherit", fontSize: 13 }}
          value={kpisText}
          onChange={(e) => {
            setKpisText(e.target.value);
            set("kpis", fromLines(e.target.value));
          }}
        />
      </div>

      <div style={{ borderTop: "1px solid var(--border)", paddingTop: 14, marginTop: 2 }}>
        <p style={{ fontSize: 12, fontWeight: 700, color: "var(--text-muted)", textTransform: "uppercase", margin: "0 0 10px" }}>
          Ideal Customer Profile
        </p>
        <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 13, fontWeight: 600 }}>Target country</label>
            <input
              placeholder="Eg: Global, Primary USA"
              style={{ width: "100%", marginTop: 4 }}
              value={overview.target_country || ""}
              onChange={(e) => set("target_country", e.target.value)}
            />
          </div>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 13, fontWeight: 600 }}>Target market</label>
            <input
              placeholder="Eg: Mid Market and Enterprise"
              style={{ width: "100%", marginTop: 4 }}
              value={overview.target_market || ""}
              onChange={(e) => set("target_market", e.target.value)}
            />
          </div>
        </div>
        <div>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Primary buyers (one per line)</label>
          <textarea
            placeholder={"Eg: CHRO\nHead of People\nVP of HR\nDirector of HR"}
            style={{ width: "100%", marginTop: 4, minHeight: 50, fontFamily: "inherit", fontSize: 13 }}
            value={primaryBuyersText}
            onChange={(e) => {
              setPrimaryBuyersText(e.target.value);
              set("primary_buyers", fromLines(e.target.value));
            }}
          />
        </div>
        <div style={{ marginTop: 12 }}>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Daily users (one per line)</label>
          <textarea
            placeholder={"Eg: HR teams\nPeople Ops"}
            style={{ width: "100%", marginTop: 4, minHeight: 40, fontFamily: "inherit", fontSize: 13 }}
            value={dailyUsersText}
            onChange={(e) => {
              setDailyUsersText(e.target.value);
              set("daily_users", fromLines(e.target.value));
            }}
          />
        </div>
        <div style={{ marginTop: 12 }}>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Beneficiaries (one per line)</label>
          <textarea
            placeholder={"Eg: Managers\nTeam Leads\nIndividual Contributors\nExecutives"}
            style={{ width: "100%", marginTop: 4, minHeight: 50, fontFamily: "inherit", fontSize: 13 }}
            value={beneficiariesText}
            onChange={(e) => {
              setBeneficiariesText(e.target.value);
              set("beneficiaries", fromLines(e.target.value));
            }}
          />
        </div>
      </div>

      <div style={{ display: "flex", gap: 12 }}>
        <div style={{ flex: 1 }}>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Registration info</label>
          <input
            style={{ width: "100%", marginTop: 4 }}
            value={overview.registration_info || ""}
            onChange={(e) => set("registration_info", e.target.value)}
          />
        </div>
        <div style={{ flex: 1 }}>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Contact</label>
          <input
            style={{ width: "100%", marginTop: 4 }}
            value={overview.contact || ""}
            onChange={(e) => set("contact", e.target.value)}
          />
        </div>
      </div>
    </div>
  );
}
