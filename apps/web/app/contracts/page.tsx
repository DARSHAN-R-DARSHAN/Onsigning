"use client";

import React, { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { ProcessingStatusBadge } from "@/components/ui/badge";
import { UploadModal } from "@/components/document/upload-modal";
import { useAuth } from "@/lib/auth-context";
import { useOrg } from "@/lib/org-context";
import { apiClient, ApiError } from "@/lib/api";
import {
  FileText,
  UploadCloud,
  Search,
  Filter,
  ArrowRight,
  AlertCircle,
  RefreshCw,
  Loader2,
  LogIn,
} from "lucide-react";
import { formatBytes } from "@/lib/utils";

export interface ContractDocumentItem {
  id: string;
  title: string;
  original_filename?: string;
  filename?: string;
  hierarchy_type?: string;
  page_count?: number | null;
  byte_size: number;
  processing_status: string;
  created_at: string;
  critical_alerts?: number;
  total_alerts?: number;
  error_category?: string | null;
  error_message?: string | null;
}

export default function ContractsListPage() {
  const router = useRouter();
  const { token, isLoading: isAuthLoading } = useAuth();
  const { selectedOrg, selectedOrgId, isLoading: isOrgLoading } = useOrg();

  const [documents, setDocuments] = useState<ContractDocumentItem[]>([]);
  const [isLoadingDocs, setIsLoadingDocs] = useState<boolean>(true);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const [isUploadOpen, setIsUploadOpen] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("ALL");

  const fetchDocuments = useCallback(async () => {
    if (!token || !selectedOrgId) {
      setDocuments([]);
      setIsLoadingDocs(false);
      return;
    }

    setIsLoadingDocs(true);
    setFetchError(null);

    try {
      const data = await apiClient.get<ContractDocumentItem[]>("/api/v1/documents", {
        token,
        organizationId: selectedOrgId,
      });
      setDocuments(data || []);
    } catch (err: unknown) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
          ? err.message
          : "Failed to load contracts. Please check your network connection.";
      setFetchError(message);
      setDocuments([]);
    } finally {
      setIsLoadingDocs(false);
    }
  }, [token, selectedOrgId]);

  useEffect(() => {
    if (!isAuthLoading && !isOrgLoading) {
      fetchDocuments();
    }
  }, [isAuthLoading, isOrgLoading, fetchDocuments]);

  const filteredDocs = documents.filter((doc) => {
    const title = doc.title || "";
    const filename = doc.original_filename || doc.filename || "";
    const matchesSearch =
      title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      filename.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus =
      statusFilter === "ALL" || doc.processing_status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  const isInitialLoading = isAuthLoading || isOrgLoading || (isLoadingDocs && documents.length === 0 && !fetchError);

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto p-6 lg:p-8 max-w-7xl mx-auto w-full">
        {/* Page Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-6 border-b border-border">
          <div>
            <h1 className="text-xl sm:text-2xl font-bold text-navy tracking-tight">
              Contracts & Signed Agreements
            </h1>
            <p className="text-xs sm:text-sm text-text-secondary mt-1">
              {selectedOrg
                ? `Active Workspace: ${selectedOrg.name}. Upload signed vendor agreements to run automated Risk Playbook audits.`
                : "Upload signed vendor agreements to run automated Risk Playbook audits and trace legal obligations."}
            </p>
          </div>

          <Button
            variant="primary"
            size="md"
            onClick={() => setIsUploadOpen(true)}
            disabled={!token || !selectedOrgId}
            className="flex-shrink-0"
          >
            <UploadCloud className="w-4 h-4 mr-2" /> Upload Agreement
          </Button>
        </div>

        {/* Unauthenticated State */}
        {!isAuthLoading && !token && (
          <div className="mt-8 bg-white rounded-lg border border-border shadow-card p-12 text-center max-w-lg mx-auto">
            <LogIn className="w-12 h-12 text-primary mx-auto mb-3" />
            <h2 className="text-base font-semibold text-navy">Authentication Required</h2>
            <p className="text-xs text-text-secondary mt-1">
              Please sign in to view and manage contract agreements for your organization.
            </p>
            <Link href="/login" className="inline-block mt-4">
              <Button variant="primary" size="md">
                Sign In
              </Button>
            </Link>
          </div>
        )}

        {/* Missing Workspace State */}
        {!isOrgLoading && token && !selectedOrgId && (
          <div className="mt-8 bg-white rounded-lg border border-border shadow-card p-12 text-center max-w-lg mx-auto">
            <AlertCircle className="w-12 h-12 text-amber-500 mx-auto mb-3" />
            <h2 className="text-base font-semibold text-navy">No Workspace Selected</h2>
            <p className="text-xs text-text-secondary mt-1">
              You are not currently in an active workspace. Please select or join an organization to access contracts.
            </p>
          </div>
        )}

        {/* Main Content Area for Authenticated & Workspace Selected */}
        {token && selectedOrgId && (
          <>
            {/* Error Banner with Retry */}
            {fetchError && (
              <div className="mt-6 flex items-start justify-between rounded-lg bg-rose-50 border border-rose-200 p-4">
                <div className="flex items-start space-x-3">
                  <AlertCircle className="w-5 h-5 text-rose-600 flex-shrink-0 mt-0.5" />
                  <div>
                    <h3 className="text-sm font-semibold text-rose-900">Failed to load contracts</h3>
                    <p className="text-xs text-rose-700 mt-0.5">{fetchError}</p>
                  </div>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={fetchDocuments}
                  className="flex-shrink-0 ml-4"
                >
                  <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Retry
                </Button>
              </div>
            )}

            {/* Filter & Search Bar */}
            <div className="mt-6 flex flex-col sm:flex-row items-center justify-between gap-3 bg-white p-3 rounded-lg border border-border shadow-card">
              <div className="relative w-full sm:w-80">
                <Search className="w-4 h-4 text-text-secondary absolute left-3 top-2.5" />
                <input
                  type="text"
                  placeholder="Search agreements by title or file..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-9 pr-3 py-1.5 text-xs rounded-md border border-border bg-canvas focus:border-primary focus:bg-white outline-none transition"
                  aria-label="Search agreements by title or file"
                />
              </div>

              <div className="flex items-center space-x-2 w-full sm:w-auto justify-end">
                <Filter className="w-3.5 h-3.5 text-text-secondary" />
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                  className="text-xs rounded-md border border-border p-1.5 bg-white text-navy focus:border-primary outline-none"
                  aria-label="Filter agreements by audit status"
                >
                  <option value="ALL">All Statuses</option>
                  <option value="READY">Audit Complete</option>
                  <option value="READY_WITH_WARNINGS">Complete with Warnings</option>
                  <option value="UPLOADED">Uploaded</option>
                  <option value="PARSING_LAYOUT">Parsing Layout</option>
                  <option value="EXTRACTING_FACTS">Extracting Terms</option>
                  <option value="EVALUATING_PLAYBOOK">Evaluating Playbook</option>
                  <option value="FAILED">Processing Failed</option>
                </select>

                <Button
                  variant="secondary"
                  size="sm"
                  onClick={fetchDocuments}
                  disabled={isLoadingDocs}
                  className="h-8 text-xs ml-2"
                  title="Refresh list"
                  aria-label="Refresh contract list"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${isLoadingDocs ? "animate-spin" : ""}`} />
                </Button>
              </div>
            </div>

            {/* Documents Table / Card List */}
            <div className="mt-4 bg-white rounded-lg border border-border shadow-card overflow-hidden">
              {isInitialLoading ? (
                <div className="p-12 text-center">
                  <Loader2 className="w-8 h-8 text-primary animate-spin mx-auto mb-3" />
                  <h3 className="text-sm font-semibold text-navy">Loading agreements...</h3>
                  <p className="text-xs text-text-secondary mt-1">
                    Fetching contracts for workspace {selectedOrg?.name || ""}
                  </p>
                </div>
              ) : documents.length === 0 ? (
                <div className="p-12 text-center">
                  <FileText className="w-12 h-12 text-blue-300 mx-auto mb-3" />
                  <h3 className="text-base font-semibold text-navy">No agreements found</h3>
                  <p className="text-xs text-text-secondary mt-1 max-w-sm mx-auto">
                    Upload a signed agreement in PDF or DOCX format to run your first Risk Playbook audit.
                  </p>
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => setIsUploadOpen(true)}
                    className="mt-4"
                  >
                    <UploadCloud className="w-4 h-4 mr-1.5" /> Upload Agreement
                  </Button>
                </div>
              ) : filteredDocs.length === 0 ? (
                <div className="p-12 text-center">
                  <Search className="w-10 h-10 text-slate-300 mx-auto mb-3" />
                  <h3 className="text-base font-semibold text-navy">No matching agreements</h3>
                  <p className="text-xs text-text-secondary mt-1 max-w-sm mx-auto">
                    No agreements matched your current search query or status filter.
                  </p>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      setSearchQuery("");
                      setStatusFilter("ALL");
                    }}
                    className="mt-4"
                  >
                    Clear Filters
                  </Button>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse text-xs">
                    <thead>
                      <tr className="bg-canvas border-b border-border text-text-secondary uppercase font-semibold text-[10px] tracking-wider">
                        <th className="py-3 px-4">Agreement Title</th>
                        <th className="py-3 px-4">Type</th>
                        <th className="py-3 px-4">Audit Status</th>
                        <th className="py-3 px-4">Risk Findings</th>
                        <th className="py-3 px-4">Size & Pages</th>
                        <th className="py-3 px-4 text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border/60">
                      {filteredDocs.map((doc) => {
                        const filename = doc.original_filename || doc.filename || "contract.pdf";
                        return (
                          <tr
                            key={doc.id}
                            className="hover:bg-blue-50/40 transition-colors group cursor-pointer"
                          >
                            <td className="py-3.5 px-4 font-medium text-navy max-w-xs">
                              <Link
                                href={`/contracts/${doc.id}`}
                                className="hover:text-primary transition flex items-center"
                              >
                                <FileText className="w-4 h-4 text-primary mr-2 flex-shrink-0" />
                                <span className="truncate">{doc.title}</span>
                              </Link>
                              <p className="text-[11px] text-text-secondary font-mono truncate pl-6">
                                {filename}
                              </p>
                            </td>

                            <td className="py-3.5 px-4 text-slate-600">
                              <span className="font-mono text-[10px] bg-slate-100 border border-slate-200 px-1.5 py-0.5 rounded">
                                {doc.hierarchy_type ? doc.hierarchy_type.replace(/_/g, " ") : "CONTRACT"}
                              </span>
                            </td>

                            <td className="py-3.5 px-4">
                              <ProcessingStatusBadge status={doc.processing_status} />
                            </td>

                            <td className="py-3.5 px-4">
                              {doc.critical_alerts != null || doc.total_alerts != null ? (
                                <div className="flex items-center space-x-2">
                                  {doc.critical_alerts != null && doc.critical_alerts > 0 && (
                                    <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-severity-critical-surface text-severity-critical-text border border-severity-critical-border">
                                      {doc.critical_alerts} Critical
                                    </span>
                                  )}
                                  <span className="text-text-secondary text-xs">
                                    {doc.total_alerts || 0} finding{doc.total_alerts !== 1 ? "s" : ""}
                                  </span>
                                </div>
                              ) : doc.processing_status === "FAILED" ? (
                                <span
                                  className="text-severity-critical-text font-medium text-xs truncate max-w-[150px] block"
                                  title={doc.error_message || undefined}
                                >
                                  {doc.error_message || "Audit failed"}
                                </span>
                              ) : doc.processing_status === "READY" ? (
                                <span className="text-emerald-700 text-xs font-medium">Audit complete</span>
                              ) : doc.processing_status === "READY_WITH_WARNINGS" ? (
                                <span className="text-amber-700 text-xs font-medium">Warnings reported</span>
                              ) : (
                                <span className="text-text-secondary text-xs italic">Pending analysis</span>
                              )}
                            </td>

                            <td className="py-3.5 px-4 text-text-secondary">
                              {doc.page_count != null ? `${doc.page_count} page${doc.page_count === 1 ? "" : "s"}` : "—"} ·{" "}
                              {formatBytes(doc.byte_size)}
                            </td>

                            <td className="py-3.5 px-4 text-right">
                              <Link href={`/contracts/${doc.id}`}>
                                <Button
                                  variant="secondary"
                                  size="sm"
                                  className="h-7 text-xs font-semibold group-hover:bg-primary group-hover:text-white group-hover:border-primary transition"
                                >
                                  Review Findings <ArrowRight className="w-3 h-3 ml-1" />
                                </Button>
                              </Link>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </div>

      {/* Upload Modal */}
      <UploadModal
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
        onSuccess={(newDocId) => {
          fetchDocuments();
          router.push(`/contracts/${newDocId}`);
        }}
      />
    </AppShell>
  );
}
