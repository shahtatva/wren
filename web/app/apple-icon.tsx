import { ImageResponse } from "next/og";
import { WrenMark } from "@/lib/brand";

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(<WrenMark size={size.width} />, size);
}
