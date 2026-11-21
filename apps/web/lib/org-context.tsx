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

  // Keep latest user ID, token, and selectedOrg in refs to guard async boundaries
  const userIdRef = useRef<string | null>(null);
  userIdRef.current = user?.id ?? null;

  const tokenRef = useRef<string | null>(null);
  tokenRef.current = token;

  const selectedOrgRef = useRef<OrganizationSummary | null>(null);
  selectedOrgRef.current = selectedOrg;

  // Track whether organizations have been loaded at least once for current session
  const hasLoadedOrgsRef = useRef<boolean>(false);
  const lastSyncedUserIdRef = useRef<string | null>(null);

  // In-flight request deduplication guard scoped to the active user
  const inFlightRequestRef = useRef<{ userId: string; promise: Promise<void> } | null>(null);

  const fetchOrganizations = useCallback(
    async (authToken: string, isBackground = false): Promise<void> => {
      const activeUserId = userIdRef.current;

      // In-flight request deduplication: return existing promise if request is already active for this user
      if (activeUserId && inFlightRequestRef.current && inFlightRequestRef.current.userId === activeUserId) {
        return inFlightRequestRef.current.promise;
      }

      let currentPromise: Promise<void> | null = null;
      currentPromise = (async () => {
        const requestId = ++requestIdRef.current;

        // Distinguish initial organization loading from background revalidation:
        // Only set the global initial-loading state when data has not yet been loaded.
        if (!isBackground && !hasLoadedOrgsRef.current) {
          setIsLoading(true);
        }
        setError(null);

        try {
          // Pre-tenant discovery: calls GET /api/v1/organizations with token, strictly without X-Organization-Id
          const orgs = await apiClient.get<OrganizationSummary[]>("/api/v1/organizations", {
            token: authToken,
          });

          // Guard against stale response if another request was triggered, user changed, or user logged out
          if (requestId !== requestIdRef.current || !userIdRef.current || userIdRef.current !== activeUserId) {
            return;
          }

          hasLoadedOrgsRef.current = true;
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

          const currentSelected = selectedOrgRef.current;
          // If the currently selected organization is still present in the updated list, keep it
          if (currentSelected && orgs.some((o) => o.id === currentSelected.id)) {
            const freshCurrent = orgs.find((o) => o.id === currentSelected.id)!;
            // Preserve selected organization object identity when fields have not changed
            if (
              currentSelected.id === freshCurrent.id &&
              currentSelected.name === freshCurrent.name &&
              currentSelected.slug === freshCurrent.slug &&
              currentSelected.role === freshCurrent.role
            ) {
              targetOrg = currentSelected;
            } else {
              targetOrg = freshCurrent;
            }
          } else if (storedOrgId) {
            targetOrg = orgs.find((o) => o.id === storedOrgId) ?? null;
          }

          if (!targetOrg && orgs.length > 0) {
            targetOrg = orgs[0];
          }

          // Preserve selected organization object identity when ID has not changed
          setSelectedOrg((prev) => {
            if (!targetOrg && !prev) return null;
            if (prev && targetOrg && prev.id === targetOrg.id && prev === targetOrg) {
              return prev;
            }
            return targetOrg;
          });

          if (targetOrg && typeof window !== "undefined") {
            try {
              localStorage.setItem(STORAGE_KEY, targetOrg.id);
            } catch {
              // Ignore localStorage errors (e.g. private browsing storage restrictions)
            }
          }
        } catch (err: unknown) {
          if (requestId !== requestIdRef.current || !userIdRef.current || userIdRef.current !== activeUserId) {
            return;
          }

          const message = err instanceof ApiError ? err.message : "Failed to load workspaces. Please check your network.";
          setError(message);
          // Only wipe organizations if initial load failed
          if (!hasLoadedOrgsRef.current) {
            setOrganizations([]);
            setSelectedOrg(null);
          }
        } finally {
          if (requestId === requestIdRef.current) {
            setIsLoading(false);
          }
          // Clear in-flight guard only if it still points to this exact promise and user
          if (
            inFlightRequestRef.current &&
            inFlightRequestRef.current.userId === activeUserId &&
            inFlightRequestRef.current.promise === currentPromise
          ) {
            inFlightRequestRef.current = null;
          }
        }
      })();

      if (activeUserId) {
        inFlightRequestRef.current = {
          userId: activeUserId,
          promise: currentPromise,
        };
      }

      return currentPromise;
    },
    []
  );

  // Synchronize organization discovery with user auth state
  useEffect(() => {
    // If auth is still resolving, remain idle
    if (isAuthLoading) {
      return;
    }

    // When user logs out or has no valid token, purge all organization state safely
    if (!user || !token) {
      requestIdRef.current++;
      inFlightRequestRef.current = null;
      lastSyncedUserIdRef.current = null;
      hasLoadedOrgsRef.current = false;
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

    // If the authenticated user changed (e.g. login, switch account), perform full initial discovery
    if (user.id !== lastSyncedUserIdRef.current) {
      inFlightRequestRef.current = null;
      lastSyncedUserIdRef.current = user.id;
      fetchOrganizations(token, false);
      return;
    }

    // If organizations have never been loaded for this session, load them
    if (!hasLoadedOrgsRef.current) {
      fetchOrganizations(token, false);
      return;
    }

    // Routine Supabase token refreshes for the same user do not re-fetch organizations
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

  // Explicit mechanism for refreshing organizations when membership or permissions change
  const refreshOrganizations = useCallback(async (): Promise<void> => {
    const activeToken = tokenRef.current;
    if (activeToken) {
      await fetchOrganizations(activeToken, true);
    }
  }, [fetchOrganizations]);

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
