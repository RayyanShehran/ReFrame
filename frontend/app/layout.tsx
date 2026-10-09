import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

const display = localFont({ src: [
  { path: "./fonts/SourceSerif4.ttf", style: "normal", weight: "200 900" },
  { path: "./fonts/SourceSerif4-Italic.ttf", style: "italic", weight: "200 900" },
], variable: "--font-display", display: "swap" });
const controls = localFont({ src: "./fonts/InterTight.ttf", weight: "100 900", variable: "--font-controls", display: "swap" });

export const metadata: Metadata = {
  title: "ReFrame",
  description: "Bring the feel of a reference edit to your own footage.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body className={`${display.variable} ${controls.variable}`}>{children}</body></html>;
}
