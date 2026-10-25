"use client";

import React, { useState, useRef, useEffect, useCallback } from "react";
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
  LogOut,
  Check,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth-context";
import { useOrg } from "@/lib/org-context";

interface AppShellProps {
  children: React.ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [orgDropdownOpen, setOrgDropdownOpen] = useState(false);
  const [isSigningOut, setIsSigningOut] = useState(false);

  const orgDropdownRef = useRef<HTMLDivElement>(null);

  const { user, isLoading: isAuthLoading, signOut } = useAuth();
  const {
    organizations,
    selectedOrg,
    isLoading: isOrgLoading,
    selectOrg,
  } = useOrg();

  // Close workspace dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        orgDropdownRef.current &&
        !orgDropdownRef.current.contains(event.target as Node)
      ) {
        setOrgDropdownOpen(false);
      }
    }
    if (orgDropdownOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [orgDropdownOpen]);

  // Derive user display information safely
  const displayName =
    (user?.user_metadata?.full_name as string) ||
    (user?.user_metadata?.name as string) ||
    (user?.email ? user.email.split("@")[0] : null) ||
    "User";

  const initials = displayName
    .split(" ")
    .map((part) => part[0])
    .filter(Boolean)
    .join("")
    .toUpperCase()
    .slice(0, 2) || "U";

  const handleSignOut = useCallback(async () => {
    if (isSigningOut) return;
    try {
      setIsSigningOut(true);
      await signOut();
    } catch {
      // Failed sign out handled safely without crashing
    } finally {
      setIsSigningOut(false);
    }
  }, [isSigningOut, signOut]);

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
            <div className="p-3 border-b border-border/60" ref={orgDropdownRef}>
              {isOrgLoading ? (
                <div className="w-full flex items-center space-x-2 p-2 rounded-md bg-canvas/60 border border-border/50 animate-pulse">
                  <div className="w-6 h-6 rounded bg-slate-200 flex-shrink-0" />
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="h-3 bg-slate-200 rounded w-24" />
                    <div className="h-2 bg-slate-100 rounded w-16" />
                  </div>
                </div>
              ) : organizations.length === 0 ? (
                <div className="p-2.5 rounded-md bg-amber-50 border border-amber-200 text-amber-900 text-[11px] leading-tight">
                  <p className="font-semibold text-amber-950">No Workspace</p>
                  <p className="text-[10px] text-amber-800 mt-0.5">
                    No organization workspace found. Please contact your administrator.
                  </p>
                </div>
              ) : organizations.length === 1 ? (
                /* Single Organization: Static, no switching suggested */
                <div className="w-full flex items-center justify-between p-2 rounded-md bg-canvas/60 border border-border/50 text-left">
                  <div className="flex items-center space-x-2 min-w-0">
                    <div className="w-6 h-6 rounded bg-blue-100 text-primary flex items-center justify-center flex-shrink-0">
                      <Building2 className="w-3.5 h-3.5" />
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-navy truncate" title={selectedOrg?.name || organizations[0].name}>
                        {selectedOrg?.name || organizations[0].name}
                      </p>
                      <p className="text-[10px] text-text-secondary truncate">
                        {selectedOrg?.role ? selectedOrg.role.replace(/_/g, " ") : "Workspace"}
                      </p>
                    </div>
                  </div>
                </div>
              ) : (
                /* Multiple Organizations: Interactive Workspace Switcher Dropdown */
                <div className="relative">
                  <button
                    onClick={() => setOrgDropdownOpen((prev) => !prev)}
                    className="w-full flex items-center justify-between p-2 rounded-md hover:bg-blue-50 transition border border-transparent hover:border-blue-100 text-left focus:outline-none focus:ring-1 focus:ring-primary"
                    aria-expanded={orgDropdownOpen}
                    aria-haspopup="listbox"
                    aria-label="Switch organization workspace"
                  >
                    <div className="flex items-center space-x-2 min-w-0">
                      <div className="w-6 h-6 rounded bg-blue-100 text-primary flex items-center justify-center flex-shrink-0">
                        <Building2 className="w-3.5 h-3.5" />
                      </div>
                      <div className="min-w-0">
                        <p className="text-xs font-semibold text-navy truncate" title={selectedOrg?.name}>
                          {selectedOrg?.name || "Select Workspace"}
                        </p>
                        <p className="text-[10px] text-text-secondary truncate">
                          {selectedOrg?.role ? selectedOrg.role.replace(/_/g, " ") : "Switch Workspace"}
                        </p>
                      </div>
                    </div>
                    <ChevronDown
                      className={cn(
                        "w-3.5 h-3.5 text-text-secondary flex-shrink-0 transition-transform duration-150",
                        orgDropdownOpen && "rotate-180"
                      )}
                    />
                  </button>

                  {/* Dropdown Menu */}
                  {orgDropdownOpen && (
                    <div className="absolute left-0 right-0 top-full mt-1 bg-white border border-border rounded-lg shadow-lg py-1 z-50 animate-in fade-in zoom-in-95 duration-100">
                      <div className="px-3 py-1.5 text-[10px] uppercase font-bold tracking-wider text-text-secondary border-b border-border/60">
                        Switch Workspace
                      </div>
                      <div className="max-h-56 overflow-y-auto divide-y divide-border/40" role="listbox">
                        {organizations.map((org) => {
                          const isSelected = org.id === selectedOrg?.id;
                          return (
                            <button
                              key={org.id}
                              onClick={() => {
                                selectOrg(org.id);
                                setOrgDropdownOpen(false);
                              }}
                              className={cn(
                                "w-full flex items-center justify-between px-3 py-2 text-left text-xs transition",
                                isSelected
                                  ? "bg-blue-50 text-primary font-semibold"
                                  : "text-navy hover:bg-canvas"
                              )}
                              role="option"
                              aria-selected={isSelected}
                            >
                              <div className="min-w-0 pr-2">
                                <p className="truncate font-medium">{org.name}</p>
                                <p className="text-[10px] text-text-secondary truncate font-mono">
                                  {org.role.replace(/_/g, " ")}
                                </p>
                              </div>
                              {isSelected && (
                                <Check className="w-4 h-4 text-primary flex-shrink-0" />
                              )}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )}
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
            {isAuthLoading ? (
              <div className="flex items-center space-x-2.5 p-2 animate-pulse">
                <div className="w-7 h-7 rounded-full bg-slate-200 flex-shrink-0" />
                <div className="flex-1 space-y-1 min-w-0">
                  <div className="h-3 bg-slate-200 rounded w-20" />
                  <div className="h-2 bg-slate-100 rounded w-14" />
                </div>
              </div>
            ) : user ? (
              <div className="flex items-center justify-between p-2 rounded hover:bg-canvas transition group">
                <div className="flex items-center space-x-2.5 min-w-0 flex-1">
                  <div className="w-7 h-7 rounded-full bg-navy text-white text-xs font-medium flex items-center justify-center flex-shrink-0">
                    {initials}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-semibold text-navy truncate" title={displayName}>
                      {displayName}
                    </p>
                    <p className="text-[10px] text-text-secondary truncate" title={user.email ?? ""}>
                      {user.email}
                    </p>
                  </div>
                </div>
                <button
                  onClick={handleSignOut}
                  disabled={isSigningOut}
                  className="p-1.5 rounded text-text-secondary hover:text-navy hover:bg-slate-200/60 focus:outline-none focus:ring-1 focus:ring-primary ml-1 flex-shrink-0 transition"
                  title="Sign out"
                  aria-label="Sign out"
                >
                  <LogOut className="w-3.5 h-3.5" />
                </button>
              </div>
            ) : (
              <div className="p-2 text-xs text-text-secondary flex items-center justify-between">
                <span>Not signed in</span>
                <Link
                  href="/login"
                  className="text-xs font-semibold text-primary hover:underline focus:outline-none"
                >
                  Sign in
                </Link>
              </div>
            )}
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
