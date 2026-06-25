"use client";

import { useState, useEffect } from "react";
import Link from "next/link";

interface GoldAnalysis {
  goldLine: string;
  wisdom: string;
  strength: string;
  question?: string;
}

interface Entry {
  id: string;
  date: string;
  text: string;
  gold: GoldAnalysis | null;
  createdAt: number;
}

function formatDate(ts: number) {
  return new Date(ts).toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

function CrackIcon({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20">
      <path
        d="M10,2 L9,8 L12,11 L8,14 L10,18"
        stroke="#c9a84c"
        strokeWidth="1.5"
        fill="none"
        strokeLinecap="round"
      />
      <path
        d="M8,14 L4,15"
        stroke="#c9a84c"
        strokeWidth="1"
        fill="none"
        opacity="0.6"
      />
    </svg>
  );
}

const FREE_LIMIT = 5;

function getUsageKey() {
  const now = new Date();
  return `kintsugi-usage-${now.getFullYear()}-${now.getMonth() + 1}`;
}

function getMonthlyUsage(): number {
  return parseInt(localStorage.getItem(getUsageKey()) || "0", 10);
}

function incrementUsage() {
  const key = getUsageKey();
  const current = getMonthlyUsage();
  localStorage.setItem(key, String(current + 1));
}

export default function JournalPage() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [currentText, setCurrentText] = useState("");
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Entry | null>(null);
  const [view, setView] = useState<"write" | "gallery">("write");
  const [error, setError] = useState("");
  const [monthlyUsage, setMonthlyUsage] = useState(0);
  const [isGold, setIsGold] = useState(false);

  useEffect(() => {
    const saved = localStorage.getItem("kintsugi-entries");
    if (saved) setEntries(JSON.parse(saved));
    setMonthlyUsage(getMonthlyUsage());
    setIsGold(localStorage.getItem("kintsugi-gold") === "true");
  }, []);

  function saveEntries(updated: Entry[]) {
    setEntries(updated);
    localStorage.setItem("kintsugi-entries", JSON.stringify(updated));
  }

  async function handleSubmit() {
    if (currentText.trim().length < 20) {
      setError("Please write at least a few sentences.");
      return;
    }
    setError("");
    setLoading(true);

    const newEntry: Entry = {
      id: Date.now().toString(),
      date: formatDate(Date.now()),
      text: currentText.trim(),
      gold: null,
      createdAt: Date.now(),
    };

    const usage = getMonthlyUsage();
    if (!isGold && usage >= FREE_LIMIT) {
      setError("今月の無料AI分析（5回）を使い切りました。Goldプランで無制限に使えます。");
      setLoading(false);
      const updated = [newEntry, ...entries];
      saveEntries(updated);
      setCurrentText("");
      setSelected(newEntry);
      setView("gallery");
      return;
    }

    try {
      const res = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ entry: currentText }),
      });

      if (!res.ok) throw new Error("API error");
      const gold: GoldAnalysis = await res.json();
      newEntry.gold = gold;
      incrementUsage();
      setMonthlyUsage(getMonthlyUsage());
    } catch {
      setError("AI analysis unavailable — entry saved without gold.");
    }

    const updated = [newEntry, ...entries];
    saveEntries(updated);
    setCurrentText("");
    setLoading(false);
    setSelected(newEntry);
    setView("gallery");
  }

  function deleteEntry(id: string) {
    const updated = entries.filter((e) => e.id !== id);
    saveEntries(updated);
    if (selected?.id === id) setSelected(null);
  }

  return (
    <div
      className="min-h-screen flex flex-col"
      style={{ backgroundColor: "#0d0b07" }}
    >
      {/* Header */}
      <header
        className="flex justify-between items-center px-6 py-4 border-b"
        style={{ borderColor: "#1a1710" }}
      >
        <Link
          href="/"
          className="text-sm tracking-widest opacity-60 hover:opacity-100 transition-opacity"
          style={{ color: "#c9a84c" }}
        >
          ← 金継ぎ KINTSUGI
        </Link>
        <div className="flex gap-4">
          <button
            onClick={() => setView("write")}
            className="text-sm px-4 py-1.5 rounded transition-all"
            style={{
              backgroundColor: view === "write" ? "#c9a84c" : "transparent",
              color: view === "write" ? "#0d0b07" : "#c9a84c",
              border: "1px solid #c9a84c",
            }}
          >
            書く
          </button>
          <button
            onClick={() => setView("gallery")}
            className="text-sm px-4 py-1.5 rounded transition-all"
            style={{
              backgroundColor: view === "gallery" ? "#c9a84c" : "transparent",
              color: view === "gallery" ? "#0d0b07" : "#c9a84c",
              border: "1px solid #c9a84c",
            }}
          >
            ギャラリー ({entries.length})
          </button>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Write View */}
        {view === "write" && (
          <div className="flex-1 flex flex-col max-w-2xl mx-auto w-full px-8 py-12">
            <div className="mb-8">
              <p
                className="text-xs tracking-[0.3em] uppercase mb-3"
                style={{ color: "#c9a84c" }}
              >
                今日の亀裂
              </p>
              <h2
                className="text-2xl font-light"
                style={{ color: "#e8e0d0" }}
              >
                今日、何が壊れましたか？
              </h2>
              <p className="text-sm mt-2 opacity-40">
                正直に書いてください。本音であるほど、より多くの金が見つかります。
              </p>
              {!isGold && (
                <div className="mt-2 text-xs" style={{ color: "#c9a84c", opacity: 0.6 }}>
                  {monthlyUsage >= FREE_LIMIT ? (
                    <span>今月の無料AI分析を使い切りました（{FREE_LIMIT}/{FREE_LIMIT}）— <a href="/" style={{ textDecoration: "underline" }}>Goldにアップグレード</a></span>
                  ) : (
                    <span>今月の無料AI分析: {monthlyUsage}/{FREE_LIMIT}回使用</span>
                  )}
                </div>
              )}
              {isGold && (
                <div className="mt-2 text-xs" style={{ color: "#c9a84c", opacity: 0.6 }}>
                  ✦ Gold会員 — AI分析無制限
                </div>
              )}
            </div>

            <textarea
              value={currentText}
              onChange={(e) => setCurrentText(e.target.value)}
              placeholder="失敗したのは... ミスをしたのは... 断られたのは... 壊れた気がするのは..."
              className="flex-1 min-h-64 w-full p-4 rounded text-sm leading-relaxed resize-none focus:outline-none"
              style={{
                backgroundColor: "#111009",
                color: "#e8e0d0",
                border: "1px solid #252218",
                fontFamily: "Georgia, serif",
              }}
              disabled={loading}
            />

            {error && (
              <p className="mt-2 text-xs" style={{ color: "#c9a84c" }}>
                {error}
              </p>
            )}

            <div className="mt-4 flex justify-between items-center">
              <span className="text-xs opacity-30">
                {currentText.length} characters
              </span>
              <button
                onClick={handleSubmit}
                disabled={loading || currentText.trim().length < 20}
                className="px-6 py-3 text-sm tracking-widest uppercase transition-all disabled:opacity-30"
                style={{
                  backgroundColor: "#c9a84c",
                  color: "#0d0b07",
                  borderRadius: "2px",
                }}
              >
                {loading ? "金を探しています..." : "金を見つける →"}
              </button>
            </div>

            {loading && (
              <div className="mt-8 text-center">
                <div className="inline-block">
                  <svg
                    width="60"
                    height="60"
                    viewBox="0 0 60 60"
                    className="gold-pulse mx-auto"
                  >
                    <path
                      d="M30,5 L28,22 L35,30 L25,38 L30,55"
                      stroke="#c9a84c"
                      strokeWidth="2"
                      fill="none"
                      strokeLinecap="round"
                    />
                  </svg>
                  <p className="text-xs mt-3 opacity-40">
                    亀裂に金を塗っています...
                  </p>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Gallery View */}
        {view === "gallery" && (
          <div className="flex-1 flex overflow-hidden">
            {/* Entry list */}
            <div
              className="w-72 border-r overflow-y-auto scrollbar-gold flex-shrink-0"
              style={{ borderColor: "#1a1710" }}
            >
              {entries.length === 0 ? (
                <div className="p-6 text-center opacity-40 text-sm mt-8">
                  <CrackIcon size={32} />
                  <p className="mt-3">まだエントリーがありません。</p>
                  <button
                    onClick={() => setView("write")}
                    className="mt-3 text-xs underline"
                    style={{ color: "#c9a84c" }}
                  >
                    最初のエントリーを書く
                  </button>
                </div>
              ) : (
                entries.map((entry) => (
                  <button
                    key={entry.id}
                    onClick={() => setSelected(entry)}
                    className="w-full text-left p-4 border-b transition-all hover:opacity-100"
                    style={{
                      borderColor: "#1a1710",
                      backgroundColor:
                        selected?.id === entry.id ? "#1a1710" : "transparent",
                      opacity: selected?.id === entry.id ? 1 : 0.7,
                    }}
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <CrackIcon size={14} />
                      <span className="text-xs" style={{ color: "#c9a84c" }}>
                        {entry.date}
                      </span>
                    </div>
                    <p
                      className="text-sm line-clamp-2 leading-relaxed"
                      style={{ color: "#e8e0d0" }}
                    >
                      {entry.text}
                    </p>
                    {entry.gold && (
                      <p
                        className="text-xs mt-2 italic opacity-60 line-clamp-1"
                        style={{ color: "#c9a84c" }}
                      >
                        ✦ {entry.gold.strength}
                      </p>
                    )}
                  </button>
                ))
              )}
            </div>

            {/* Entry detail */}
            <div className="flex-1 overflow-y-auto scrollbar-gold p-8">
              {!selected ? (
                <div className="flex items-center justify-center h-full opacity-20 text-sm">
                  エントリーを選んでください
                </div>
              ) : (
                <div className="max-w-xl mx-auto fade-in-up">
                  <div
                    className="text-xs tracking-widest uppercase mb-2"
                    style={{ color: "#c9a84c" }}
                  >
                    {selected.date}
                  </div>

                  {/* Original entry */}
                  <div
                    className="p-6 rounded mb-6"
                    style={{
                      backgroundColor: "#111009",
                      border: "1px solid #1a1710",
                    }}
                  >
                    <h3
                      className="text-xs uppercase tracking-widest mb-3 opacity-40"
                      style={{ color: "#e8e0d0" }}
                    >
                      亀裂
                    </h3>
                    <p
                      className="text-sm leading-relaxed"
                      style={{ color: "#c8c0b0" }}
                    >
                      {selected.text}
                    </p>
                  </div>

                  {/* Gold analysis */}
                  {selected.gold ? (
                    <div
                      className="p-6 rounded"
                      style={{
                        backgroundColor: "#0f0e09",
                        border: "1px solid #c9a84c",
                      }}
                    >
                      <h3
                        className="text-xs uppercase tracking-widest mb-4"
                        style={{ color: "#c9a84c" }}
                      >
                        ✦ 金
                      </h3>
                      <p
                        className="text-lg font-light italic mb-5 leading-relaxed"
                        style={{ color: "#f0d080" }}
                      >
                        &ldquo;{selected.gold.goldLine}&rdquo;
                      </p>
                      <p
                        className="text-sm leading-relaxed mb-5 opacity-70"
                        style={{ color: "#e8e0d0" }}
                      >
                        {selected.gold.wisdom}
                      </p>
                      <div
                        className="inline-block px-3 py-1 rounded text-xs tracking-widest uppercase mb-4"
                        style={{
                          backgroundColor: "#1a1710",
                          color: "#c9a84c",
                          border: "1px solid #252218",
                        }}
                      >
                        鍛えられた強さ: {selected.gold.strength}
                      </div>
                      {selected.gold.question && (
                        <p
                          className="text-sm italic opacity-50 mt-2"
                          style={{ color: "#e8e0d0" }}
                        >
                          {selected.gold.question}
                        </p>
                      )}
                    </div>
                  ) : (
                    <div
                      className="p-6 rounded text-center"
                      style={{
                        border: "1px dashed #252218",
                        backgroundColor: "#0f0e09",
                      }}
                    >
                      <p className="text-sm opacity-40">
                        このエントリーにはAI分析がありません。
                      </p>
                    </div>
                  )}

                  <button
                    onClick={() => deleteEntry(selected.id)}
                    className="mt-6 text-xs opacity-20 hover:opacity-50 transition-opacity"
                    style={{ color: "#e8e0d0" }}
                  >
                    このエントリーを削除
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
