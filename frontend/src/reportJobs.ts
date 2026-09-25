import { useSyncExternalStore } from "react";
import { api } from "./api/client";

// Report button state per client, kept outside ClientDetailPage so it
// survives the page unmounting (navigating to another screen and back).
// Previously every flag was component useState: leaving the page dropped
// "Generating..." / the download progress while the work kept running, and
// the re-enabled button let a second click start a duplicate report build.
//
// Download: the source of truth is the server's ReportGenerationJob. On
// mount the page calls resumeReportJob, which reads the client's latest job
// (/generate-report/latest), so a build still running after navigation OR
// a browser refresh is picked back up. The server also refuses to start a
// second build while one is active and hands back the existing job id.
//
// Generate: same pattern against the server's ReportPrepJob
// (/report-prep/*). The section checks run server-side and each section's
// result is kept on the job, so the page restores both the button and the
// section previews after navigation or a refresh.

export type DownloadStatus = "idle" | "building" | "downloading" | "failed";

export interface PrepSection {
  status: "pending" | "running" | "done" | "failed";
  data: any;
  error: string | null;
}

export interface ReportJobState {
  generating: boolean;
  hasGenerated: boolean;
  generate: {
    jobId: string | null;
    sections: Record<string, PrepSection>;
    pct: number | null;
    error: string | null;
    errorSeq: number;
  };
  download: {
    status: DownloadStatus;
    jobId: string | null;
    stage: string;
    pct: number | null;
    error: string | null;
    // Bumped on every new error so the page shows it even when the text repeats.
    errorSeq: number;
    issues: string[] | null;
    // Last finished build that hasn't been downloaded in this session —
    // offered as "Download last report" instead of building it again.
    readyJob: { id: string; createdAt: string } | null;
    downloadedSeq: number;
  };
}

const EMPTY: ReportJobState = {
  generating: false,
  hasGenerated: false,
  generate: { jobId: null, sections: {}, pct: null, error: null, errorSeq: 0 },
  download: {
    status: "idle", jobId: null, stage: "", pct: null, error: null, errorSeq: 0,
    issues: null, readyJob: null, downloadedSeq: 0,
  },
};

const states = new Map<string, ReportJobState>();
const listeners = new Set<() => void>();
// Clients with a polling loop already running — one loop per client, no
// matter how many times the page mounts.
const polling = new Set<string>();
const resumed = new Set<string>();
const genPolling = new Set<string>();
const genResumed = new Set<string>();

function get(clientId: string): ReportJobState {
  return states.get(clientId) ?? EMPTY;
}

function set(clientId: string, patch: Partial<Omit<ReportJobState, "download">>, dl?: Partial<ReportJobState["download"]>) {
  const prev = get(clientId);
  states.set(clientId, { ...prev, ...patch, download: { ...prev.download, ...dl } });
  listeners.forEach((l) => l());
}

function setGen(clientId: string, patch: Partial<Omit<ReportJobState, "generate" | "download">>, gen: Partial<ReportJobState["generate"]>) {
  const prev = get(clientId);
  states.set(clientId, { ...prev, ...patch, generate: { ...prev.generate, ...gen } });
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useReportJobState(clientId: string): ReportJobState {
  return useSyncExternalStore(subscribe, () => get(clientId));
}

function isDownloadActive(clientId: string) {
  const s = get(clientId).download.status;
  return s === "building" || s === "downloading";
}

function fail(clientId: string, error: string) {
  set(clientId, {}, { status: "failed", stage: "", pct: null, error, errorSeq: get(clientId).download.errorSeq + 1 });
}

// ---------- Generate ----------

// quietFail: a run that failed before this page load (e.g. yesterday)
// restores the idle button without re-raising its old error banner.
function applyPrepJob(clientId: string, job: any, quietFail = false) {
  const base = { jobId: job.id, sections: job.sections || {}, pct: job.progress_pct ?? null };
  if (job.status === "done") {
    setGen(clientId, { generating: false, hasGenerated: true }, base);
  } else if (job.status === "failed" && quietFail) {
    setGen(clientId, { generating: false }, base);
  } else if (job.status === "failed") {
    setGen(clientId, { generating: false }, {
      ...base, error: job.error || "Generate Report failed", errorSeq: get(clientId).generate.errorSeq + 1,
    });
  } else {
    setGen(clientId, { generating: true }, base);
  }
}

async function pollGenerate(clientId: string, jobId: string) {
  if (genPolling.has(clientId)) return;
  genPolling.add(clientId);
  let failCount = 0;
  try {
    for (;;) {
      let job;
      try {
        job = (await api.get(`/clients/${clientId}/report-prep/${jobId}`)).data;
        failCount = 0;
      } catch (err: any) {
        if (failCount++ < 5) {
          await new Promise((r) => setTimeout(r, 3000));
          continue;
        }
        setGen(clientId, { generating: false }, {
          error: err?.response?.data?.detail || "Lost connection while checking Generate Report — please try again",
          errorSeq: get(clientId).generate.errorSeq + 1,
        });
        return;
      }
      applyPrepJob(clientId, job);
      if (job.status === "done" || job.status === "failed") return;
      await new Promise((r) => setTimeout(r, 2000));
    }
  } finally {
    genPolling.delete(clientId);
  }
}

export async function startGenerate(clientId: string, body: { sections: string[]; analytics_start: string; analytics_end: string }) {
  if (get(clientId).generating) return;
  setGen(clientId, { generating: true }, { error: null });
  try {
    // The server returns the run already in progress (reused: true)
    // instead of starting a second one.
    const res = await api.post(`/clients/${clientId}/report-prep/start`, body);
    await pollGenerate(clientId, res.data.job_id);
  } catch (err: any) {
    setGen(clientId, { generating: false }, {
      error: err?.response?.data?.detail || "Generate Report failed",
      errorSeq: get(clientId).generate.errorSeq + 1,
    });
  }
}

// Called when the page mounts; asks the server once per page load (so after
// a refresh), while in-app navigation reuses the store and its poll loop.
export async function resumeGenerate(clientId: string) {
  if (genResumed.has(clientId) || get(clientId).generating) return;
  genResumed.add(clientId);
  try {
    const job = (await api.get(`/clients/${clientId}/report-prep/latest`)).data;
    if (!job || get(clientId).generating) return;
    applyPrepJob(clientId, job, true);
    if (job.status === "pending" || job.status === "running") pollGenerate(clientId, job.id);
  } catch {
    genResumed.delete(clientId);
  }
}

// ---------- Download ----------

function filenameFrom(disposition: string | undefined, fallback: string) {
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition || "");
  if (star) return decodeURIComponent(star[1]);
  const plain = /filename="?([^";]+)"?/i.exec(disposition || "");
  return plain ? plain[1] : fallback;
}

