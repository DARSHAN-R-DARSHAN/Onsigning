import React from "react";
import { cn } from "@/lib/utils";
import { Check, AlertTriangle, AlertCircle, HelpCircle, Minus } from "lucide-react";

export type SeverityType = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "COMPLIANT" | "UNDECIDED_NEEDS_REVIEW";
export type ProcessingStatusType = "UPLOADED" | "PARSING_LAYOUT" | "EXTRACTING_FACTS" | "EVALUATING_PLAYBOOK" | "READY" | "READY_WITH_WARNINGS" | "FAILED";
export type CitationStatusType = "EXACT_MATCH" | "NORMALIZED_MATCH" | "APPROXIMATE_NEEDS_REVIEW" | "UNVERIFIED_MISMATCH" | "NOT_APPLICABLE";
export type ReviewStatusType = "AI_EVALUATED" | "NEEDS_HUMAN_REVIEW" | "HUMAN_CONFIRMED" | "HUMAN_OVERRIDDEN" | "DISMISSED_FALSE_POSITIVE";

export function SeverityBadge({ severity }: { severity: SeverityType | string }) {
  const configs: Record<string, { label: string; text: string; bg: string; border: string; icon: React.ReactNode }> = {
    CRITICAL: {
      label: "Critical Risk",
      text: "text-severity-critical-text",
      bg: "bg-severity-critical-surface",
      border: "border-severity-critical-border",
      icon: <AlertCircle className="w-3.5 h-3.5 mr-1 stroke-2" />,
    },
    HIGH: {
      label: "High Risk",
      text: "text-severity-high-text",
      bg: "bg-severity-high-surface",
      border: "border-severity-high-border",
      icon: <AlertTriangle className="w-3.5 h-3.5 mr-1 stroke-2" />,
    },
    MEDIUM: {
      label: "Medium Risk",
      text: "text-severity-medium-text",
      bg: "bg-severity-medium-surface",
      border: "border-severity-medium-border",
      icon: <AlertTriangle className="w-3.5 h-3.5 mr-1 stroke-2" />,
    },
    LOW: {
      label: "Low Risk",
      text: "text-severity-low-text",
      bg: "bg-severity-low-surface",
      border: "border-severity-low-border",
      icon: <HelpCircle className="w-3.5 h-3.5 mr-1 stroke-2" />,
    },
    COMPLIANT: {
      label: "Compliant",
      text: "text-severity-compliant-text",
      bg: "bg-severity-compliant-surface",
      border: "border-severity-compliant-border",
      icon: <Check className="w-3.5 h-3.5 mr-1 stroke-2" />,
    },
    UNDECIDED_NEEDS_REVIEW: {
      label: "Needs Review",
      text: "text-severity-undecided-text",
      bg: "bg-severity-undecided-surface",
      border: "border-severity-undecided-border",
      icon: <HelpCircle className="w-3.5 h-3.5 mr-1 stroke-2" />,
    },
  };

  const c = configs[severity] || configs["UNDECIDED_NEEDS_REVIEW"];

  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border",
        c.text,
        c.bg,
        c.border
      )}
    >
      {c.icon}
      {c.label}
    </span>
  );
}

export function CitationBadge({ status }: { status: CitationStatusType | string }) {
  const configs: Record<string, { label: string; text: string; bg: string; border: string; icon: React.ReactNode }> = {
    EXACT_MATCH: {
      label: "Exact Quote",
      text: "text-emerald-800",
      bg: "bg-emerald-50",
      border: "border-emerald-200",
      icon: <Check className="w-3 h-3 mr-1" />,
    },
    NORMALIZED_MATCH: {
      label: "Verified Match",
      text: "text-emerald-800",
      bg: "bg-emerald-50",
      border: "border-emerald-200",
      icon: <Check className="w-3 h-3 mr-1" />,
    },
    APPROXIMATE_NEEDS_REVIEW: {
      label: "Approximate (Unverified)",
      text: "text-amber-800",
      bg: "bg-amber-50",
      border: "border-amber-200",
      icon: <AlertTriangle className="w-3 h-3 mr-1" />,
    },
    UNVERIFIED_MISMATCH: {
      label: "Unverified Quote",
      text: "text-rose-800",
      bg: "bg-rose-50",
      border: "border-rose-200",
      icon: <AlertCircle className="w-3 h-3 mr-1" />,
    },
    NOT_APPLICABLE: {
      label: "Omission / N/A",
      text: "text-slate-600",
      bg: "bg-slate-50",
      border: "border-slate-200",
      icon: <Minus className="w-3 h-3 mr-1" />,
    },
  };

  const c = configs[status] || configs["NOT_APPLICABLE"];

  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-medium border",
        c.text,
        c.bg,
        c.border
      )}
    >
      {c.icon}
      {c.label}
    </span>
  );
}

export function ReviewStatusBadge({ status }: { status: ReviewStatusType | string }) {
  const configs: Record<string, { label: string; text: string; bg: string; border: string }> = {
    AI_EVALUATED: {
      label: "AI Evaluated",
      text: "text-navy",
      bg: "bg-blue-100",
      border: "border-blue-200",
    },
    NEEDS_HUMAN_REVIEW: {
      label: "Needs Review",
      text: "text-amber-800",
      bg: "bg-amber-50",
      border: "border-amber-200",
    },
    HUMAN_CONFIRMED: {
      label: "Human Confirmed",
      text: "text-emerald-800",
      bg: "bg-emerald-50",
      border: "border-emerald-200",
    },
    HUMAN_OVERRIDDEN: {
      label: "Human Overridden",
      text: "text-blue-800",
      bg: "bg-blue-50",
      border: "border-blue-200",
    },
    DISMISSED_FALSE_POSITIVE: {
      label: "Dismissed (FP)",
      text: "text-slate-600",
      bg: "bg-slate-100",
      border: "border-slate-200",
    },
  };

  const c = configs[status] || configs["AI_EVALUATED"];

  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border",
        c.text,
        c.bg,
        c.border
      )}
    >
      {c.label}
    </span>
  );
}

export function ProcessingStatusBadge({ status }: { status: ProcessingStatusType | string }) {
  const configs: Record<string, { label: string; text: string; bg: string; border: string }> = {
    UPLOADED: {
      label: "Uploaded",
      text: "text-slate-700",
      bg: "bg-slate-50",
      border: "border-slate-200",
    },
    PARSING_LAYOUT: {
      label: "Parsing Layout...",
      text: "text-primary",
      bg: "bg-blue-50",
      border: "border-blue-200",
    },
    EXTRACTING_FACTS: {
      label: "Extracting Terms...",
      text: "text-primary",
      bg: "bg-blue-50",
      border: "border-blue-200",
    },
    EVALUATING_PLAYBOOK: {
      label: "Evaluating Rules...",
      text: "text-primary",
      bg: "bg-blue-50",
      border: "border-blue-200",
    },
    READY: {
      label: "Audit Complete",
      text: "text-emerald-800",
      bg: "bg-emerald-50",
      border: "border-emerald-200",
    },
    READY_WITH_WARNINGS: {
      label: "Complete (Warnings)",
      text: "text-amber-800",
      bg: "bg-amber-50",
      border: "border-amber-200",
    },
    FAILED: {
      label: "Failed",
      text: "text-rose-800",
      bg: "bg-rose-50",
      border: "border-rose-200",
    },
  };

  const c = configs[status] || configs["UPLOADED"];

  return (
    <span
      className={cn(
        "inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border",
        c.text,
        c.bg,
        c.border
      )}
    >
      {c.label}
    </span>
  );
}
