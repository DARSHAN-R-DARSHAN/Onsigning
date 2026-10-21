"use client";

import React, { useState, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import {
  ChevronLeft,
  ChevronRight,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Search,
  FileText,
  AlertCircle,
  ExternalLink,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface DocumentViewerProps {
  documentId: string;
  documentTitle: string;
  totalPages: number;
  highlightPage?: number;
  highlightQuote?: string;
  pdfUrl?: string;
}

export function DocumentViewer({
  documentId,
  documentTitle,
  totalPages = 1,
  highlightPage = 1,
  highlightQuote,
  pdfUrl,
}: DocumentViewerProps) {
  const [currentPage, setCurrentPage] = useState<number>(highlightPage || 1);
  const [zoomLevel, setZoomLevel] = useState<number>(100);
  const [searchQuery, setSearchQuery] = useState("");
  const pageContainerRef = useRef<HTMLDivElement>(null);

  // Synchronize incoming highlight navigation
  useEffect(() => {
    if (highlightPage && highlightPage >= 1 && highlightPage <= totalPages) {
      setCurrentPage(highlightPage);
      if (pageContainerRef.current) {
        pageContainerRef.current.scrollTop = 0;
      }
    }
  }, [highlightPage, totalPages]);

  const handlePrevPage = () => {
    if (currentPage > 1) setCurrentPage((p) => p - 1);
  };

  const handleNextPage = () => {
    if (currentPage < totalPages) setCurrentPage((p) => p + 1);
  };

  const handleZoomIn = () => {
    setZoomLevel((z) => Math.min(z + 15, 200));
  };

  const handleZoomOut = () => {
    setZoomLevel((z) => Math.max(z - 15, 70));
  };

  const handleResetZoom = () => {
    setZoomLevel(100);
  };

  // Content stream endpoint
  const contentStreamUrl = pdfUrl || `/api/v1/documents/${documentId}/content`;

  return (
    <div className="flex flex-col h-full bg-slate-100 border-r border-border select-none">
      {/* Top Toolbar */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-white border-b border-border shadow-sm flex-wrap gap-2">
        {/* Document Info */}
        <div className="flex items-center space-x-2 min-w-0">
          <FileText className="w-4 h-4 text-primary flex-shrink-0" />
          <span className="text-xs font-semibold text-navy truncate max-w-[200px]" title={documentTitle}>
            {documentTitle}
          </span>
        </div>

        {/* Page Navigation */}
        <div className="flex items-center space-x-1.5">
          <Button
            variant="secondary"
            size="sm"
            onClick={handlePrevPage}
            disabled={currentPage <= 1}
            className="h-7 w-7 p-0"
            title="Previous Page"
          >
            <ChevronLeft className="w-4 h-4" />
          </Button>
          <span className="text-xs font-medium text-navy px-1">
            Page {currentPage} of {totalPages || 1}
          </span>
          <Button
            variant="secondary"
            size="sm"
            onClick={handleNextPage}
            disabled={currentPage >= totalPages}
            className="h-7 w-7 p-0"
            title="Next Page"
          >
            <ChevronRight className="w-4 h-4" />
          </Button>
        </div>

        {/* Zoom Controls */}
        <div className="flex items-center space-x-1">
          <Button
            variant="secondary"
            size="sm"
            onClick={handleZoomOut}
            disabled={zoomLevel <= 70}
            className="h-7 w-7 p-0"
            title="Zoom Out"
          >
            <ZoomOut className="w-3.5 h-3.5" />
          </Button>
          <button
            onClick={handleResetZoom}
            className="text-xs font-mono text-slate-600 px-1.5 hover:text-navy"
            title="Reset to 100%"
          >
            {zoomLevel}%
          </button>
          <Button
            variant="secondary"
            size="sm"
            onClick={handleZoomIn}
            disabled={zoomLevel >= 200}
            className="h-7 w-7 p-0"
            title="Zoom In"
          >
            <ZoomIn className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

      {/* Verified Citation Highlight Banner (When quote is selected) */}
      {highlightQuote && (
        <div className="bg-amber-50 border-b border-amber-200 px-4 py-2 text-xs flex items-center justify-between">
          <div className="flex items-center overflow-hidden mr-2">
            <span className="font-semibold text-amber-900 mr-2 flex-shrink-0">Verified Quote:</span>
            <span className="italic text-amber-800 truncate font-serif">"{highlightQuote}"</span>
          </div>
          <span className="text-[11px] font-mono text-amber-700 bg-amber-100/80 px-2 py-0.5 rounded flex-shrink-0">
            Page {currentPage}
          </span>
        </div>
      )}

      {/* Main Canvas Viewport */}
      <div
        ref={pageContainerRef}
        className="flex-1 overflow-auto p-6 flex justify-center items-start"
      >
        <div
          style={{ transform: `scale(${zoomLevel / 100})`, transformOrigin: "top center" }}
          className="transition-transform duration-100 ease-out"
        >
          {/* Simulated High-Fidelity PDF Page Sheet */}
          <div className="w-[612px] min-h-[792px] bg-white rounded shadow-lg border border-slate-300 p-12 text-navy relative select-text">
            {/* Header / Page provenance */}
            <div className="flex justify-between items-center text-[10px] text-slate-400 border-b border-slate-100 pb-2 mb-8">
              <span>{documentTitle}</span>
              <span>Page {currentPage} of {totalPages}</span>
            </div>

            {/* Document Page Content Layer */}
            <div className="space-y-4 text-xs leading-relaxed text-slate-800 font-serif">
              <p className="font-sans font-bold text-sm text-navy uppercase tracking-wide">
                Master Services Agreement — Page {currentPage}
              </p>

              {highlightQuote && (
                <div className="my-6 p-4 rounded bg-amber-100/70 border-l-4 border-amber-500 shadow-sm transition-all duration-200 animate-in fade-in">
                  <div className="flex items-center text-[10px] uppercase font-sans font-semibold text-amber-800 tracking-wider mb-1">
                    <span className="w-2 h-2 rounded-full bg-amber-500 mr-1.5 animate-pulse" />
                    Verified Evidence Citation (Page {currentPage})
                  </div>
                  <p className="font-serif italic text-sm text-navy font-medium leading-relaxed">
                    "{highlightQuote}"
                  </p>
                </div>
              )}

              <p className="leading-relaxed text-justify text-slate-700">
                This Master Services Agreement ("Agreement") governs the procurement and provision of enterprise software and related professional services between the Parties. Each Party covenants that it is duly organized, validly existing, and in good standing under the laws of its jurisdiction of incorporation.
              </p>

              <p className="leading-relaxed text-justify text-slate-700">
                Except as expressly provided herein, neither party makes any warranties of any kind, whether express, implied, statutory or otherwise, and each party specifically disclaims all implied warranties, including any warranties of merchantability or fitness for a particular purpose, to the maximum extent permitted by applicable law.
              </p>

              <p className="leading-relaxed text-justify text-slate-700">
                In the event of any conflict or inconsistency between the terms of this Agreement and any Exhibit, Statement of Work, or Schedule, the terms of this Agreement shall govern unless the Exhibit explicitly states otherwise and references the specific section to be modified.
              </p>
            </div>

            {/* Footer */}
            <div className="absolute bottom-6 left-12 right-12 text-center text-[10px] text-slate-400 border-t border-slate-100 pt-2">
              Confidential · For Internal Legal Audit Only · Page {currentPage}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
