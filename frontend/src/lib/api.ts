/** API client for Nodeglow backend */

import { loginHref } from './redirect';

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? '';

export function getCsrfToken(): string {
  if (typeof document === 'undefined') return '';
  const match = document.cookie.match(/ng_csrf=([^;]+)/);
  if (!match) return '';
  return decodeURIComponent(match[1]);
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public data?: unknown) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function api<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const headers: Record<string, string> = {
    Accept: 'application/json',
    ...(options.headers as Record<string, string>),
  };

  const method = (options.method ?? 'GET').toUpperCase();
  if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
    headers['x-csrf-token'] = getCsrfToken();
    if (options.body instanceof URLSearchParams) {
      headers['Content-Type'] = 'application/x-www-form-urlencoded';
    } else if (options.body && typeof options.body === 'string') {
      headers['Content-Type'] = 'application/json';
    }
  }

  const res = await fetch(url, {
    ...options,
    headers,
    credentials: 'include',
  });

  if (res.status === 401) {
    if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
      // Come back to where the session ran out after logging in again.
      window.location.href = loginHref(
        window.location.pathname + window.location.search + window.location.hash,
      );
    }
    throw new ApiError(401, 'Unauthorized');
  }

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new ApiError(res.status, `API ${method} ${path}: ${res.status}`, text);
  }

  const contentType = res.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) {
    return res.json() as Promise<T>;
  }
  return res.text() as unknown as T;
}

/** JSON error body the backend sends with 4xx responses, e.g. {error, code}. */
export interface ApiErrorBody {
  error?: string;
  code?: string;
  missing_fields?: string[];
  [key: string]: unknown;
}

/** Parse the JSON body of a failed request, or null when there is none. */
export function apiErrorBody(e: unknown): ApiErrorBody | null {
  if (!(e instanceof ApiError) || typeof e.data !== 'string' || !e.data) return null;
  try {
    const parsed = JSON.parse(e.data);
    return parsed && typeof parsed === 'object' ? (parsed as ApiErrorBody) : null;
  } catch {
    return null;
  }
}

/** The backend's `error` (or FastAPI `detail`) text, else `fallback`. */
export function apiErrorMessage(e: unknown, fallback: string): string {
  const body = apiErrorBody(e);
  if (typeof body?.error === 'string' && body.error) return body.error;
  if (typeof body?.detail === 'string' && body.detail) return body.detail;
  return fallback;
}

/** Convenience methods */
export const get = <T>(path: string) => api<T>(path);

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, {
    method: 'POST',
    body: body ? JSON.stringify(body) : undefined,
  });

export const patch = <T>(path: string, body?: unknown) =>
  api<T>(path, {
    method: 'PATCH',
    body: body ? JSON.stringify(body) : undefined,
  });

export const put = <T>(path: string, body?: unknown) =>
  api<T>(path, {
    method: 'PUT',
    body: body ? JSON.stringify(body) : undefined,
  });

export const del = <T>(path: string) =>
  api<T>(path, { method: 'DELETE' });
