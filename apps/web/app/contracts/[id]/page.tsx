"use client";

import React, { useState, useEffect } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { DocumentViewer } from "@/components/document/document-viewer";
import { FindingsRail } from "@/components/audit/findings-rail";
import { ProcessingStatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ArrowLeft, RefreshCw, FileText, Share2, Download, AlertTriangle } from "lucide-react";

export default function SingleAgreementAuditPage() {
  const params = useParams();
  const documentId = (params?.id as string) || "doc-sample-msa-001";

  // Active view mode for tablet / mobile responsive layout
  const [mobileTab, setMobileTab] = useState<"DOCUMENT" | "REPORT">("REPORT");
  const [isRerunning, setIsRerunning] = useState(false);

  // Active highlighted citation in DocumentViewer
  const [highlightPage, setHighlightPage] = useState<number>(1);
  const [highlightQuote, setHighlightQuote] = useState<string | undefined>(
    "The total liability of either party shall not exceed five hundred thousand dollars ($500,000)."
  );

  // Mock document data conforming to Feature 1 specifications
  const [documentData, setDocumentData] = useState({
    id: documentId,
    title: "Master Services Agreement — Acme SaaS Platform",
    filename: "acme_saas_msa_signed.pdf",
    hierarchy_type: "MASTER_SERVICES_AGREEMENT",
    page_count: 5,
    processing_status: "READY",
    unreadable_pages: [] as number[],
    parser_warnings: [] as Array<{ page?: number; message?: string }>,
  });

  const [playbookData, setPlaybookData] = useState({
    name: "Baseline B2B SaaS Risk Playbook",
    version: 1,
    description: "Evaluates limitation of liability caps, IP carveouts, governing law, and auto-renewal notice periods.",
  });

  const [alerts, setAlerts] = useState<any[]>([
    {
      id: "alert-lol-01",
      rule_id: "RULE_LOL_CAP_MINIMUM",
      severity: "HIGH",
      title: "Liability Cap Below $1M Policy Minimum",
      explanation: "The agreement caps total liability at $500,000.00 USD, which is below the organizational policy minimum of $1,000,000.00.",
      evidence: {
        clause_category: "LIMITATION_OF_LIABILITY",
        page_number: 1,
        page_end: 1,
        section_reference: "Section 8.1",
        quote: "The total liability of either party shall not exceed five hundred thousand dollars ($500,000).",
        citation_status: "EXACT_MATCH",
        is_evidence_verified: true,
      },
      review_status: "AI_EVALUATED",
    },
    {
      id: "alert-lol-02",
      rule_id: "RULE_LOL_DISALLOWED_CARVEOUTS",
      severity: "CRITICAL",
      title: "Uncapped Consequential Damages Carve-Out",
      explanation: "Agreement contains uncapped liability carve-outs for indirect or consequential damages.",
      evidence: {
        clause_category: "LIMITATION_OF_LIABILITY",
        page_number: 2,
        page_end: 2,
        section_reference: "Section 8.3",
        quote: "Neither party's liability for consequential damages or loss of profits shall be limited.",
        citation_status: "EXACT_MATCH",
        is_evidence_verified: true,
      },
      review_status: "NEEDS_HUMAN_REVIEW",
    },
    {
      id: "alert-gov-03",
      rule_id: "RULE_GOV_LAW_APPROVED",
      severity: "MEDIUM",
      title: "Non-Standard Governing Law Jurisdiction",
      explanation: "Governing law is specified as Texas, which is outside the approved core jurisdictions (Delaware, New York, California, England and Wales).",
      evidence: {
        clause_category: "GOVERNING_LAW",
        page_number: 4,
        page_end: 4,
        section_reference: "Section 14.2",
        quote: "This Agreement shall be governed by and construed in accordance with the laws of the State of Texas.",
        citation_status: "NORMALIZED_MATCH",
        is_evidence_verified: true,
      },
      review_status: "AI_EVALUATED",
    },
  ]);

  const handleSelectAlert = (alert: any) => {
    if (alert.evidence.page_number) {
      setHighlightPage(alert.evidence.page_number);
      setHighlightQuote(alert.evidence.quote);
      // On mobile, switch to document viewer to view citation
      if (window.innerWidth < 1024) {
        setMobileTab("DOCUMENT");
      }
    }
  };

  const handleUpdateAlertReview = async (alertId: string, status: string, note: string) => {
    // In production, calls PATCH /api/v1/audits/alerts/{alertId}/review
    try {
      await fetch(`/api/v1/audits/alerts/${alertId}/review`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ review_status: status, review_note: note }),
      }).catch(() => null);
    } catch (e) {
      // offline/mock fallback
    }

    setAlerts((prev) =>
      prev.map((a) =>
        a.id === alertId
          ? { ...a, review_status: status, review_note: note, reviewed_at: new Date().toISOString() }
          : a
      )
    );
  };

  const handleRerunAudit = async () => {
    try {
      setIsRerunning(true);
      await fetch("/api/v1/audits/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document_id: documentId }),
      }).catch(() => null);
      // Simulate audit refresh
      setTimeout(() => {
        setIsRerunning(false);
      }, 1200);
    } catch (e) {
      setIsRerunning(false);
    }
  };

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
              <h1 className="text-sm font-bold text-navy truncate max-w-xs sm:max-w-md lg:max-w-lg" title={documentData.title}>
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
              >
                Document
              </button>
              <button
                onClick={() => setMobileTab("REPORT")}
                className={`px-2.5 py-1 text-xs rounded font-medium transition ${
                  mobileTab === "REPORT" ? "bg-white text-primary shadow-sm" : "text-slate-600"
                }`}
              >
                Report ({alerts.length})
              </button>
            </div>

            <Button
              variant="secondary"
              size="sm"
              onClick={handleRerunAudit}
              isLoading={isRerunning}
              className="hidden sm:inline-flex h-8 text-xs"
            >
              <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Re-audit
            </Button>
          </div>
        </header>

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
              totalPages={documentData.page_count}
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
              unreadablePages={documentData.unreadable_pages}
              parserWarnings={documentData.parser_warnings}
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
