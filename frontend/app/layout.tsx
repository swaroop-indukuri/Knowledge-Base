import type { Metadata } from "next";
import "./globals.css";
import Header from "@/components/header";
import ServerStatusBanner from "@/components/server-status-banner";

export const metadata: Metadata = {
  title: "Knowledge Management Demo",
  description: "Knowledge management demo app",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <ServerStatusBanner />
        <Header />
        {children}
      </body>
    </html>
  );
}
