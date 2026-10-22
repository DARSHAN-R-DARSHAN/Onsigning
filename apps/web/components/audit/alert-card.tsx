"use client";

import React from "react";
import { SeverityBadge, CitationBadge, ReviewStatusBadge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Check, Edit3, Trash2, FileText, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";

export interface RiskAlertData {
  id: string;
  rule_id: string;
  severity: string;
  title: string;
  explanation: string;
  evidence: {
    clause_category?: string;
    page_number?: number;
    page_end?: number;
    quote?: string;
    citation_status?: string;
    is_evidence_verified?: boolean;
    section_reference?: string;
  };
  review_status: string;
  reviewed_by?: string;
  reviewed_at?: string;
  review_note?: string;
}

interface AlertCardProps {
  alert: RiskAlertData;
  isSelected: boolean;
  onSelect: () => void;
  onNavigateToCitation: (page: number, quote?: string) => void;
  onReviewAction: (action: "CONFIRM" | "OVERRIDE" | "DISMISS") => void;
}

export function AlertCard({
  alert,
  isSelected,
  onSelect,
  onNavigateToCitation,
  onReviewAction,
}: AlertCardProps) {
  const { evidence } = alert;

  return (
    <div
      tabIndex={0}
      onClick={onSelect}
      className={cn(
        "rounded-lg border bg-white p-4 transition-all duration-150 cursor-pointer shadow-card outline-none",
        isSelected
          ? "border-primary ring-2 ring-primary/20 shadow-md"
          : "border-border hover:border-blue-300 hover:shadow"
      )}
    >
      {/* Header: Severity & Review Status */}
      <div className="flex items-center justify-between gap-2 mb-2">
        <SeverityBadge severity={alert.severity} />
        <ReviewStatusBadge status={alert.review_status} />
      </div>

      {/* Title & Explanation */}
      <h3 className="font-semibold text-navy text-sm leading-snug">{alert.title}</h3>
      <p className="text-xs text-slate-600 mt-1.5 leading-relaxed">{alert.explanation}</p>

      {/* Evidence & Quote Box */}
      {evidence.quote ? (
        <div className="mt-3 rounded-md bg-canvas border border-border p-3 text-xs">
          <div className="flex items-center justify-between text-text-secondary text-[11px] mb-1.5">
            <span className="font-medium flex items-center">
              <FileText className="w-3.5 h-3.5 mr-1 text-primary" />
              {evidence.section_reference || "Source Passage"} · Page {evidence.page_number}
              {evidence.page_end && evidence.page_end !== evidence.page_number
                ? `–${evidence.page_end}`
                : ""}
            </span>
            <CitationBadge status={evidence.citation_status || "NORMALIZED_MATCH"} />
          </div>

          <p className="font-serif italic text-navy/90 text-xs border-l-2 border-primary/50 pl-2.5 py-0.5 leading-relaxed">
            &ldquo;{evidence.quote}&rdquo;
          </p>

          {evidence.page_number && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onNavigateToCitation(evidence.page_number!, evidence.quote);
              }}
              className="mt-2 text-[11px] font-medium text-primary hover:text-primary-hover flex items-center transition"
            >
              View in Document <ArrowRight className="w-3 h-3 ml-1" />
            </button>
          )}
        </div>
      ) : (
        <div className="mt-2 text-xs text-text-secondary italic flex items-center">
          <CitationBadge status="NOT_APPLICABLE" />
          <span className="ml-2">Clause omitted or not explicitly found in document.</span>
        </div>
      )}

      {/* Reviewer Audit Note (if preserved) */}
      {alert.review_note && (
        <div className="mt-2.5 rounded bg-blue-50/60 border border-blue-100 p-2 text-[11px] text-navy">
          <span className="font-semibold text-primary">Reviewer Note: </span>
          <span className="text-slate-700">{alert.review_note}</span>
        </div>
      )}

      {/* Review Action Controls */}
      <div className="mt-3.5 pt-2.5 border-t border-border flex items-center justify-between gap-1.5">
        <span className="text-[10px] text-text-secondary font-mono">
          {alert.rule_id}
        </span>

        <div className="flex items-center space-x-1" onClick={(e) => e.stopPropagation()}>
          <Button
            variant="secondary"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => onReviewAction("CONFIRM")}
            title="Confirm Finding (c)"
          >
            <Check className="w-3 h-3 mr-1 text-emerald-600" /> Confirm
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="h-7 px-2 text-xs"
            onClick={() => onReviewAction("OVERRIDE")}
            title="Override Finding (o)"
          >
            <Edit3 className="w-3 h-3 mr-1 text-blue-600" /> Override
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="h-7 px-2 text-xs text-rose-700 hover:bg-rose-50 hover:text-rose-800"
            onClick={() => onReviewAction("DISMISS")}
            title="Dismiss as False Positive (d)"
          >
            <Trash2 className="w-3 h-3 mr-1 text-rose-600" /> Dismiss
          </Button>
        </div>
      </div>
    </div>
  );
}
