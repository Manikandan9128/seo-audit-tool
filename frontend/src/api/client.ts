import axios from "axios";

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
  return config;
});
