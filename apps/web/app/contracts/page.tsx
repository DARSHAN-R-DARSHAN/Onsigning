"use client";

import React, { useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { ProcessingStatusBadge } from "@/components/ui/badge";
import { UploadModal } from "@/components/document/upload-modal";
import {
  FileText,
  UploadCloud,
  Search,
  Filter,
  ArrowRight,
} from "lucide-react";
import { formatBytes } from "@/lib/utils";

interface ContractDocumentItem {
  id: string;
  title: string;
  filename: string;
  hierarchy_type: string;
  page_count: number;
  byte_size: number;
  processing_status: string;
  created_at: string;
  critical_alerts: number;
  total_alerts: number;
}

export default function ContractsListPage() {
  const [isUploadOpen, setIsUploadOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");

  // Sample seed data conforming to Feature 1 single-agreement audit
  const [documents] = useState<ContractDocumentItem[]>([
    {
      id: "doc-sample-msa-001",
      title: "Master Cloud Services Agreement — Datadog Inc.",
      filename: "datadog_enterprise_msa_signed.pdf",
      hierarchy_type: "MASTER_SERVICES_AGREEMENT",
      page_count: 14,
      byte_size: 2450000,
      processing_status: "READY",
      created_at: "2026-10-18T14:30:00Z",
      critical_alerts: 1,
      total_alerts: 3,
    },
    {
      id: "doc-sample-vendor-002",
      title: "Vendor Agreement — Salesforce CRM Order Form",
      filename: "salesforce_order_form_2026.pdf",
      hierarchy_type: "ORDER_FORM",
      page_count: 6,
      byte_size: 1120000,
      processing_status: "READY_WITH_WARNINGS",
      created_at: "2026-10-19T09:15:00Z",
      critical_alerts: 0,
      total_alerts: 2,
    },
  ]);

  const filteredDocs = documents.filter((doc) => {
    const matchesSearch =
      doc.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      doc.filename.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus =
      statusFilter === "ALL" || doc.processing_status === statusFilter;
    return matchesSearch && matchesStatus;
  });

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
              Upload signed vendor agreements to run automated Risk Playbook audits and trace legal obligations.
            </p>
          </div>

          <Button
            variant="primary"
            size="md"
            onClick={() => setIsUploadOpen(true)}
            className="flex-shrink-0"
          >
            <UploadCloud className="w-4 h-4 mr-2" /> Upload Agreement
          </Button>
        </div>

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
            />
          </div>

          <div className="flex items-center space-x-2 w-full sm:w-auto justify-end">
            <Filter className="w-3.5 h-3.5 text-text-secondary" />
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="text-xs rounded-md border border-border p-1.5 bg-white text-navy focus:border-primary outline-none"
            >
              <option value="ALL">All Statuses</option>
              <option value="READY">Audit Complete</option>
              <option value="READY_WITH_WARNINGS">Complete with Warnings</option>
              <option value="FAILED">Processing Failed</option>
            </select>
          </div>
        </div>

        {/* Documents Table / Card List */}
        <div className="mt-4 bg-white rounded-lg border border-border shadow-card overflow-hidden">
          {filteredDocs.length === 0 ? (
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
                  {filteredDocs.map((doc) => (
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
                          {doc.filename}
                        </p>
                      </td>

                      <td className="py-3.5 px-4 text-slate-600">
                        <span className="font-mono text-[10px] bg-slate-100 border border-slate-200 px-1.5 py-0.5 rounded">
                          {doc.hierarchy_type.replace(/_/g, " ")}
                        </span>
                      </td>

                      <td className="py-3.5 px-4">
                        <ProcessingStatusBadge status={doc.processing_status} />
                      </td>

                      <td className="py-3.5 px-4">
                        <div className="flex items-center space-x-2">
                          {doc.critical_alerts > 0 && (
                            <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-severity-critical-surface text-severity-critical-text border border-severity-critical-border">
                              {doc.critical_alerts} Critical
                            </span>
                          )}
                          <span className="text-text-secondary text-xs">
                            {doc.total_alerts} finding{doc.total_alerts !== 1 ? "s" : ""}
                          </span>
                        </div>
                      </td>

                      <td className="py-3.5 px-4 text-text-secondary">
                        {doc.page_count} pages · {formatBytes(doc.byte_size)}
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
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Upload Modal */}
      <UploadModal
        isOpen={isUploadOpen}
        onClose={() => setIsUploadOpen(false)}
        onSuccess={(newDocId) => {
          // Direct navigation or refresh
          window.location.href = `/contracts/${newDocId}`;
        }}
      />
    </AppShell>
  );
}
