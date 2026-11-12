import type { Metadata, Viewport } from "next";
import { Recursive } from "next/font/google";
import { brand } from "@/lib/brand";
import "./globals.css";

// One family for everything. Its CASL axis sets Wren's words casual and everything else linear.
const recursive = Recursive({
  variable: "--font-recursive",
  subsets: ["latin"],
  axes: ["CASL"],
});

export const metadata: Metadata = {
  title: brand.name,
  description: brand.description,
  applicationName: brand.name,
};

// The browser bar takes the page's own background, so it blends in.
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: brand.background },
    { media: "(prefers-color-scheme: dark)", color: brand.night },
  ],
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${recursive.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
