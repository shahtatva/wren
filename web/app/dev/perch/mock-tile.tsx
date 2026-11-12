"use client";

import { MockBar, Stage } from "@/app/talk/stage";
import { birdView } from "@/lib/bird-state";

// A mock interview's stage, with a long interviewer name, for checking the bar and the mic button by eye.
// A client component, because the page can't hand the buttons their (empty) handlers from the server.
export function MockTile() {
  return (
    <>
      <MockBar name="Bartholomew-Fitzgerald" onStepOut={() => {}} />
      <Stage view={birdView({ status: "speaking", micOn: true, hearing: true, mock: true })} mock onTalk={() => {}} levelRef={null} />
    </>
  );
}