async function saveJobFile(clientId: string, jobId: string) {
  set(clientId, {}, { status: "downloading", stage: "Downloading…", pct: 100 });
  try {
    const res = await api.get(`/clients/${clientId}/generate-report/${jobId}/download`, { responseType: "blob" });
    const url = window.URL.createObjectURL(new Blob([res.data]));
    const link = document.createElement("a");
    link.href = url;
    link.setAttribute("download", filenameFrom(res.headers["content-disposition"], "seo-audit.pptx"));
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
    set(clientId, {}, {
      status: "idle", stage: "", pct: null, jobId: null, readyJob: null,
      downloadedSeq: get(clientId).download.downloadedSeq + 1,
    });
  } catch (err: any) {
    fail(clientId, err?.response?.data?.detail || "Report download failed");
  }
}

async function poll(clientId: string, jobId: string) {
  if (polling.has(clientId)) return;
  polling.add(clientId);
  let failCount = 0;
  try {
    for (;;) {
      let job;
      try {
        job = (await api.get(`/clients/${clientId}/generate-report/${jobId}`)).data;
        failCount = 0;
      } catch (err: any) {
        // A transient gateway error (502/504 — a deploy restarting the
        // backend, or a brief hiccup) used to kill the loop and leave the
        // button stuck. Retry a few times before giving up.
        if (failCount++ < 5) {
          set(clientId, {}, { stage: "Reconnecting…" });
          await new Promise((r) => setTimeout(r, 3000));
          continue;
        }
        fail(clientId, err?.response?.data?.detail || "Lost connection while checking report status — please try again");
        return;
      }
      if (job.status === "done") {
        // Real per-section AI failures this run — never inside the PPTX
        // itself, shown on the page instead before the file goes anywhere.
        set(clientId, {}, {
          issues: Array.isArray(job.content_generation_issues) && job.content_generation_issues.length
            ? job.content_generation_issues : null,
        });
        await saveJobFile(clientId, jobId);
        return;
      }
      if (job.status === "failed") {
        fail(clientId, job.error || "Report generation failed");
        return;
      }
      set(clientId, {}, {
        status: "building",
        jobId,
        stage: job.status === "running"
          ? job.progress_stage || "Building report… PageSpeed/AI steps can take a couple minutes"
          : "Queued…",
        // A rough per-stage estimate (report_generation_job.py), but a real,
        // increasing signal — never a fake animation.
        pct: typeof job.progress_pct === "number" ? job.progress_pct : get(clientId).download.pct,
      });
      await new Promise((r) => setTimeout(r, 2000));
    }
  } finally {
    polling.delete(clientId);
  }
}

export async function startDownload(clientId: string, body: any) {
  if (isDownloadActive(clientId)) return;
  set(clientId, {}, { status: "building", stage: "Starting report build…", pct: 0, error: null, issues: null, readyJob: null });
  try {
    const res = await api.post(`/clients/${clientId}/generate-report/start`, body);
    // The server returns the already-running build (reused: true) instead
    // of starting a second one.
    set(clientId, {}, { jobId: res.data.job_id });
    await poll(clientId, res.data.job_id);
  } catch (err: any) {
    fail(clientId, err?.response?.data?.detail || "Report generation failed");
  }
}

// Called when the page mounts. After navigation within the app the store
// already knows the state (and its poll loop kept running), so the server
// is only asked once per client per page load — that covers a refresh.
export async function resumeReportJob(clientId: string) {
  if (resumed.has(clientId) || isDownloadActive(clientId)) return;
  resumed.add(clientId);
  try {
    const job = (await api.get(`/clients/${clientId}/generate-report/latest`)).data;
    if (!job || isDownloadActive(clientId)) return;
    if (job.status === "pending" || job.status === "running") {
      set(clientId, {}, { status: "building", jobId: job.id, stage: job.progress_stage || "Queued…", pct: job.progress_pct ?? null });
      poll(clientId, job.id);
    } else if (job.status === "done") {
      set(clientId, {}, { readyJob: { id: job.id, createdAt: job.created_at } });
    }
  } catch {
    // Nothing to resume; the buttons simply start from idle.
    resumed.delete(clientId);
  }
}
