import type { Metadata } from "next";
import { Talk } from "./talk";

export const metadata: Metadata = {
  title: "Talk with Wren",
};

export default function TalkPage() {
  return <Talk />;
}
