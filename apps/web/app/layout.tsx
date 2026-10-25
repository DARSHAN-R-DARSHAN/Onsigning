import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth-context";
import { OrgProvider } from "@/lib/org-context";

export const metadata: Metadata = {
  title: "Onsigning — Evidence-Backed Contract Intelligence",
  description: "Transform signed agreements into traceable obligations, deadlines, and risk insights.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased bg-canvas text-navy">
        <AuthProvider>
          <OrgProvider>{children}</OrgProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
