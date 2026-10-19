import type { Metadata, Viewport } from "next";
import { IBM_Plex_Sans } from "next/font/google";
import { APP_TITLE } from "@/lib/title";
import "./globals.css";

// Self-hosted at build time, so pages make no request to Google.
const plex = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-sans" });

// Same mark as components/Logo.tsx, with --accent's color written in, since a
// data: URI has no page CSS.
const FAVICON_SVG =
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='#1e3a64' stroke-width='1.3' stroke-linecap='round'>" +
    "<line x1='6' y1='13' x2='6' y2='14'/><line x1='6' y1='18' x2='6' y2='20'/>" +
    "<rect x='4' y='14' width='4' height='4' fill='#1e3a64' stroke='none'/>" +
    "<line x1='12' y1='3' x2='12' y2='6'/><line x1='12' y1='13' x2='12' y2='17'/>" +
    "<rect x='10' y='6' width='4' height='7'/>" +
    "<line x1='18' y1='8' x2='18' y2='10'/><line x1='18' y1='16' x2='18' y2='19'/>" +
    "<rect x='16' y='10' width='4' height='6' fill='#1e3a64' stroke='none'/></svg>";

export const metadata: Metadata = {
    title: { default: APP_TITLE, template: `%s · ${APP_TITLE}` },
    description: "An AI agent for a demo brokerage account: positions, live research, 10-K search, and human-confirmed orders.",
    icons: { icon: { url: `data:image/svg+xml,${encodeURIComponent(FAVICON_SVG)}`, type: "image/svg+xml" } },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
    return (
        <html lang="en" className={plex.variable}>
            <body>{children}</body>
        </html>
    );
}
