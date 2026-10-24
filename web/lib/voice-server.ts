// Where the voice server lives. The browser talks only to it: never to Supabase or a model provider.
export const VOICE_SERVER_URL = process.env.NEXT_PUBLIC_VOICE_SERVER_URL ?? "http://localhost:8000";
