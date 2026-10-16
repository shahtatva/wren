import { ImageResponse } from "next/og";
import { WrenMark } from "@/lib/brand";

// Served at /icon/192 and /icon/512; app/manifest.ts points at both.
const sizes = [192, 512];

export function generateImageMetadata() {
  return sizes.map((size) => ({
    id: String(size),
    contentType: "image/png",
    size: { width: size, height: size },
  }));
}

export default async function Icon({ id }: { id: Promise<string | number> }) {
  const size = Number(await id);
  return new ImageResponse(<WrenMark size={size} />, {
    width: size,
    height: size,
  });
}
