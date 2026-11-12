import Link from "next/link";
import { Bird, Perch } from "@/lib/bird";
import { brand } from "@/lib/brand";
import { PRIMARY } from "@/lib/ui";

export default function Home() {
  return (
    <main className="mx-auto flex w-full max-w-[40rem] flex-1 flex-col px-5 text-center">
      <div className="flex flex-1 flex-col justify-center pb-6">
        <Perch pose="perched" className="-mx-5 h-[200px]">
          <div className="perch-bird">
            <Bird />
          </div>
        </Perch>
        <h1 className="casual mt-4 text-[56px] leading-none font-extrabold tracking-tight text-wren">Wren</h1>
        <p className="mt-3 text-[17px] text-ink-soft">{brand.description}</p>
        <Link href="/talk" className={`${PRIMARY} mt-8 self-center`}>
          Talk to Wren
        </Link>
      </div>
      {/* On every screen (wren-voice boundary 2). */}
      <p className="pb-5 text-[13px] text-ink-soft">Wren is an AI, not a person.</p>
    </main>
  );
}
