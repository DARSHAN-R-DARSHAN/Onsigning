"use client";

import React, { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { DocumentViewer } from "@/components/document/document-viewer";
import { FindingsRail } from "@/components/audit/findings-rail";
import { RiskAlertData } from "@/components/audit/alert-card";
import { ProcessingStatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { useOrg } from "@/lib/org-context";
import { apiClient, ApiError } from "@/lib/api";
import {
  ArrowLeft,
  RefreshCw,
  FileText,
  AlertCircle,
  Loader2,
  LogIn,
  X,
} from "lucide-react";

interface DocumentDetail {
  id: string;
  title: string;
  original_filename: string;
  mime_type: string;
  byte_size: number;
  processing_status: string;
  page_count: number | null;
  unreadable_pages: number[];
  parser_warnings: Array<{ page?: number; message?: string }>;
  error_category: string | null;
  error_message: string | null;
  failed_at: string | null;
  created_at: string;
}

interface LatestAuditResponse {
  audit_run_id: string;
  document_id: string;
  playbook_id?: string | null;
  playbook_version?: number;
  status: string;
  completed_at: string | null;
  summary: Record<string, unknown>;
  alerts: RiskAlertData[];
  extracted_clauses: Array<Record<string, unknown>>;
}

interface PlaybookDetailResponse {
  id: string;
  name: string;
  version: number;
  description?: string;
  is_active: boolean;
  rules: Array<Record<string, unknown>>;
}

interface PlaybookSummary {
  name: string;
  version: number;
  description?: string;
}

export default function SingleAgreementAuditPage() {
  const params = useParams();
  const documentId =
    typeof params?.id === "string"
      ? params.id
      : Array.isArray(params?.id)
      ? params.id[0]
      : "";

  const { token, isLoading: isAuthLoading } = useAuth();
  const { selectedOrg, selectedOrgId, isLoading: isOrgLoading } = useOrg();

  // Document and audit report state
  const [documentData, setDocumentData] = useState<DocumentDetail | null>(null);
  const [playbookData, setPlaybookData] = useState<PlaybookSummary>({
    name: "Baseline Risk Playbook",
    version: 1,
    description: "Evaluates standard liability caps, carveouts, governing law, and renewals.",
  });
  const [alerts, setAlerts] = useState<RiskAlertData[]>([]);

  // Loading and error states
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [isNotFound, setIsNotFound] = useState<boolean>(false);

  // Mutation and interactive states
  const [isRerunning, setIsRerunning] = useState<boolean>(false);
  const [rerunError, setRerunError] = useState<string | null>(null);

  // View mode for tablet / mobile responsive layout
  const [mobileTab, setMobileTab] = useState<"DOCUMENT" | "REPORT">("REPORT");

  // Active highlighted citation in DocumentViewer
  const [highlightPage, setHighlightPage] = useState<number>(1);
  const [highlightQuote, setHighlightQuote] = useState<string | undefined>(undefined);

  // Reset state immediately on workspace switch to prevent displaying stale cross-workspace data
  useEffect(() => {
    setDocumentData(null);
    setAlerts([]);
    setError(null);
    setIsNotFound(false);
    setRerunError(null);
  }, [selectedOrgId]);

  const loadDocumentAndAudit = useCallback(async () => {
    if (!documentId) {
      setIsLoading(false);
      return;
    }

    if (!token || !selectedOrgId) {
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setError(null);
    setIsNotFound(false);

    try {
      // 1. Fetch live document details
      const doc = await apiClient.get<DocumentDetail>(`/api/v1/documents/${documentId}`, {
        token,
        organizationId: selectedOrgId,
      });
      setDocumentData(doc);

      // 2. Fetch latest audit report if available
      try {
        const audit = await apiClient.get<LatestAuditResponse>(
          `/api/v1/audits/document/${documentId}/latest`,
          {
            token,
            organizationId: selectedOrgId,
          }
        );

        const normalizedAlerts: RiskAlertData[] = (audit.alerts || []).map((a) => ({
          ...a,
          evidence: a.evidence || {},
        }));
        setAlerts(normalizedAlerts);

        // Highlight first alert with a valid page reference
        const firstCitation = normalizedAlerts.find((a) => a.evidence?.page_number != null);
        if (firstCitation && firstCitation.evidence.page_number) {
          setHighlightPage(firstCitation.evidence.page_number);
          setHighlightQuote(firstCitation.evidence.quote);
        }

        // Fetch playbook metadata if playbook_id is specified
        if (audit.playbook_id) {
          try {
            const pb = await apiClient.get<PlaybookDetailResponse>(
              `/api/v1/playbooks/${audit.playbook_id}`,
              {
                token,
                organizationId: selectedOrgId,
              }
            );
            setPlaybookData({
              name: pb.name,
              version: pb.version,
              description: pb.description,
            });
          } catch {
            setPlaybookData({
              name: "Baseline Risk Playbook",
              version: audit.playbook_version || 1,
              description: "Standard risk evaluation playbook.",
            });
          }
        } else {
          setPlaybookData({
            name: "Baseline Risk Playbook",
            version: audit.playbook_version || 1,
            description: "Standard risk evaluation playbook.",
          });
        }
      } catch (auditErr: unknown) {
        // HTTP 404 indicates an audit run has not yet been executed for this document
        if (auditErr instanceof ApiError && auditErr.status === 404) {
          setAlerts([]);
          setPlaybookData({
            name: "Baseline Risk Playbook",
            version: 1,
            description: "Playbook evaluation pending or in progress.",
          });
        } else {
          const msg =
            auditErr instanceof Error
              ? auditErr.message
              : "Unable to load audit report findings.";
          setRerunError(msg);
        }
      }
    } catch (err: unknown) {
      if (err instanceof ApiError && err.status === 404) {
        setIsNotFound(true);
        setError(`Agreement "${documentId}" was not found in this workspace.`);
      } else {
        const message =
          err instanceof ApiError
            ? err.message
            : err instanceof Error
            ? err.message
            : "Failed to load contract agreement. Please check your network connection.";
        setError(message);
      }
      setDocumentData(null);
      setAlerts([]);
    } finally {
      setIsLoading(false);
    }
  }, [documentId, token, selectedOrgId]);

  useEffect(() => {
    if (!isAuthLoading && !isOrgLoading) {
      loadDocumentAndAudit();
    }
  }, [isAuthLoading, isOrgLoading, loadDocumentAndAudit]);

  const handleSelectAlert = useCallback((alert: RiskAlertData) => {
    if (alert.evidence?.page_number) {
      setHighlightPage(alert.evidence.page_number);
      setHighlightQuote(alert.evidence.quote);
      // On mobile, switch to document tab to inspect citation
      if (typeof window !== "undefined" && window.innerWidth < 1024) {
        setMobileTab("DOCUMENT");
      }
    }
  }, []);

  const handleUpdateAlertReview = async (alertId: string, status: string, note: string) => {
    if (!token || !selectedOrgId) {
      throw new Error("Authentication required to update review decision.");
    }

    // Call PATCH /api/v1/audits/alerts/{alertId}/review
    const updated = await apiClient.patch<{
      alert_id: string;
      review_status: string;
      reviewed_by?: string;
      reviewed_at?: string;
      review_note?: string;
    }>(
      `/api/v1/audits/alerts/${alertId}/review`,
      {
        review_status: status,
        review_note: note.trim() || undefined,
      },
      {
        token,
        organizationId: selectedOrgId,
      }
    );

    // Update local alert state only after server confirmation
    setAlerts((prev) =>
      prev.map((a) =>
        a.id === alertId
          ? {
              ...a,
              review_status: updated.review_status,
              reviewed_by: updated.reviewed_by,
              reviewed_at: updated.reviewed_at,
              review_note: updated.review_note,
            }
          : a
      )
    );
  };

  const handleRerunAudit = async () => {
    if (!token || !selectedOrgId || !documentId || isRerunning) return;

    try {
      setIsRerunning(true);
      setRerunError(null);

      await apiClient.post(
        "/api/v1/audits/run",
        { document_id: documentId },
        {
          token,
          organizationId: selectedOrgId,
        }
      );

      // Refresh contract details and latest findings from backend
      await loadDocumentAndAudit();
    } catch (err: unknown) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
          ? err.message
          : "Failed to run audit. Please check your permissions and try again.";
      setRerunError(message);
    } finally {
      setIsRerunning(false);
    }
  };

  // Missing Document ID State
  if (!documentId) {
    return (
      <AppShell>
        <div className="flex-1 p-8 max-w-lg mx-auto text-center mt-12 bg-white rounded-lg border border-border shadow-card">
          <AlertCircle className="w-12 h-12 text-amber-500 mx-auto mb-3" />
          <h2 className="text-base font-semibold text-navy">Missing Document ID</h2>
          <p className="text-xs text-text-secondary mt-1">
            No contract document identifier was provided in the URL.
          </p>
          <Link href="/contracts" className="inline-block mt-4">
            <Button variant="primary" size="md">
              Return to Contracts
            </Button>
          </Link>
        </div>
      </AppShell>
    );
  }

  // Unauthenticated State
  if (!isAuthLoading && !token) {
    return (
      <AppShell>
        <div className="flex-1 p-8 max-w-lg mx-auto text-center mt-12 bg-white rounded-lg border border-border shadow-card">
          <LogIn className="w-12 h-12 text-primary mx-auto mb-3" />
          <h2 className="text-base font-semibold text-navy">Authentication Required</h2>
          <p className="text-xs text-text-secondary mt-1">
            Please sign in to view this contract and its audit findings.
          </p>
          <Link href="/login" className="inline-block mt-4">
            <Button variant="primary" size="md">
              Sign In
            </Button>
          </Link>
        </div>
      </AppShell>
    );
  }

  // Missing Workspace State
  if (!isOrgLoading && token && !selectedOrgId) {
    return (
      <AppShell>
        <div className="flex-1 p-8 max-w-lg mx-auto text-center mt-12 bg-white rounded-lg border border-border shadow-card">
          <AlertCircle className="w-12 h-12 text-amber-500 mx-auto mb-3" />
          <h2 className="text-base font-semibold text-navy">No Workspace Selected</h2>
          <p className="text-xs text-text-secondary mt-1">
            Please select an organization workspace from the workspace switcher to view this contract.
          </p>
          <Link href="/contracts" className="inline-block mt-4">
            <Button variant="secondary" size="md">
              <ArrowLeft className="w-4 h-4 mr-1.5" /> Back to Contracts
            </Button>
          </Link>
        </div>
      </AppShell>
    );
  }

  // Loading State
  if (isLoading || isAuthLoading || isOrgLoading) {
    return (
      <AppShell>
        <div className="flex-1 flex flex-col items-center justify-center p-12">
          <Loader2 className="w-8 h-8 text-primary animate-spin mb-3" />
          <h2 className="text-sm font-semibold text-navy">Loading contract agreement...</h2>
          <p className="text-xs text-text-secondary mt-1">
            Retrieving document analysis and audit findings from {selectedOrg?.name || "workspace"}...
          </p>
        </div>
      </AppShell>
    );
  }

  // Error or Not Found State
  if (error || !documentData) {
    return (
      <AppShell>
        <div className="flex-1 p-8 max-w-lg mx-auto text-center mt-12 bg-white rounded-lg border border-border shadow-card">
          <AlertCircle className="w-12 h-12 text-rose-500 mx-auto mb-3" />
          <h2 className="text-base font-semibold text-navy">
            {isNotFound ? "Contract Not Found" : "Unable to Load Contract"}
          </h2>
          <p className="text-xs text-text-secondary mt-1">
            {error || "The requested contract agreement could not be retrieved."}
          </p>
          <div className="flex items-center justify-center space-x-2 mt-4">
            <Link href="/contracts">
              <Button variant="secondary" size="md">
                <ArrowLeft className="w-4 h-4 mr-1.5" /> Back to Contracts
              </Button>
            </Link>
            {!isNotFound && (
              <Button variant="primary" size="md" onClick={loadDocumentAndAudit}>
                <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Retry
              </Button>
            )}
          </div>
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <div className="flex flex-col h-screen overflow-hidden bg-white">
        {/* Top Workspace Header Bar */}
        <header className="h-14 border-b border-border bg-white px-4 lg:px-6 flex items-center justify-between flex-shrink-0 z-20">
          <div className="flex items-center space-x-3 min-w-0">
            <Link
              href="/contracts"
              className="text-text-secondary hover:text-navy p-1 rounded hover:bg-canvas transition flex items-center text-xs font-medium"
              title="Return to Contracts List"
            >
              <ArrowLeft className="w-4 h-4 mr-1" />
              <span className="hidden sm:inline">Contracts</span>
            </Link>

            <span className="text-border">|</span>

            <div className="flex items-center space-x-2 min-w-0">
              <FileText className="w-4 h-4 text-primary flex-shrink-0" />
              <h1
                className="text-sm font-bold text-navy truncate max-w-xs sm:max-w-md lg:max-w-lg"
                title={documentData.title}
              >
                {documentData.title}
              </h1>
              <ProcessingStatusBadge status={documentData.processing_status} />
            </div>
          </div>

          {/* Top Actions & Responsive Segmented Tab Control */}
          <div className="flex items-center space-x-2">
            {/* Tablet/Mobile Segmented Switcher */}
            <div className="flex lg:hidden bg-canvas p-0.5 rounded border border-border">
              <button
                onClick={() => setMobileTab("DOCUMENT")}
                className={`px-2.5 py-1 text-xs rounded font-medium transition ${
                  mobileTab === "DOCUMENT" ? "bg-white text-primary shadow-sm" : "text-slate-600"
                }`}
                aria-label="Switch to document view"
              >
                Document
              </button>
              <button
                onClick={() => setMobileTab("REPORT")}
                className={`px-2.5 py-1 text-xs rounded font-medium transition ${
                  mobileTab === "REPORT" ? "bg-white text-primary shadow-sm" : "text-slate-600"
                }`}
                aria-label="Switch to report view"
              >
                Report ({alerts.length})
              </button>
            </div>

            <Button
              variant="secondary"
              size="sm"
              onClick={handleRerunAudit}
              isLoading={isRerunning}
              disabled={isRerunning}
              className="hidden sm:inline-flex h-8 text-xs"
              title="Re-run risk playbook audit against this agreement"
            >
              <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Re-audit
            </Button>
          </div>
        </header>

        {/* Operational Status & Error Banners */}
        {rerunError && (
          <div className="bg-rose-50 border-b border-rose-200 px-4 py-2 flex items-center justify-between text-xs text-rose-800">
            <div className="flex items-center">
              <AlertCircle className="w-4 h-4 text-rose-600 mr-2 flex-shrink-0" />
              <span>{rerunError}</span>
            </div>
            <button
              onClick={() => setRerunError(null)}
              className="text-rose-600 hover:text-rose-800 p-1"
              aria-label="Dismiss error banner"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {documentData.processing_status === "FAILED" && (
          <div className="bg-rose-50 border-b border-rose-200 px-4 py-2 flex items-center text-xs text-rose-800">
            <AlertCircle className="w-4 h-4 text-rose-600 mr-2 flex-shrink-0" />
            <span>
              Document processing failed: {documentData.error_message || "An error occurred during ingestion or audit."}
              {documentData.error_category ? ` (${documentData.error_category})` : ""}
            </span>
          </div>
        )}

        {["UPLOADED", "PARSING_LAYOUT", "EXTRACTING_FACTS", "EVALUATING_PLAYBOOK"].includes(
          documentData.processing_status
        ) && (
          <div className="bg-blue-50 border-b border-blue-200 px-4 py-2 flex items-center justify-between text-xs text-primary">
            <div className="flex items-center">
              <RefreshCw className="w-3.5 h-3.5 mr-2 animate-spin flex-shrink-0" />
              <span>
                Agreement analysis is currently in progress ({documentData.processing_status.replace(/_/g, " ")}).
              </span>
            </div>
            <Button
              variant="secondary"
              size="sm"
              onClick={loadDocumentAndAudit}
              className="h-6 px-2 text-[11px]"
            >
              Check Status
            </Button>
          </div>
        )}

        {/* Side-by-Side Review Workspace (Desktop 55% / 45%) */}
        <div className="flex-1 flex overflow-hidden">
          {/* Left: Document Viewer (55% on desktop) */}
          <div
            className={`w-full lg:w-[55%] h-full flex flex-col ${
              mobileTab === "DOCUMENT" ? "flex" : "hidden lg:flex"
            }`}
          >
            <DocumentViewer
              documentId={documentData.id}
              documentTitle={documentData.title}
              totalPages={documentData.page_count || 1}
              highlightPage={highlightPage}
              highlightQuote={highlightQuote}
            />
          </div>

          {/* Right: Findings Rail (45% on desktop) */}
          <div
            className={`w-full lg:w-[45%] h-full flex flex-col ${
              mobileTab === "REPORT" ? "flex" : "hidden lg:flex"
            }`}
          >
            <FindingsRail
              playbook={playbookData}
              alerts={alerts}
              unreadablePages={documentData.unreadable_pages || []}
              parserWarnings={documentData.parser_warnings || []}
              onSelectAlert={handleSelectAlert}
              onUpdateAlertReview={handleUpdateAlertReview}
              onRerunAudit={handleRerunAudit}
              isRerunning={isRerunning}
            />
          </div>
        </div>
      </div>
    </AppShell>
  );
}
