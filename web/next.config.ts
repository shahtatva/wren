import type { NextConfig } from "next";

// Same default as lib/voice-server.ts. Read at build time, so the policy names the deployed voice server.
const VOICE_SERVER_URL = process.env.NEXT_PUBLIC_VOICE_SERVER_URL ?? "http://localhost:8000";

// Every page's headers. The policy lets the page reach only itself and the voice server.
export function securityHeaders(voiceServerUrl: string, dev: boolean): { key: string; value: string }[] {
  const voice = new URL(voiceServerUrl).origin;
  const socket = voice.replace(/^http/, "ws");
  const policy = [
    "default-src 'self'",
    // Next needs inline scripts without nonces, which Cache Components rule out. Dev also needs eval.
    // The audio worklets load from /voice/*.mjs, which 'self' covers.
    `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' blob: data:",
    "font-src 'self'",
    `connect-src 'self' ${voice} ${socket}${dev ? " ws://localhost:3000" : ""}`, // dev: hot reload
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    ...(dev ? [] : ["upgrade-insecure-requests"]), // it would break http://localhost
  ];
  return [
    { key: "Content-Security-Policy", value: policy.join("; ") },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Permissions-Policy", value: "microphone=(self), camera=(), geolocation=()" },
  ];
}

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: securityHeaders(VOICE_SERVER_URL, process.env.NODE_ENV === "development"),
      },
    ];
  },
};

export default nextConfig;
