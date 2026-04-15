"use client";

import { RunProvider } from "@/lib/RunContext";
import Sidebar from "@/components/Sidebar";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <RunProvider>
      <div className="flex h-screen bg-base-100 overflow-hidden">
        <Sidebar />
        <main className="flex-1 min-w-0 overflow-y-auto px-8 py-8">
          {children}
        </main>
      </div>
    </RunProvider>
  );
}
