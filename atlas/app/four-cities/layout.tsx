import type { Metadata } from "next";
import "./four-cities.css";

export const metadata: Metadata = {
  title: "Four City Surface Heat Atlas",
  description:
    "Explore observed and predicted neighborhood-scale land-surface temperature across Seattle, Denver, Atlanta, and Miami.",
};

export default function FourCityLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
