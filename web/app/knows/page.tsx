import type { Metadata } from "next";
import { WhatWrenKnows } from "./what-wren-knows";

export const metadata: Metadata = {
  title: "What Wren knows",
};

export default function KnowsPage() {
  return <WhatWrenKnows />;
}
