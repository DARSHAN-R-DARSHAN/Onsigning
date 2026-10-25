"use client";

import React, { createContext, useContext, useEffect, useState, useCallback, useMemo } from "react";
import type { User, Session } from "@supabase/supabase-js";
import { getSupabaseClient, isSupabaseConfigured } from "@/lib/supabase";

export interface SignInCredentials {
  email: string;
  password: string;
}

export interface AuthContextType {
  user: User | null;
  session: Session | null;
  token: string | null;
  isLoading: boolean;
  isConfigured: boolean;
  error: string | null;
  signIn: (credentials: SignInCredentials) => Promise<{ error: Error | null }>;
  signOut: () => Promise<void>;
  clearError: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const configured = useMemo(() => isSupabaseConfigured(), []);

  useEffect(() => {
    if (!configured) {
      setIsLoading(false);
      setError(
        "Supabase authentication is not configured. Missing or invalid NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_ANON_KEY."
      );
      return;
    }

    const supabase = getSupabaseClient();
    if (!supabase) {
      setIsLoading(false);
      setError("Failed to initialize Supabase client with current configuration.");
      return;
    }

    let isMounted = true;
    let authStateReceived = false;

    // 1. Subscribe to auth state changes first.
    // In @supabase/supabase-js v2, onAuthStateChange fires on registration or subsequent auth events.
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, currentSession) => {
      if (!isMounted) return;

      authStateReceived = true;
      setSession(currentSession);
      setUser(currentSession?.user ?? null);
      setToken(currentSession?.access_token ?? null);
      setIsLoading(false);
    });

    // 2. Retrieve initial active session as fallback if listener has not yet populated state
    supabase.auth.getSession().then(({ data: { session: initialSession }, error: sessionError }) => {
      if (!isMounted) return;

      // Prevent stale getSession result from overwriting newer state if onAuthStateChange already fired
      if (!authStateReceived) {
        if (sessionError) {
          setError(sessionError.message);
          setUser(null);
          setSession(null);
          setToken(null);
        } else {
          setSession(initialSession);
          setUser(initialSession?.user ?? null);
          setToken(initialSession?.access_token ?? null);
        }
        setIsLoading(false);
      }
    }).catch(() => {
      if (isMounted && !authStateReceived) {
        setError("An unexpected error occurred while verifying the session.");
        setIsLoading(false);
      }
    });

    return () => {
      isMounted = false;
      subscription.unsubscribe();
    };
  }, [configured]);

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  const signIn = useCallback(async ({ email, password }: SignInCredentials): Promise<{ error: Error | null }> => {
    if (!configured) {
      const configError = new Error("Authentication is not configured. Contact your administrator.");
      setError(configError.message);
      return { error: configError };
    }

    const supabase = getSupabaseClient();
    if (!supabase) {
      const initError = new Error("Supabase client is not available.");
      setError(initError.message);
      return { error: initError };
    }

    setError(null);

    try {
      const { data, error: authError } = await supabase.auth.signInWithPassword({
        email: email.trim(),
        password,
      });

      if (authError) {
        setError(authError.message);
        return { error: new Error(authError.message) };
      }

      setSession(data.session);
      setUser(data.user);
      setToken(data.session?.access_token ?? null);
      return { error: null };
    } catch {
      const genericError = new Error("Unable to connect to authentication service. Please check your network connection.");
      setError(genericError.message);
      return { error: genericError };
    }
  }, [configured]);

  const signOut = useCallback(async (): Promise<void> => {
    const supabase = getSupabaseClient();
    if (supabase) {
      await supabase.auth.signOut().catch(() => null);
    }
    setSession(null);
    setUser(null);
    setToken(null);
    setError(null);
  }, []);

  const value = useMemo<AuthContextType>(() => ({
    user,
    session,
    token,
    isLoading,
    isConfigured: configured,
    error,
    signIn,
    signOut,
    clearError,
  }), [user, session, token, isLoading, configured, error, signIn, signOut, clearError]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
