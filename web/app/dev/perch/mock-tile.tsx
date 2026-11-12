"use client";

import { useState } from "react";
import { MockBar, Stage } from "@/app/talk/stage";
import { birdView } from "@/lib/bird-state";
import { OUTLINE } from "@/lib/ui";

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

// The talk button as a mock starts and ends, as it does by voice: Tab to Wren, then press the switch with
// a click from script (web/e2e/perch_board_e2e.py), and focus should stay on the talk button.
export function SwitchTile() {
  const [mock, setMock] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setMock((on) => !on)} className={OUTLINE}>
        {mock ? "End the mock" : "Start a mock"}
      </button>
      <Stage view={birdView({ status: "listening", micOn: true, hearing: true, mock })} mock={mock} onTalk={() => {}} levelRef={null} />
    </>
  );
}
