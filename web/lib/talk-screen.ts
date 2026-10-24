// What the talk page shows after each sign-in event, apart from the screen, so `node --test` can check it.

export type Screen = "sign-in" | "age" | "talk";

export type ScreenEvent =
  | { type: "signed-in"; needsAge: boolean }
  | { type: "refused"; code: "unauthorized" | "age_unconfirmed" } // from the server, at the hello or POST /turn
  | { type: "age-confirmed" }
  | { type: "age-signed-out" } // the age step found the sign-in gone
  | { type: "sign-in-asked" }; // the header's "Sign in", from the talk screen

/** stopClient: let go of the mic and the session. forgetSignIn: the server refused the sign-in,
 * so drop it here, with no request. */
export type ScreenChange = { screen: Screen; stopClient: boolean; forgetSignIn: boolean };

export function screenAfter(event: ScreenEvent): ScreenChange {
  switch (event.type) {
    case "signed-in":
      return { screen: event.needsAge ? "age" : "talk", stopClient: false, forgetSignIn: false };
    case "refused":
      return event.code === "age_unconfirmed"
        ? { screen: "age", stopClient: true, forgetSignIn: false }
        : { screen: "sign-in", stopClient: true, forgetSignIn: true };
    case "age-confirmed":
      // The next tap starts the session. Wren's audio is built inside a tap, or iOS can keep it silent.
      return { screen: "talk", stopClient: false, forgetSignIn: false };
    case "age-signed-out":
      return { screen: "sign-in", stopClient: true, forgetSignIn: true };
    case "sign-in-asked":
      return { screen: "sign-in", stopClient: true, forgetSignIn: false };
  }
}
