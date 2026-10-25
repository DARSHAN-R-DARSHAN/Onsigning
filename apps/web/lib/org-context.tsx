"use client";

import React, { createContext, useContext, useEffect, useState, useCallback, useMemo, useRef } from "react";
import { useAuth } from "@/lib/auth-context";
import { apiClient, ApiError } from "@/lib/api";

export interface OrganizationSummary {
  id: string;
  name: string;
  slug: string;
  role: "OWNER" | "ADMIN" | "LEGAL_REVIEWER" | "VIEWER" | string;
  created_at: string;
}

export interface OrgContextType {
  organizations: OrganizationSummary[];
  selectedOrg: OrganizationSummary | null;
  selectedOrgId: string | null;
  isLoading: boolean;
  error: string | null;
  selectOrg: (orgId: string) => void;
  refreshOrganizations: () => Promise<void>;
  clearOrgError: () => void;
}

const STORAGE_KEY = "onsigning_selected_org_id";

const OrgContext = createContext<OrgContextType | undefined>(undefined);

export function OrgProvider({ children }: { children: React.ReactNode }) {
  const { user, token, isLoading: isAuthLoading } = useAuth();

  const [organizations, setOrganizations] = useState<OrganizationSummary[]>([]);
  const [selectedOrg, setSelectedOrg] = useState<OrganizationSummary | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Counter to discard out-of-order responses across auth or tenant changes
  const requestIdRef = useRef<number>(0);

  const fetchOrganizations = useCallback(async (authToken: string): Promise<void> => {
    const requestId = ++requestIdRef.current;
    setIsLoading(true);
    setError(null);

    try {
      // Pre-tenant discovery: calls GET /api/v1/organizations with token, strictly without X-Organization-Id
      const orgs = await apiClient.get<OrganizationSummary[]>("/api/v1/organizations", {
        token: authToken,
      });

      // Guard against stale response if another request was triggered
      if (requestId !== requestIdRef.current) {
        return;
      }

      setOrganizations(orgs);

      // Determine active organization:
      // 1. Check if user previously stored a preferred organization ID
      // 2. ONLY accept the stored ID if it exists in the validated membership list
      // 3. Otherwise default to the first organization or null
      let targetOrg: OrganizationSummary | null = null;
      let storedOrgId: string | null = null;

      try {
        storedOrgId = typeof window !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
      } catch {
        storedOrgId = null;
      }

      if (storedOrgId) {
        targetOrg = orgs.find((o) => o.id === storedOrgId) ?? null;
      }

      if (!targetOrg && orgs.length > 0) {
        targetOrg = orgs[0];
      }

      setSelectedOrg(targetOrg);

      if (targetOrg && typeof window !== "undefined") {
        try {
          localStorage.setItem(STORAGE_KEY, targetOrg.id);
        } catch {
          // Ignore localStorage errors (e.g. private browsing storage restrictions)
        }
      }
    } catch (err: unknown) {
      if (requestId !== requestIdRef.current) {
        return;
      }

      const message = err instanceof ApiError ? err.message : "Failed to load workspaces. Please check your network.";
      setError(message);
      setOrganizations([]);
      setSelectedOrg(null);
    } finally {
      if (requestId === requestIdRef.current) {
        setIsLoading(false);
      }
    }
  }, []);

  // Synchronize organization discovery with user auth state
  useEffect(() => {
    // If auth is still resolving, remain idle
    if (isAuthLoading) {
      return;
    }

    // When user logs out or has no valid token, purge all organization state
    if (!user || !token) {
      requestIdRef.current++;
      setOrganizations([]);
      setSelectedOrg(null);
      setIsLoading(false);
      setError(null);
      try {
        if (typeof window !== "undefined") {
          localStorage.removeItem(STORAGE_KEY);
        }
      } catch {
        // Ignore localStorage errors
      }
      return;
    }

    // Fetch workspaces for authenticated user
    fetchOrganizations(token);
  }, [user, token, isAuthLoading, fetchOrganizations]);

  const selectOrg = useCallback((orgId: string) => {
    const match = organizations.find((o) => o.id === orgId);
    if (!match) {
      // Do not allow selecting an organization the user does not belong to
      return;
    }

    setSelectedOrg(match);
    try {
      if (typeof window !== "undefined") {
        localStorage.setItem(STORAGE_KEY, match.id);
      }
    } catch {
      // Ignore localStorage write failures
    }
  }, [organizations]);

  const refreshOrganizations = useCallback(async (): Promise<void> => {
    if (token) {
      await fetchOrganizations(token);
    }
  }, [token, fetchOrganizations]);

  const clearOrgError = useCallback(() => {
    setError(null);
  }, []);

  const value = useMemo<OrgContextType>(() => ({
    organizations,
    selectedOrg,
    selectedOrgId: selectedOrg?.id ?? null,
    isLoading,
    error,
    selectOrg,
    refreshOrganizations,
    clearOrgError,
  }), [organizations, selectedOrg, isLoading, error, selectOrg, refreshOrganizations, clearOrgError]);

  return <OrgContext.Provider value={value}>{children}</OrgContext.Provider>;
}

export function useOrg(): OrgContextType {
  const context = useContext(OrgContext);
  if (!context) {
    throw new Error("useOrg must be used within an OrgProvider");
  }
  return context;
}
