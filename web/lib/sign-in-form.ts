// The sign-in form's state, apart from the screen, so `node --test` can check it.
// A refusal keeps the email and clears the password, so retyping starts in the right place.

export type SignInForm = { email: string; password: string; busy: boolean; error: string | null };

export type SignInFormEvent =
  | { type: "email" | "password"; value: string }
  | { type: "submit" }
  | { type: "refused"; message: string }
  | { type: "done" };

export const START_FORM: SignInForm = { email: "", password: "", busy: false, error: null };

export function signInForm(state: SignInForm, event: SignInFormEvent): SignInForm {
  switch (event.type) {
    case "email":
      return { ...state, email: event.value };
    case "password":
      return { ...state, password: event.value };
    case "submit":
      return { ...state, busy: true, error: null };
    case "refused":
      return { ...state, busy: false, password: "", error: event.message };
    case "done":
      return { ...state, busy: false, password: "" };
  }
}
