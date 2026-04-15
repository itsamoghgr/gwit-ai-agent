import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { ThemeProvider } from "@/lib/theme";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "IT Support Dashboard",
  description: "George Washington University IT Knowledge Base Gap Analysis & AI Chat",
  icons: { icon: "/favicon.ico" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="gw-light" className={inter.variable} suppressHydrationWarning>
      <head>
        {/* Anti-flash: restore saved theme before React hydrates */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var t=localStorage.getItem('gw-theme');if(t)document.documentElement.setAttribute('data-theme',t);}catch(e){}`,
          }}
        />
      </head>
      <body className="min-h-screen bg-base-100">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
