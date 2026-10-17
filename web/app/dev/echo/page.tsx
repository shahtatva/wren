import { notFound } from "next/navigation";
import { EchoSpike } from "./echo-spike";

// M0 echo spike, throwaway and development only. See voice/app/tools/echo_spike.py.
export default function EchoSpikePage() {
  if (process.env.NODE_ENV !== "development") notFound();
  return <EchoSpike />;
}
