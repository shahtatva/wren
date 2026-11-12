import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

// Every text color clears WCAG AA (4.5:1) on every surface it's used on, in both themes (spec: Colors and type).
const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
const [day, ...rest] = css.split("@media (prefers-color-scheme: dark)");
const night = rest.join("");

function tokens(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/--([a-z-]+):\s*(#[0-9a-f]{6})\b/gi)].map((m) => [m[1], m[2].toLowerCase()]));
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const PAIRS: [text: string, surface: string][] = [
  ["foreground", "background"], ["foreground", "toffee"], ["foreground", "field"],
  ["ink-soft", "background"], ["ink-soft", "toffee"], ["ink-soft", "field"],
  ["wren", "background"], ["wren", "toffee"],
  ["brick", "background"], ["brick", "toffee"],
  ["background", "wren"], ["background", "brick"], // the text on Send and on Delete
];

for (const [theme, block] of [["light", day], ["dark", night]] as const) {
  test(`${theme}: every text color clears 4.5:1 on its surfaces`, () => {
    const t = tokens(block);
    for (const [text, surface] of PAIRS) {
      assert.ok(t[text] && t[surface], `${theme} is missing --${text} or --${surface}`);
      const ratio = contrast(t[text], t[surface]);
      assert.ok(ratio >= 4.5, `${theme}: --${text} on --${surface} is ${ratio.toFixed(2)}:1`);
    }
  });
}
