import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Reframe",
  description: "Bring the feel of a reference edit to your own footage.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
