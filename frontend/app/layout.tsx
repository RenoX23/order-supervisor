import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Order Supervisor",
  description: "Long-running AI supervisor for e-commerce orders",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full bg-slate-950 text-slate-100">
        <header className="border-b border-slate-800">
          <div className="mx-auto flex max-w-5xl items-center gap-6 px-4 py-3">
            <Link href="/" className="font-semibold">
              Order Supervisor
            </Link>
            <nav className="flex gap-4 text-sm text-slate-400">
              <Link href="/" className="hover:text-slate-100">
                Runs
              </Link>
              <Link href="/supervisors/new" className="hover:text-slate-100">
                New supervisor
              </Link>
              <Link href="/runs/new" className="hover:text-slate-100">
                Start run
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
