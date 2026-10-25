"use client";

import React, { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { Button } from "@/components/ui/button";
import { AlertCircle, Lock, Mail, Loader2, ArrowRight } from "lucide-react";

export default function LoginPage() {
  const router = useRouter();
  const { user, isLoading, isConfigured, error: authError, signIn, clearError } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  // If already authenticated, redirect to contracts dashboard
  useEffect(() => {
    if (!isLoading && user) {
      router.replace("/contracts");
    }
  }, [user, isLoading, router]);

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setLocalError(null);
    clearError();

    if (!email.trim() || !password) {
      setLocalError("Please enter both email and password.");
      return;
    }

    try {
      setIsSubmitting(true);
      const { error } = await signIn({ email, password });
      if (error) {
        setLocalError(error.message);
      } else {
        router.push("/contracts");
      }
    } catch {
      setLocalError("An unexpected error occurred. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const displayedError = localError || authError;

  return (
    <div className="min-h-screen flex flex-col justify-center items-center px-4 py-12 bg-canvas text-navy">
      <div className="w-full max-w-md">
        {/* Brand Header */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-primary text-white font-bold text-xl shadow-md mb-4">
            O
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-navy">
            Sign in to Onsigning
          </h1>
          <p className="text-xs text-text-secondary mt-1.5">
            Evidence-backed post-signature contract execution and risk audit platform.
          </p>
        </div>

        {/* Login Card */}
        <div className="bg-white border border-border rounded-xl shadow-card p-6 sm:p-8">
          {/* Missing Configuration Notice */}
          {!isConfigured && (
            <div
              role="alert"
              className="mb-6 p-4 rounded-lg bg-amber-50 border border-amber-200 text-amber-900 text-xs flex items-start space-x-3"
            >
              <AlertCircle className="w-4 h-4 text-amber-600 flex-shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold">Authentication Not Configured</p>
                <p className="text-amber-800 mt-0.5">
                  The client requires <code className="font-mono text-[11px] bg-amber-100 px-1 py-0.5 rounded">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
                  <code className="font-mono text-[11px] bg-amber-100 px-1 py-0.5 rounded">NEXT_PUBLIC_SUPABASE_ANON_KEY</code>. Please configure your environment variables.
                </p>
              </div>
            </div>
          )}

          {/* Authentication Error Alert */}
          {isConfigured && displayedError && (
            <div
              role="alert"
              className="mb-6 p-3.5 rounded-lg bg-severity-critical-surface border border-severity-critical-border text-severity-critical-text text-xs flex items-start space-x-2.5"
            >
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div className="flex-1 font-medium leading-relaxed">
                {displayedError}
              </div>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="email"
                className="block text-xs font-semibold text-navy mb-1.5"
              >
                Enterprise Email
              </label>
              <div className="relative">
                <Mail className="w-4 h-4 text-text-secondary absolute left-3 top-2.5 pointer-events-none" />
                <input
                  id="email"
                  type="email"
                  autoComplete="email"
                  required
                  disabled={!isConfigured || isSubmitting}
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="legal@company.com"
                  className="w-full pl-9 pr-3 py-2 text-xs rounded-md border border-border bg-canvas text-navy placeholder:text-slate-400 focus:border-primary focus:bg-white focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50 disabled:cursor-not-allowed transition"
                />
              </div>
            </div>

            <div>
              <label
                htmlFor="password"
                className="block text-xs font-semibold text-navy mb-1.5"
              >
                Password
              </label>
              <div className="relative">
                <Lock className="w-4 h-4 text-text-secondary absolute left-3 top-2.5 pointer-events-none" />
                <input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  disabled={!isConfigured || isSubmitting}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••••••"
                  className="w-full pl-9 pr-3 py-2 text-xs rounded-md border border-border bg-canvas text-navy placeholder:text-slate-400 focus:border-primary focus:bg-white focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50 disabled:cursor-not-allowed transition"
                />
              </div>
            </div>

            <Button
              type="submit"
              variant="primary"
              size="md"
              disabled={!isConfigured || isSubmitting || isLoading}
              className="w-full mt-2 font-semibold"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Signing in...
                </>
              ) : (
                <>
                  Sign in
                  <ArrowRight className="w-4 h-4 ml-1.5" />
                </>
              )}
            </Button>
          </form>
        </div>

        {/* Security & Provenance Footer */}
        <p className="text-center text-[11px] text-text-secondary mt-6">
          Protected by enterprise cryptographic token validation and tenant Row-Level Security.
        </p>
      </div>
    </div>
  );
}
