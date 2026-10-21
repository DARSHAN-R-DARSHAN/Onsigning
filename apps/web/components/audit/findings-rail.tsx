"use client";

import React, { useState, useEffect, useCallback } from "react";
import { AlertCard, RiskAlertData } from "./alert-card";
import { CoverageWarningBanner } from "./coverage-warning-banner";
import { ReviewActionDialog } from "./review-action-dialog";
import { Button } from "@/components/ui/button";
import { ShieldCheck, BookOpen, Filter, RefreshCw, Keyboard } from "lucide-react";
import { cn } from "@/lib/utils";

interface PlaybookSummary {
  name: string;
  version: number;
  description?: string;
}

interface FindingsRailProps {
  playbook: PlaybookSummary;
  alerts: RiskAlertData[];
  unreadablePages?: number[];
  parserWarnings?: Array<{ page?: number; message?: string }>;
  onSelectAlert: (alert: RiskAlertData) => void;
  onUpdateAlertReview: (alertId: string, status: string, note: string) => Promise<void>;
  onRerunAudit?: () => void;
  isRerunning?: boolean;
}

export function FindingsRail({
  playbook,
  alerts = [],
  unreadablePages = [],
  parserWarnings = [],
  onSelectAlert,
  onUpdateAlertReview,
  onRerunAudit,
  isRerunning = false,
}: FindingsRailProps) {
  const [selectedIndex, setSelectedIndex] = useState<number>(0);
  const [filterMode, setFilterMode] = useState<"ALL" | "UNRESOLVED" | "CONFIRMED">("ALL");
  const [activeDialog, setActiveDialog] = useState<{
    alert: RiskAlertData;
    action: "CONFIRM" | "OVERRIDE" | "DISMISS";
  } | null>(null);

  // Filter alerts based on selection
  const filteredAlerts = alerts.filter((a) => {
    if (filterMode === "UNRESOLVED") {
      return a.review_status === "AI_EVALUATED" || a.review_status === "NEEDS_HUMAN_REVIEW";
    }
    if (filterMode === "CONFIRMED") {
      return ["HUMAN_CONFIRMED", "HUMAN_OVERRIDDEN", "DISMISSED_FALSE_POSITIVE"].includes(
        a.review_status
      );
    }
    return true;
  });

  const selectedAlert = filteredAlerts[selectedIndex] || filteredAlerts[0] || null;

  // Sync selected alert with parent
  useEffect(() => {
    if (selectedAlert) {
      onSelectAlert(selectedAlert);
    }
  }, [selectedIndex, filteredAlerts]);

  // Keyboard navigation shortcuts (j/k/Enter/c/o/d)
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      // Avoid firing if an input or textarea is active
      if (["INPUT", "TEXTAREA"].includes((e.target as HTMLElement)?.tagName)) {
        return;
      }

      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((prev) => Math.min(prev + 1, filteredAlerts.length - 1));
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((prev) => Math.max(prev - 1, 0));
      } else if (e.key === "Enter" && selectedAlert) {
        e.preventDefault();
        if (selectedAlert.evidence.page_number) {
          onSelectAlert(selectedAlert);
        }
      } else if (e.key === "c" && selectedAlert) {
        e.preventDefault();
        setActiveDialog({ alert: selectedAlert, action: "CONFIRM" });
      } else if (e.key === "o" && selectedAlert) {
        e.preventDefault();
        setActiveDialog({ alert: selectedAlert, action: "OVERRIDE" });
      } else if (e.key === "d" && selectedAlert) {
        e.preventDefault();
        setActiveDialog({ alert: selectedAlert, action: "DISMISS" });
      }
    },
    [filteredAlerts, selectedAlert, onSelectAlert]
  );

  useEffect(() => {
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  // Severity Rollup Breakdown Counts
  const criticalCount = alerts.filter((a) => a.severity === "CRITICAL").length;
  const highCount = alerts.filter((a) => a.severity === "HIGH").length;
  const mediumCount = alerts.filter((a) => a.severity === "MEDIUM").length;
  const lowCount = alerts.filter((a) => a.severity === "LOW").length;
  const undecidedCount = alerts.filter((a) => a.severity === "UNDECIDED_NEEDS_REVIEW").length;

  return (
    <div className="flex flex-col h-full bg-canvas border-l border-border overflow-hidden">
      {/* Playbook Header */}
      <div className="p-4 bg-white border-b border-border shadow-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <BookOpen className="w-4 h-4 text-primary" />
            <span className="text-xs font-semibold uppercase tracking-wider text-primary">
              Playbook Audit
            </span>
          </div>

          {onRerunAudit && (
            <Button
              variant="secondary"
              size="sm"
              onClick={onRerunAudit}
              isLoading={isRerunning}
              className="h-7 text-xs"
              title="Re-run audit rules against this agreement"
            >
              <RefreshCw className="w-3 h-3 mr-1" /> Re-audit
            </Button>
          )}
        </div>

        <div className="mt-2">
          <h2 className="text-sm font-bold text-navy flex items-center">
            {playbook.name}
            <span className="ml-2 px-1.5 py-0.2 bg-blue-100 text-primary text-[10px] rounded font-mono font-medium">
              v{playbook.version}
            </span>
          </h2>
          {playbook.description && (
            <p className="text-xs text-text-secondary mt-0.5 line-clamp-1">{playbook.description}</p>
          )}
        </div>

        {/* Severity Metrics Rollup */}
        <div className="mt-3.5 pt-3 border-t border-slate-100 grid grid-cols-5 gap-1.5 text-center">
          <div className="bg-severity-critical-surface border border-severity-critical-border rounded py-1 px-1">
            <div className="text-xs font-bold text-severity-critical-text">{criticalCount}</div>
            <div className="text-[10px] uppercase font-medium text-severity-critical-text">Critical</div>
          </div>
          <div className="bg-severity-high-surface border border-severity-high-border rounded py-1 px-1">
            <div className="text-xs font-bold text-severity-high-text">{highCount}</div>
            <div className="text-[10px] uppercase font-medium text-severity-high-text">High</div>
          </div>
          <div className="bg-severity-medium-surface border border-severity-medium-border rounded py-1 px-1">
            <div className="text-xs font-bold text-severity-medium-text">{mediumCount}</div>
            <div className="text-[10px] uppercase font-medium text-severity-medium-text">Medium</div>
          </div>
          <div className="bg-severity-low-surface border border-severity-low-border rounded py-1 px-1">
            <div className="text-xs font-bold text-severity-low-text">{lowCount}</div>
            <div className="text-[10px] uppercase font-medium text-severity-low-text">Low</div>
          </div>
          <div className="bg-severity-undecided-surface border border-severity-undecided-border rounded py-1 px-1">
            <div className="text-xs font-bold text-severity-undecided-text">{undecidedCount}</div>
            <div className="text-[10px] uppercase font-medium text-severity-undecided-text">Review</div>
          </div>
        </div>
      </div>

      {/* Filter Tabs & Keyboard Tip */}
      <div className="px-4 py-2 bg-canvas border-b border-border flex items-center justify-between text-xs">
        <div className="flex items-center space-x-1">
          <button
            onClick={() => { setFilterMode("ALL"); setSelectedIndex(0); }}
            className={cn(
              "px-2.5 py-1 rounded text-xs font-medium transition",
              filterMode === "ALL" ? "bg-white text-primary font-semibold shadow-sm" : "text-slate-600 hover:text-navy"
            )}
          >
            All ({alerts.length})
          </button>
          <button
            onClick={() => { setFilterMode("UNRESOLVED"); setSelectedIndex(0); }}
            className={cn(
              "px-2.5 py-1 rounded text-xs font-medium transition",
              filterMode === "UNRESOLVED" ? "bg-white text-primary font-semibold shadow-sm" : "text-slate-600 hover:text-navy"
            )}
          >
            Unresolved ({alerts.filter(a => a.review_status === "AI_EVALUATED" || a.review_status === "NEEDS_HUMAN_REVIEW").length})
          </button>
          <button
            onClick={() => { setFilterMode("CONFIRMED"); setSelectedIndex(0); }}
            className={cn(
              "px-2.5 py-1 rounded text-xs font-medium transition",
              filterMode === "CONFIRMED" ? "bg-white text-primary font-semibold shadow-sm" : "text-slate-600 hover:text-navy"
            )}
          >
            Actioned
          </button>
        </div>

        <div className="hidden lg:flex items-center text-[10px] text-text-secondary" title="Use 'j' / 'k' to navigate findings, 'c' to confirm, 'o' to override, 'd' to dismiss">
          <Keyboard className="w-3 h-3 mr-1" />
          <span><kbd className="font-mono bg-white px-1 border rounded">j</kbd>/<kbd className="font-mono bg-white px-1 border rounded">k</kbd> navigate</span>
        </div>
      </div>

      {/* Alerts Scroll Rail */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {/* Coverage Warning Banner */}
        <CoverageWarningBanner unreadablePages={unreadablePages} parserWarnings={parserWarnings} />

        {filteredAlerts.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border bg-white p-8 text-center text-xs text-text-secondary">
            <ShieldCheck className="w-8 h-8 text-emerald-600 mx-auto mb-2" />
            <p className="font-medium text-navy">No risk findings in this filter view.</p>
            <p className="mt-1 text-slate-500">All terms comply with active playbook policy requirements.</p>
          </div>
        ) : (
          filteredAlerts.map((alert, idx) => (
            <AlertCard
              key={alert.id}
              alert={alert}
              isSelected={selectedAlert?.id === alert.id}
              onSelect={() => {
                setSelectedIndex(idx);
                onSelectAlert(alert);
              }}
              onNavigateToCitation={(page, quote) => {
                setSelectedIndex(idx);
                onSelectAlert(alert);
              }}
              onReviewAction={(action) => {
                setActiveDialog({ alert, action });
              }}
            />
          ))
        )}
      </div>

      {/* Review Action Modal Dialog */}
      {activeDialog && (
        <ReviewActionDialog
          isOpen={true}
          onClose={() => setActiveDialog(null)}
          actionType={activeDialog.action}
          alertTitle={activeDialog.alert.title}
          ruleId={activeDialog.alert.rule_id}
          onSubmit={async (status, note) => {
            await onUpdateAlertReview(activeDialog.alert.id, status, note);
            // Optimistic update locally
            activeDialog.alert.review_status = status;
            activeDialog.alert.review_note = note;
          }}
        />
      )}
    </div>
  );
}
