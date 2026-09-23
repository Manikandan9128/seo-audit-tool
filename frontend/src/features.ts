// Semrush MCP as a report data source is paused (2026-09-23): every
// report runs on manually uploaded Semrush data while the team confirms
// the Semrush account's API-unit budget. Flip to true to bring back the
// Generate Report source popup, the "Fetch via Claude (Semrush MCP)"
// buttons, and the Settings connection card — all of that code is kept.
export const SEMRUSH_MCP_ENABLED = false;
