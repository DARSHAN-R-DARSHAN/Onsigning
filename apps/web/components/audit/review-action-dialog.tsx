"use client";

import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { X, CheckCircle, Edit3, Trash2 } from "lucide-react";

interface ReviewActionDialogProps {
  isOpen: boolean;
  onClose: () => void;
  actionType: "CONFIRM" | "OVERRIDE" | "DISMISS" | null;
  alertTitle: string;
  ruleId: string;
  onSubmit: (status: string, note: string) => Promise<void>;
}

export function ReviewActionDialog({
  isOpen,
  onClose,
  actionType,
  alertTitle,
  ruleId,
  onSubmit,
}: ReviewActionDialogProps) {
  const [note, setNote] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen || !actionType) return null;

  const requiresNote = actionType === "OVERRIDE" || actionType === "DISMISS";

  const config = {
    CONFIRM: {
      title: "Confirm Risk Finding",
      icon: <CheckCircle className="w-5 h-5 text-emerald-600 mr-2" />,
      description: "Confirm that this risk finding is valid and will be tracked under the contract.",
      targetStatus: "HUMAN_CONFIRMED",
      buttonText: "Confirm Finding",
      buttonVariant: "primary" as const,
      notePlaceholder: "Optional reviewer notes or context...",
    },
    OVERRIDE: {
      title: "Override Risk Finding",
      icon: <Edit3 className="w-5 h-5 text-blue-600 mr-2" />,
      description: "Override this automated finding (e.g. exception approved by GC, modified threshold).",
      targetStatus: "HUMAN_OVERRIDDEN",
      buttonText: "Save Override",
      buttonVariant: "primary" as const,
      notePlaceholder: "Mandatory justification: explain why this finding is being overridden...",
    },
    DISMISS: {
      title: "Dismiss as False Positive",
      icon: <Trash2 className="w-5 h-5 text-rose-600 mr-2" />,
      description: "Dismiss this finding if the clause was misinterpreted or does not represent a real risk.",
      targetStatus: "DISMISSED_FALSE_POSITIVE",
      buttonText: "Dismiss Finding",
      buttonVariant: "destructive" as const,
      notePlaceholder: "Mandatory justification: explain why this is a false positive...",
    },
  }[actionType];

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (requiresNote && !note.trim()) {
      setError("A written justification note is mandatory for overrides and dismissals.");
      return;
    }

    try {
      setIsSubmitting(true);
      setError(null);
      await onSubmit(config.targetStatus, note.trim());
      setNote("");
      onClose();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to submit review decision.";
      setError(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 animate-in fade-in duration-150">
      <div className="bg-white rounded-lg shadow-xl max-w-lg w-full border border-border overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-border bg-canvas">
          <div className="flex items-center">
            {config.icon}
            <h3 className="font-semibold text-navy text-base">{config.title}</h3>
          </div>
          <button
            onClick={onClose}
            className="text-text-secondary hover:text-navy rounded p-1 hover:bg-blue-100 transition"
            aria-label="Close dialog"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          <div>
            <p className="text-xs text-text-secondary uppercase font-semibold tracking-wider">Target Rule</p>
            <p className="text-sm font-medium text-navy mt-0.5">{alertTitle}</p>
            <p className="text-xs font-mono text-slate-500 mt-0.5">{ruleId}</p>
          </div>

          <p className="text-xs text-text-secondary leading-relaxed">{config.description}</p>

          <div>
            <label className="block text-xs font-medium text-navy mb-1">
              Reviewer Notes {requiresNote ? <span className="text-rose-600">* (Mandatory)</span> : <span className="text-text-secondary">(Optional)</span>}
            </label>
            <textarea
              value={note}
              onChange={(e) => {
                setNote(e.target.value);
                if (error) setError(null);
              }}
              rows={3}
              placeholder={config.notePlaceholder}
              className="w-full text-sm rounded-md border border-border p-2.5 focus:border-primary focus:ring-1 focus:ring-primary outline-none transition"
              required={requiresNote}
            />
          </div>

          {error && (
            <div className="text-xs text-rose-600 bg-rose-50 border border-rose-200 rounded p-2">
              {error}
            </div>
          )}

          <div className="flex items-center justify-end space-x-2.5 pt-2 border-t border-border">
            <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant={config.buttonVariant}
              size="sm"
              isLoading={isSubmitting}
            >
              {config.buttonText}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
