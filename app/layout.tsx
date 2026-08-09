import type { Metadata } from "next";
import "./globals.css";

const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL ??
  "https://cmschase.github.io/LA-surface-heat-atlas/";
const socialImageUrl = new URL("og.png", siteUrl).toString();
const description =
  "Explore observed and predicted neighborhood-scale land-surface temperature across Los Angeles in the held-out 2025 evaluation.";

export const metadata: Metadata = {
  title: "LA Surface Heat Atlas",
  description,
  metadataBase: new URL(siteUrl),
  openGraph: {
    title: "LA Surface Heat Atlas",
    description:
      "Observed, predicted, and residual surface heat across Los Angeles census tracts.",
    images: [{ url: socialImageUrl, width: 1734, height: 907 }],
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "LA Surface Heat Atlas",
    description:
      "A held-out 2025 historical hindcast of neighborhood-scale urban surface heat.",
    images: [socialImageUrl],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
