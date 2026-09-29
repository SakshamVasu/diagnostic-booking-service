// Thin client for the Diagnostic Booking REST API (same origin).

const TOKEN_KEY = "dbs.token";

export const session = {
  get token() {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set token(value) {
    try {
      if (value) localStorage.setItem(TOKEN_KEY, value);
      else localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* storage unavailable: the session lasts until reload */
    }
  },
  clear() {
    this.token = null;
  },
};

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function formatDetail(detail, fallback) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((error) => {
        const field = (error.loc || []).filter((part) => part !== "body" && part !== "query").join(".");
        return field ? `${field}: ${error.msg}` : error.msg;
      })
      .join("\n");
  }
  return fallback;
}

export async function api(path, { method = "GET", body, query } = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }

  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = session.token;
  if (token) headers.Authorization = `Bearer ${token}`;

  let response;
  try {
    response = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "Can't reach the server. Is the API running?");
  }

  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && token) {
      session.clear();
      window.dispatchEvent(new CustomEvent("auth:expired"));
    }
    throw new ApiError(response.status, formatDetail(data?.detail, `Request failed (${response.status})`));
  }
  return data;
}

// Small read-through cache for names shown on bookings (tests/centres rarely change).
const cache = new Map();

export function cached(path) {
  if (!cache.has(path)) {
    const request = api(path).catch((error) => {
      cache.delete(path);
      throw error;
    });
    cache.set(path, request);
  }
  return cache.get(path);
}

export function clearCache() {
  cache.clear();
}
