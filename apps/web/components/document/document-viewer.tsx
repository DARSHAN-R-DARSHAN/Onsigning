"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { useOrg } from "@/lib/org-context";
import { apiClient, ApiError } from "@/lib/api";
import {
  ChevronLeft,
  ChevronRight,
  ZoomIn,
  ZoomOut,
  FileText,
  AlertCircle,
  RefreshCw,
  Loader2,
  ExternalLink,
  Download,
} from "lucide-react";

interface DocumentViewerProps {
  documentId: string;
  documentTitle: string;
  totalPages?: number;
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
}: DocumentViewerProps) {
  const { token } = useAuth();
  const { selectedOrgId } = useOrg();

  const [currentPage, setCurrentPage] = useState<number>(highlightPage || 1);
  const [zoomLevel, setZoomLevel] = useState<number>(100);

  // Streaming blob state and lifecycle
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [documentFormat, setDocumentFormat] = useState<"pdf" | "docx" | "other" | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [statusCode, setStatusCode] = useState<number | null>(null);

  const objectUrlRef = useRef<string | null>(null);
  const activeRequestIdRef = useRef<number>(0);

  // Synchronize incoming citation page navigation
  useEffect(() => {
    if (highlightPage && highlightPage >= 1 && highlightPage <= (totalPages || 1)) {
      setCurrentPage(highlightPage);
    }
  }, [highlightPage, totalPages]);

  const fetchContent = useCallback(async () => {
    if (!documentId) {
      setIsLoading(false);
      return;
    }

    if (!token || !selectedOrgId) {
      setIsLoading(false);
      return;
    }

    const currentRequestId = ++activeRequestIdRef.current;
    setIsLoading(true);
    setError(null);
    setStatusCode(null);

    // Clean up previous blob URL before loading new document
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
    setBlobUrl(null);
    setDocumentFormat(null);

    try {
      const blob = await apiClient.getBlob(
        `/api/v1/documents/${documentId}/content`,
        {
          token,
          organizationId: selectedOrgId,
        }
      );

      // Prevent stale asynchronous responses from overriding the active document
      if (currentRequestId !== activeRequestIdRef.current) {
        return;
      }

      const url = URL.createObjectURL(blob);
      objectUrlRef.current = url;
      setBlobUrl(url);

      if (blob.type.includes("pdf") || blob.type === "application/pdf") {
        setDocumentFormat("pdf");
      } else if (
        blob.type.includes("wordprocessingml") ||
        blob.type.includes("docx") ||
        documentTitle.toLowerCase().endsWith(".docx")
      ) {
        setDocumentFormat("docx");
      } else {
        setDocumentFormat("other");
      }
    } catch (err: unknown) {
      if (currentRequestId !== activeRequestIdRef.current) {
        return;
      }

      if (err instanceof ApiError) {
        setStatusCode(err.status);
        setError(err.message);
      } else {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to stream document content. Please check your network connection."
        );
      }
    } finally {
      if (currentRequestId === activeRequestIdRef.current) {
        setIsLoading(false);
      }
    }
  }, [documentId, token, selectedOrgId, documentTitle]);

  useEffect(() => {
    fetchContent();

    return () => {
      // Revoke object URL on unmount or document change
      if (objectUrlRef.current) {
        URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = null;
      }
    };
  }, [fetchContent]);

  const handlePrevPage = () => {
    if (currentPage > 1) {
      setCurrentPage((p) => p - 1);
    }
  };

  const handleNextPage = () => {
    if (currentPage < (totalPages || 1)) {
      setCurrentPage((p) => p + 1);
    }
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

  const handleOpenInNewTab = () => {
    if (blobUrl) {
      window.open(blobUrl, "_blank", "noopener,noreferrer");
    }
  };

  return (
    <div className="flex flex-col h-full bg-slate-100 border-r border-border select-none overflow-hidden">
      {/* Top Toolbar */}
      <div className="flex items-center justify-between px-4 py-2 bg-white border-b border-border shadow-sm flex-wrap gap-2 flex-shrink-0 z-10">
        {/* Document Info */}
        <div className="flex items-center space-x-2 min-w-0">
          <FileText className="w-4 h-4 text-primary flex-shrink-0" />
          <span
            className="text-xs font-semibold text-navy truncate max-w-[180px] sm:max-w-[220px]"
            title={documentTitle}
          >
            {documentTitle}
          </span>
        </div>

        {/* Page Navigation */}
        <div className="flex items-center space-x-1.5">
          <Button
            variant="secondary"
            size="sm"
            onClick={handlePrevPage}
            disabled={currentPage <= 1 || isLoading || documentFormat !== "pdf"}
            className="h-7 w-7 p-0"
            title="Previous Page"
            aria-label="Previous Page"
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
            disabled={currentPage >= (totalPages || 1) || isLoading || documentFormat !== "pdf"}
            className="h-7 w-7 p-0"
            title="Next Page"
            aria-label="Next Page"
          >
            <ChevronRight className="w-4 h-4" />
          </Button>
        </div>

        {/* Zoom & Popout Controls */}
        <div className="flex items-center space-x-1">
          <Button
            variant="secondary"
            size="sm"
            onClick={handleZoomOut}
            disabled={zoomLevel <= 70 || isLoading || documentFormat !== "pdf"}
            className="h-7 w-7 p-0"
            title="Zoom Out"
            aria-label="Zoom Out"
          >
            <ZoomOut className="w-3.5 h-3.5" />
          </Button>
          <button
            onClick={handleResetZoom}
            disabled={isLoading || documentFormat !== "pdf"}
            className="text-xs font-mono text-slate-600 px-1.5 hover:text-navy disabled:opacity-50"
            title="Reset to 100%"
            aria-label="Reset Zoom"
          >
            {zoomLevel}%
          </button>
          <Button
            variant="secondary"
            size="sm"
            onClick={handleZoomIn}
            disabled={zoomLevel >= 200 || isLoading || documentFormat !== "pdf"}
            className="h-7 w-7 p-0"
            title="Zoom In"
            aria-label="Zoom In"
          >
            <ZoomIn className="w-3.5 h-3.5" />
          </Button>

          {blobUrl && (
            <Button
              variant="secondary"
              size="sm"
              onClick={handleOpenInNewTab}
              className="h-7 w-7 p-0 ml-1"
              title="Open document in new tab"
              aria-label="Open document in new tab"
            >
              <ExternalLink className="w-3.5 h-3.5" />
            </Button>
          )}
        </div>
      </div>

      {/* Verified Citation Highlight Banner (When quote is selected) */}
      {highlightQuote && (
        <div className="bg-amber-50 border-b border-amber-200 px-4 py-2 text-xs flex items-center justify-between flex-shrink-0">
          <div className="flex items-center overflow-hidden mr-2">
            <span className="font-semibold text-amber-900 mr-2 flex-shrink-0">
              Citation Reference:
            </span>
            <span className="italic text-amber-800 truncate font-serif">
              &ldquo;{highlightQuote}&rdquo;
            </span>
          </div>
          <span className="text-[11px] font-mono text-amber-700 bg-amber-100/80 px-2 py-0.5 rounded flex-shrink-0">
            Page {currentPage}
          </span>
        </div>
      )}

      {/* Main Document Viewport */}
      <div className="flex-1 overflow-hidden relative flex flex-col items-center justify-center bg-slate-100">
        {/* Loading State */}
        {isLoading && (
          <div className="flex flex-col items-center justify-center p-8 text-center">
            <Loader2 className="w-8 h-8 text-primary animate-spin mb-3" />
            <h3 className="text-sm font-semibold text-navy">Streaming document content...</h3>
            <p className="text-xs text-text-secondary mt-1">
              Loading contract agreement securely from workspace storage
            </p>
          </div>
        )}

        {/* Error States */}
        {!isLoading && error && (
          <div className="flex flex-col items-center justify-center p-8 text-center max-w-md bg-white rounded-lg border border-border shadow-card m-6">
            <AlertCircle
              className={`w-10 h-10 mb-2 ${
                statusCode === 403 || statusCode === 401
                  ? "text-rose-600"
                  : statusCode === 404
                  ? "text-amber-500"
                  : "text-rose-500"
              }`}
            />
            <h3 className="text-sm font-semibold text-navy">
              {statusCode === 401 || statusCode === 403
                ? "Access Denied"
                : statusCode === 404
                ? "Document Content Not Found"
                : "Unable to Load Document Content"}
            </h3>
            <p className="text-xs text-text-secondary mt-1">{error}</p>
            <Button
              variant="secondary"
              size="sm"
              onClick={fetchContent}
              className="mt-4"
            >
              <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Retry
            </Button>
          </div>
        )}

        {/* Unsupported In-Browser Format (e.g. DOCX) */}
        {!isLoading && !error && documentFormat === "docx" && (
          <div className="flex flex-col items-center justify-center p-8 text-center max-w-lg bg-white rounded-lg border border-border shadow-card m-6">
            <FileText className="w-12 h-12 text-primary mb-3" />
            <h3 className="text-base font-semibold text-navy">{documentTitle}</h3>
            <span className="mt-1 px-2.5 py-0.5 rounded text-[11px] font-mono bg-blue-50 text-primary border border-blue-200">
              Microsoft Word (.docx)
            </span>
            <p className="text-xs text-text-secondary mt-3 leading-relaxed">
              This agreement was uploaded as a DOCX file. AI Playbook audits and clause citations are fully extracted and evaluated in the Findings report. Inline page rendering requires PDF format.
            </p>
            {blobUrl && (
              <a
                href={blobUrl}
                download={
                  documentTitle.toLowerCase().endsWith(".docx")
                    ? documentTitle
                    : `${documentTitle}.docx`
                }
                className="mt-4 inline-block"
              >
                <Button variant="primary" size="md">
                  <Download className="w-4 h-4 mr-2" /> Download Original Document
                </Button>
              </a>
            )}
          </div>
        )}

        {/* Unrecognized Format */}
        {!isLoading && !error && documentFormat === "other" && (
          <div className="flex flex-col items-center justify-center p-8 text-center max-w-md bg-white rounded-lg border border-border shadow-card m-6">
            <AlertCircle className="w-10 h-10 text-amber-500 mb-2" />
            <h3 className="text-sm font-semibold text-navy">Unsupported Document Format</h3>
            <p className="text-xs text-text-secondary mt-1">
              Inline viewing is not supported for this file format. You can download the file directly.
            </p>
            {blobUrl && (
              <a href={blobUrl} download={documentTitle} className="mt-4 inline-block">
                <Button variant="secondary" size="md">
                  <Download className="w-4 h-4 mr-2" /> Download File
                </Button>
              </a>
            )}
          </div>
        )}

        {/* Real PDF Document Rendering */}
        {!isLoading && !error && documentFormat === "pdf" && blobUrl && (
          <div className="w-full h-full flex flex-col items-center justify-center">
            <object
              key={`${blobUrl}-p${currentPage}`}
              data={`${blobUrl}#page=${currentPage}&zoom=${zoomLevel}`}
              type="application/pdf"
              className="w-full h-full border-0"
              aria-label={`PDF document viewer: ${documentTitle}`}
            >
              {/* Fallback for browsers without inline PDF viewing plugin */}
              <div className="p-8 text-center text-xs text-text-secondary max-w-md bg-white rounded-lg border border-border shadow-card m-auto">
                <FileText className="w-10 h-10 text-primary mx-auto mb-2" />
                <h4 className="font-semibold text-navy text-sm">Inline PDF Viewing Unsupported</h4>
                <p className="mt-1">
                  Your browser does not support embedded PDF rendering. You can open the contract in a separate viewer tab.
                </p>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={handleOpenInNewTab}
                  className="mt-4"
                >
                  <ExternalLink className="w-3.5 h-3.5 mr-1.5" /> Open PDF in New Tab
                </Button>
              </div>
            </object>
          </div>
        )}
      </div>
    </div>
  );
}
