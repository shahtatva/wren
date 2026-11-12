export const brand = {
  name: "Wren",
  description: "A voice companion for your job search.",
  background: "#f9c59b", // apricot day
  night: "#2b1e2e", // plum night
  ink: "#3a1f2b",
  accent: "#7b3a1d",
};

// Placeholder mark until there is a real illustrated avatar.
export function WrenMark({ size }: { size: number }) {
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: brand.accent,
        color: brand.background,
        fontSize: Math.round(size * 0.6),
        fontWeight: 700,
      }}
    >
      W
    </div>
  );
}
