import Link from "next/link";

export default function Home() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
      <h1 className="text-4xl font-semibold tracking-tight">Wren</h1>
      <p className="text-lg opacity-70">A voice companion for your job search.</p>
      <Link
        href="/talk"
        className="mt-6 rounded-full bg-wren px-6 py-3 font-medium text-background focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-wren"
      >
        Talk to Wren
      </Link>
    </main>
  );
}
