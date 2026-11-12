"""Browser check on the dev board, /dev/perch, which needs only the web dev server (`npm run dev` in web/).

When a mock interview starts or ends, the talk button changes from the bird to a plain mic button or back.
If the keyboard was on the talk button, it stays there, with its ring showing, so the next Space still
reaches it.

From the repo root, with voice/.venv active and `pip install -r web/e2e/requirements.txt` done:

    python web/e2e/perch_board_e2e.py [--headed]
"""

import argparse
import sys

from playwright.sync_api import sync_playwright

WEB = "http://localhost:3000"
FOCUSED = """() => {
  const el = document.activeElement, style = getComputedStyle(el);
  return { label: el.getAttribute('aria-label'), bird: el.classList.contains('perch-bird'),
           ring: style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0 };
}"""


def main() -> int:
    parser = argparse.ArgumentParser(description="The talk button keeps keyboard focus as a mock starts and ends.")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    results: list[bool] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=not args.headed)
        page = browser.new_page()
        page.goto(f"{WEB}/dev/perch", timeout=180_000)
        tile = page.locator('section[aria-label="talk button handoff"]')
        tile.get_by_role("button", name="Start a mock").focus()
        page.keyboard.press("Tab")  # to Wren, from the keyboard
        start = page.evaluate(FOCUSED)
        results.append(start == {"label": "Mute", "bird": True, "ring": True})
        print(f"{'PASS' if results[-1] else 'FAIL'}  Tab reaches Wren, with its ring  ({start})")
        for switch, bird in (("Start a mock", False), ("End the mock", True)):
            # A click from script moves no focus, as when the mock starts or ends by voice.
            tile.get_by_role("button", name=switch).evaluate("button => button.click()")
            page.wait_for_timeout(200)
            focused = page.evaluate(FOCUSED)
            results.append(focused == {"label": "Mute", "bird": bird, "ring": True})
            print(f"{'PASS' if results[-1] else 'FAIL'}  after {switch}: focus on the {'bird' if bird else 'mic'}, with its ring  ({focused})")
        browser.close()
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
