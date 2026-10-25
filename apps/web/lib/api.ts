/**
 * Onsigning Centralized API Client
 *
 * Provides a typed HTTP client for communicating with the FastAPI backend.
 * Automatically handles:
 * - Base URL resolution from NEXT_PUBLIC_API_BASE_URL (defaulting to http://localhost:8000)
 * - Safe URL path normalization (preventing duplicate /api/v1 prefixes)
 * - Supabase JWT bearer token authorization
 * - Multi-tenant workspace context (X-Organization-Id header)
 * - Error normalization into typed ApiError instances
 * - JSON and multipart form-data requests
 * - Binary streaming responses (blobs)
 */

export interface RequestOptions extends Omit<RequestInit, "headers" | "body"> {
  token?: string | null;
  organizationId?: string | null;
  headers?: Record<string, string>;
  body?: unknown;
}

export class ApiError extends Error {
  status: number;
  data: unknown;

  constructor(status: number, message: string, data?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
}

/**
 * Returns the normalized API base URL without trailing slashes.
 */
export function getApiBaseUrl(): string {
  const envUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!envUrl || envUrl.trim() === "") {
    return "http://localhost:8000";
  }
  return envUrl.trim().replace(/\/+$/, "");
}

/**
 * Normalizes an endpoint path against the base URL, safely deduplicating /api/v1 prefixes.
 */
export function resolveApiUrl(endpoint: string): string {
  const base = getApiBaseUrl();
  const normalizedEndpoint = endpoint.startsWith("/") ? endpoint : `/${endpoint}`;

  if (base.endsWith("/api/v1") && normalizedEndpoint.startsWith("/api/v1/")) {
    return `${base}${normalizedEndpoint.slice("/api/v1".length)}`;
  }

  return `${base}${normalizedEndpoint}`;
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let errorMessage = `API request failed with status ${response.status}`;
    let errorData: unknown = null;

    try {
      const contentType = response.headers.get("content-type");
      if (contentType && contentType.includes("application/json")) {
        errorData = await response.json();
        if (errorData && typeof errorData === "object") {
          const obj = errorData as Record<string, unknown>;
          if (typeof obj.detail === "string") {
            errorMessage = obj.detail;
          } else if (typeof obj.message === "string") {
            errorMessage = obj.message;
          }
        }
      } else {
        const text = await response.text();
        if (text && text.trim().length > 0) {
          errorMessage = text.trim();
        }
      }
    } catch {
      // Fall back to status text if error body parsing fails
      if (response.statusText) {
        errorMessage = response.statusText;
      }
    }

    throw new ApiError(response.status, errorMessage, errorData);
  }

  // 204 No Content
  if (response.status === 204) {
    return undefined as unknown as T;
  }

  const contentType = response.headers.get("content-type");
  if (contentType && contentType.includes("application/json")) {
    return (await response.json()) as T;
  }

  return (await response.text()) as unknown as T;
}

/**
 * Core fetch wrapper executing requests against the Onsigning API.
 */
async function request<T>(endpoint: string, options: RequestOptions = {}): Promise<T> {
  const url = resolveApiUrl(endpoint);
  const headers: Record<string, string> = {
    ...options.headers,
  };

  if (options.token) {
    headers["Authorization"] = `Bearer ${options.token}`;
  }

  if (options.organizationId) {
    headers["X-Organization-Id"] = options.organizationId;
  }

  let body: BodyInit | undefined;
  if (options.body !== undefined) {
    if (options.body instanceof FormData || options.body instanceof Blob) {
      body = options.body;
    } else {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(options.body);
    }
  }

  try {
    const response = await fetch(url, {
      ...options,
      headers,
      body,
    });
    return await handleResponse<T>(response);
  } catch (error: unknown) {
    if (error instanceof ApiError) {
      throw error;
    }
    const message = error instanceof Error ? error.message : "Network connection failed";
    throw new ApiError(0, `Unable to reach API server: ${message}`);
  }
}

/**
 * Centralized API client methods.
 */
export const apiClient = {
  get: <T>(endpoint: string, options?: Omit<RequestOptions, "method" | "body">): Promise<T> => {
    return request<T>(endpoint, { ...options, method: "GET" });
  },

  post: <T>(endpoint: string, body?: unknown, options?: Omit<RequestOptions, "method" | "body">): Promise<T> => {
    return request<T>(endpoint, { ...options, method: "POST", body });
  },

  patch: <T>(endpoint: string, body?: unknown, options?: Omit<RequestOptions, "method" | "body">): Promise<T> => {
    return request<T>(endpoint, { ...options, method: "PATCH", body });
  },

  delete: <T>(endpoint: string, options?: Omit<RequestOptions, "method" | "body">): Promise<T> => {
    return request<T>(endpoint, { ...options, method: "DELETE" });
  },

  postFormData: <T>(endpoint: string, formData: FormData, options?: Omit<RequestOptions, "method" | "body">): Promise<T> => {
    return request<T>(endpoint, { ...options, method: "POST", body: formData });
  },

  getBlob: async (endpoint: string, options?: Omit<RequestOptions, "method" | "body">): Promise<Blob> => {
    const url = resolveApiUrl(endpoint);
    const headers: Record<string, string> = {
      ...options?.headers,
    };

    if (options?.token) {
      headers["Authorization"] = `Bearer ${options.token}`;
    }

    if (options?.organizationId) {
      headers["X-Organization-Id"] = options.organizationId;
    }

    try {
      const response = await fetch(url, {
        ...options,
        method: "GET",
        headers,
      });

      if (!response.ok) {
        throw new ApiError(response.status, `Failed to stream file: ${response.statusText}`);
      }

      return await response.blob();
    } catch (error: unknown) {
      if (error instanceof ApiError) {
        throw error;
      }
      const message = error instanceof Error ? error.message : "Network error during blob download";
      throw new ApiError(0, message);
    }
  },
};
