"use client";

import React, { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  FileText,
  LayoutDashboard,
  Calendar,
  MessageSquare,
  Activity,
  Settings,
  ChevronDown,
  Building2,
  Menu,
  X,
  Shield,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface AppShellProps {
  children: React.ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const navigation = [
    { name: "Overview", href: "/overview", icon: LayoutDashboard },
    { name: "Contracts", href: "/contracts", icon: FileText, current: pathname.startsWith("/contracts") },
    { name: "Obligations & Deadlines", href: "/obligations", icon: Calendar, badge: "M4" },
    { name: "Ask Contracts", href: "/ask", icon: MessageSquare, badge: "M3" },
    { name: "Activity Log", href: "/activity", icon: Activity },
    { name: "Settings", href: "/settings", icon: Settings },
  ];

  return (
    <div className="min-h-screen flex flex-col bg-canvas text-navy">
      {/* Mobile Header Bar */}
      <div className="lg:hidden flex items-center justify-between px-4 py-3 bg-white border-b border-border shadow-sm">
        <div className="flex items-center space-x-2">
          <div className="w-7 h-7 rounded bg-primary flex items-center justify-center text-white font-bold text-xs">
            O
          </div>
          <span className="font-bold text-navy text-sm tracking-tight">Onsigning</span>
        </div>
        <button
          onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          className="p-1 rounded text-text-secondary hover:text-navy hover:bg-canvas"
          aria-label="Toggle navigation menu"
        >
          {mobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>
      </div>

      <div className="flex-1 flex overflow-hidden">
        {/* Desktop Left Sidebar (Persistent, 80% white / 20% blue accents) */}
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-40 w-64 bg-white border-r border-border flex flex-col justify-between transition-transform duration-200 lg:static lg:translate-x-0 shadow-sm",
            mobileMenuOpen ? "translate-x-0" : "-translate-x-full"
          )}
        >
          <div>
            {/* Brand Logo & Title */}
            <div className="h-14 flex items-center px-5 border-b border-border">
              <div className="w-7 h-7 rounded bg-primary flex items-center justify-center text-white font-bold text-xs mr-2.5 shadow-sm">
                O
              </div>
              <div>
                <span className="font-bold text-navy text-base tracking-tight">Onsigning</span>
                <span className="text-[10px] text-primary font-semibold block leading-none">
                  Contract Intelligence
                </span>
              </div>
            </div>

            {/* Tenant Workspace Switcher */}
            <div className="p-3 border-b border-border/60">
              <button className="w-full flex items-center justify-between p-2 rounded-md hover:bg-blue-50 transition border border-transparent hover:border-blue-100 text-left">
                <div className="flex items-center space-x-2 min-w-0">
                  <div className="w-6 h-6 rounded bg-blue-100 text-primary flex items-center justify-center flex-shrink-0">
                    <Building2 className="w-3.5 h-3.5" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-xs font-semibold text-navy truncate">Acme Legal Corp</p>
                    <p className="text-[10px] text-text-secondary">Enterprise Workspace</p>
                  </div>
                </div>
                <ChevronDown className="w-3.5 h-3.5 text-text-secondary flex-shrink-0" />
              </button>
            </div>

            {/* Navigation Links */}
            <nav className="p-3 space-y-1">
              {navigation.map((item) => {
                const isActive = item.current || pathname === item.href;
                const Icon = item.icon;
                return (
                  <Link
                    key={item.name}
                    href={item.href}
                    onClick={() => setMobileMenuOpen(false)}
                    className={cn(
                      "flex items-center justify-between px-3 py-2 rounded-md text-xs font-medium transition",
                      isActive
                        ? "bg-blue-100 text-primary font-semibold border border-blue-200"
                        : "text-slate-700 hover:text-navy hover:bg-canvas"
                    )}
                  >
                    <div className="flex items-center space-x-2.5">
                      <Icon className={cn("w-4 h-4", isActive ? "text-primary" : "text-slate-500")} />
                      <span>{item.name}</span>
                    </div>
                    {item.badge && (
                      <span className="text-[9px] uppercase font-mono px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 border border-slate-200">
                        {item.badge}
                      </span>
                    )}
                  </Link>
                );
              })}
            </nav>
          </div>

          {/* User Account / Session Footer */}
          <div className="p-3 border-t border-border">
            <div className="flex items-center space-x-2.5 p-2 rounded hover:bg-canvas transition">
              <div className="w-7 h-7 rounded-full bg-navy text-white text-xs font-medium flex items-center justify-center">
                SL
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold text-navy truncate">Sarah Legal</p>
                <p className="text-[10px] text-text-secondary truncate">Legal Reviewer</p>
              </div>
            </div>
          </div>
        </aside>

        {/* Backdrop for mobile drawer */}
        {mobileMenuOpen && (
          <div
            onClick={() => setMobileMenuOpen(false)}
            className="fixed inset-0 z-30 bg-black/30 lg:hidden"
          />
        )}

        {/* Main Content Area */}
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
