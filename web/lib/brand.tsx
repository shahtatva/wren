export const brand = {
  name: "Wren",
  description: "A voice companion for your job search.",
  background: "#faf6f0",
  ink: "#3b2a1e",
  accent: "#8a5a35",
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
