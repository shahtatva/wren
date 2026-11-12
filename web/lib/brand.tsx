import { BIRD_DAY, Bird } from "./bird";

export const brand = {
  name: "Wren",
  description: "A voice companion for your job search.",
  background: "#f9c59b", // apricot day
  night: "#2b1e2e", // plum night
  ink: "#3a1f2b",
  accent: "#7b3a1d",
};

// The app icon: Wren on apricot. At 0.78 of the icon's width, every part of the bird stays within 0.37 of
// the width from the center, inside the maskable icon's safe circle (0.4), so no launcher clips it.
export function WrenMark({ size }: { size: number }) {
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: brand.background,
      }}
    >
      <Bird paint={BIRD_DAY} width={Math.round(size * 0.78)} />
    </div>
  );
}
