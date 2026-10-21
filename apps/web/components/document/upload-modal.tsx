"use client";

import React, { useState, useRef } from "react";
import { Button } from "@/components/ui/button";
import { X, UploadCloud, FileText, AlertCircle, CheckCircle2 } from "lucide-react";
import { formatBytes } from "@/lib/utils";

interface UploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (documentId: string) => void;
}

export function UploadModal({ isOpen, onClose, onSuccess }: UploadModalProps) {
  const [dragActive, setDragActive] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [hierarchyType, setHierarchyType] = useState("MASTER_SERVICES_AGREEMENT");
  const [autoAudit, setAutoAudit] = useState(true);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  if (!isOpen) return null;

  const validateAndSetFile = (file: File) => {
    setError(null);

    // 1. Check legacy binary .doc rejection
    if (file.name.toLowerCase().endsWith(".doc")) {
      setError("Legacy .doc files are not supported. Please upload a .docx or .pdf file.");
      setSelectedFile(null);
      return;
    }

    // 2. Validate supported extensions (.pdf and .docx)
    if (!file.name.toLowerCase().endsWith(".pdf") && !file.name.toLowerCase().endsWith(".docx")) {
      setError("Unsupported file format. Please upload an enterprise agreement as a PDF or modern DOCX.");
      setSelectedFile(null);
      return;
    }

    // 3. Operational baseline: 35 MB maximum size limit
    const maxBytes = 35 * 1024 * 1024;
    if (file.size > maxBytes) {
      setError(`File size (${formatBytes(file.size)}) exceeds the maximum allowed limit of 35 MB.`);
      setSelectedFile(null);
      return;
    }

    setSelectedFile(file);
  };

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      validateAndSetFile(e.dataTransfer.files[0]);
    }
  };

  const handleUpload = async () => {
    if (!selectedFile) return;

    try {
      setIsUploading(true);
      setError(null);

      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("hierarchy_type", hierarchyType);
      formData.append("auto_audit", autoAudit ? "true" : "false");

      // In real deployment, auth headers are injected via Supabase session
      const res = await fetch("/api/v1/documents/upload", {
        method: "POST",
        body: formData,
      });

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.message || `Upload failed with status ${res.status}`);
      }

      const data = await res.json();
      onSuccess(data.id);
      onClose();
    } catch (err: any) {
      setError(err?.message || "Failed to upload document. Please try again.");
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 animate-in fade-in duration-150">
      <div className="bg-white rounded-lg shadow-xl max-w-lg w-full border border-border overflow-hidden">
        {/* Modal Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border bg-canvas">
          <div className="flex items-center space-x-2">
            <UploadCloud className="w-5 h-5 text-primary" />
            <h3 className="font-semibold text-navy text-base">Upload Agreement</h3>
          </div>
          <button
            onClick={onClose}
            className="text-text-secondary hover:text-navy rounded p-1 hover:bg-blue-100 transition"
            aria-label="Close upload dialog"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-6 space-y-5">
          {/* Dropzone */}
          <div
            onDragEnter={handleDrag}
            onDragLeave={handleDrag}
            onDragOver={handleDrag}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors ${
              dragActive
                ? "border-primary bg-blue-50/70"
                : selectedFile
                ? "border-emerald-400 bg-emerald-50/30"
                : "border-border hover:border-blue-300 hover:bg-canvas"
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx,.doc"
              onChange={(e) => {
                if (e.target.files && e.target.files[0]) {
                  validateAndSetFile(e.target.files[0]);
                }
              }}
              className="hidden"
            />

            {selectedFile ? (
              <div className="flex flex-col items-center">
                <CheckCircle2 className="w-10 h-10 text-emerald-600 mb-2" />
                <p className="font-medium text-sm text-navy">{selectedFile.name}</p>
                <p className="text-xs text-text-secondary mt-1">{formatBytes(selectedFile.size)}</p>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedFile(null);
                  }}
                  className="mt-2 text-xs text-primary hover:underline"
                >
                  Change file
                </button>
              </div>
            ) : (
              <div className="flex flex-col items-center">
                <UploadCloud className="w-10 h-10 text-primary mb-2" />
                <p className="font-medium text-sm text-navy">
                  Click to select or drag and drop agreement
                </p>
                <p className="text-xs text-text-secondary mt-1">
                  Supported formats: PDF and modern DOCX (up to 35 MB)
                </p>
              </div>
            )}
          </div>

          {/* Error Message */}
          {error && (
            <div className="flex items-start rounded-md bg-rose-50 border border-rose-200 p-3 text-xs text-rose-800">
              <AlertCircle className="w-4 h-4 text-rose-600 mr-2 flex-shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}

          {/* Form Options */}
          <div className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-navy uppercase tracking-wider mb-1.5">
                Document Hierarchy Type
              </label>
              <select
                value={hierarchyType}
                onChange={(e) => setHierarchyType(e.target.value)}
                className="w-full text-sm rounded-md border border-border p-2.5 bg-white text-navy focus:border-primary focus:ring-1 focus:ring-primary outline-none"
              >
                <option value="MASTER_SERVICES_AGREEMENT">Master Services Agreement (MSA)</option>
                <option value="AMENDMENT">Amendment / Addendum</option>
                <option value="STATEMENT_OF_WORK">Statement of Work (SOW)</option>
                <option value="ORDER_FORM">Order Form / Schedule</option>
                <option value="OTHER">Other Standalone Agreement</option>
              </select>
            </div>

            <div className="flex items-center space-x-2">
              <input
                id="auto-audit"
                type="checkbox"
                checked={autoAudit}
                onChange={(e) => setAutoAudit(e.target.checked)}
                className="rounded border-border text-primary focus:ring-primary h-4 w-4"
              />
              <label htmlFor="auto-audit" className="text-xs text-navy font-medium cursor-pointer">
                Automatically execute Risk Playbook audit upon upload
              </label>
            </div>
          </div>
        </div>

        {/* Modal Footer */}
        <div className="flex items-center justify-end space-x-2.5 px-6 py-4 bg-canvas border-t border-border">
          <Button variant="secondary" size="md" onClick={onClose} disabled={isUploading}>
            Cancel
          </Button>
          <Button
            variant="primary"
            size="md"
            onClick={handleUpload}
            disabled={!selectedFile || isUploading}
            isLoading={isUploading}
          >
            {isUploading ? "Uploading..." : "Upload & Analyze"}
          </Button>
        </div>
      </div>
    </div>
  );
}
