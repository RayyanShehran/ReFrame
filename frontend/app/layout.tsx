import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

// One UI typeface. The Source Serif 4 files and licence stay in ./fonts but are not loaded.
const controls = localFont({ src: "./fonts/InterTight.ttf", weight: "100 900", variable: "--font-controls", display: "swap" });

export const metadata: Metadata = {
  title: "ReFrame",
  description: "Bring the feel of a reference edit to your own footage.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body className={controls.variable}>{children}</body></html>;
}
