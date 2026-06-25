import Link from "next/link";

export default function GoldSuccess() {
  return (
    <div
      className="min-h-screen flex items-center justify-center"
      style={{ backgroundColor: "#0d0b07" }}
    >
      <div className="text-center max-w-md px-8">
        <div className="text-5xl mb-6" style={{ color: "#c9a84c" }}>✦</div>
        <h1 className="text-3xl font-light mb-4" style={{ color: "#e8e0d0" }}>
          Welcome to Gold
        </h1>
        <p className="text-sm opacity-50 mb-8 leading-relaxed">
          Your subscription is active. Unlimited AI analysis and all Gold features are now unlocked.
        </p>
        <Link
          href="/journal"
          className="inline-block px-8 py-3 text-sm tracking-widest uppercase"
          style={{ backgroundColor: "#c9a84c", color: "#0d0b07", borderRadius: "2px" }}
        >
          Open Your Journal →
        </Link>
      </div>
    </div>
  );
}
