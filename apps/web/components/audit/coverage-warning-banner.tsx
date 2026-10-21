import React from "react";
import { AlertTriangle } from "lucide-react";

interface CoverageWarningBannerProps {
  unreadablePages?: number[];
  parserWarnings?: Array<{ page?: number; message?: string; warning_type?: string }>;
}

export function CoverageWarningBanner({ unreadablePages = [], parserWarnings = [] }: CoverageWarningBannerProps) {
  if (unreadablePages.length === 0 && parserWarnings.length === 0) {
    return null;
  }

  return (
    <div className="mb-4 rounded-md bg-amber-50 border border-amber-200 p-3.5 text-sm text-amber-900 shadow-sm" role="alert">
      <div className="flex items-start">
        <AlertTriangle className="h-5 w-5 text-amber-600 mt-0.5 mr-2.5 flex-shrink-0" />
        <div className="flex-1">
          <h4 className="font-semibold text-amber-900">Document Parsing & Text Coverage Warning</h4>
          <p className="mt-1 text-xs text-amber-800 leading-relaxed">
            {unreadablePages.length > 0 && (
              <span>
                Page{unreadablePages.length > 1 ? "s" : ""} <strong>{unreadablePages.join(", ")}</strong> contain minimal or no extractable text layer (e.g. scanned images without OCR).{" "}
              </span>
            )}
            Playbook compliance rules targeting omitted terms on unreadable pages have automatically fallen back to <strong>Needs Review</strong>. Please verify physical document exhibits manually.
          </p>
        </div>
      </div>
    </div>
  );
}
