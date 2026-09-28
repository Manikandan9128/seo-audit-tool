import axios from "axios";

import { selectionHeaders } from "../aiProvider";

// In dev (Vite on :5173) the API lives on :8000 of the same host.
// When served from the backend itself (production build, or via a single
// ngrok tunnel), the API is same-origin under /api.
const isViteDev = window.location.port === "5173";

export const api = axios.create({
  baseURL: isViteDev ? `http://${window.location.hostname}:8001/api` : "/api",
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  // The Report AI Provider rides on every request, so any backend AI step
  // (uploads, analysis, Generate/Preview/Download) uses exactly it.
  for (const [name, value] of Object.entries(selectionHeaders())) {
    config.headers[name] = value;
  }
  return config;
});

// A 401 means the token is gone/expired — bounce to login instead of
// leaving the page stuck silently re-issuing the same failing request
// (e.g. polling loops during a backend restart).
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !error.config?.url?.startsWith("/auth/")) {
      localStorage.removeItem("access_token");
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);
