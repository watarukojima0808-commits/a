"use client";

import Link from "next/link";
import { useState } from "react";

function GoldButton() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleClick() {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/create-checkout-session", { method: "POST" });
      const data = await res.json();
      if (!data.url) throw new Error(JSON.stringify(data));
      window.location.href = data.url;
    } catch (e) {
      setError(String(e));
      setLoading(false);
    }
  }

  return (
    <>
      <button
        onClick={handleClick}
        disabled={loading}
        className="block w-full mt-8 text-center py-2 rounded text-sm transition-opacity hover:opacity-90 disabled:opacity-50"
        style={{ backgroundColor: "#c9a84c", color: "#0d0b07" }}
      >
        {loading ? "読み込み中..." : "Goldトライアルを始める"}
      </button>
      {error && <p className="mt-2 text-xs text-red-400 break-all">{error}</p>}
    </>
  );
}

function CrackSVG() {
  return (
    <svg width="200" height="200" viewBox="0 0 200 200" className="mx-auto mb-8">
      <circle cx="100" cy="100" r="80" fill="none" stroke="#2a2418" strokeWidth="8" />
      <circle cx="100" cy="100" r="80" fill="none" stroke="#c9a84c" strokeWidth="1" opacity="0.3" />
      <path d="M100,20 L95,60 L110,80 L88,110 L105,140 L98,180" className="crack-line" />
      <path d="M88,110 L60,125 L40,120" className="crack-line" style={{animationDelay: '0.5s'}} />
      <path d="M105,140 L130,155 L150,145" className="crack-line" style={{animationDelay: '0.8s'}} />
      <circle cx="100" cy="100" r="4" fill="#c9a84c" className="gold-pulse" />
    </svg>
  );
}

export default function Home() {
  return (
    <div className="min-h-screen" style={{backgroundColor: '#0d0b07'}}>
      {/* Nav */}
      <nav className="flex justify-between items-center px-8 py-5 border-b" style={{borderColor: '#252218'}}>
        <div className="text-xl tracking-widest" style={{color: '#c9a84c'}}>金継ぎ · KINTSUGI</div>
        <div className="flex items-center gap-6">
          <Link href="/journal" className="text-sm opacity-70 hover:opacity-100 transition-opacity" style={{color: '#e8e0d0'}}>
            ジャーナルを開く
          </Link>
          <Link href="/journal" className="text-sm px-4 py-2 rounded border transition-colors" style={{borderColor: '#c9a84c', color: '#c9a84c'}}>
            無料で始める →
          </Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="max-w-4xl mx-auto px-8 py-24 text-center">
        <CrackSVG />
        <p className="text-sm tracking-[0.3em] uppercase mb-4 opacity-50" style={{color: '#c9a84c'}}>
          AI失敗ジャーナル
        </p>
        <h1 className="text-5xl font-light leading-tight mb-6" style={{color: '#e8e0d0'}}>
          失敗は傷ではない。<br />
          <span style={{color: '#c9a84c'}}>それは金の亀裂だ。</span>
        </h1>
        <p className="text-lg opacity-60 max-w-xl mx-auto mb-12 leading-relaxed">
          金継ぎとは、壊れた陶器を金で修復する日本の芸術——
          壊れたことで、より美しくなる。このジャーナルはあなたの人生に同じことをします。
        </p>
        <Link href="/journal"
          className="inline-block px-8 py-4 text-sm tracking-widest uppercase transition-all hover:opacity-90"
          style={{backgroundColor: '#c9a84c', color: '#0d0b07', borderRadius: '2px'}}>
          実践を始める
        </Link>
        <p className="text-xs mt-4 opacity-30">永久無料 · アカウント不要 · データはブラウザに保存</p>
      </section>

      {/* How it works */}
      <section className="max-w-4xl mx-auto px-8 py-16 border-t" style={{borderColor: '#1a1710'}}>
        <h2 className="text-center text-2xl font-light mb-16" style={{color: '#c9a84c'}}>実践の流れ</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-12">
          {[
            {
              num: "一",
              title: "亀裂を書く",
              desc: "失敗、挫折、ミスを正直に書いてください。和らげないで。金は正直な亀裂にしか現れません。"
            },
            {
              num: "二",
              title: "AIが金を見つける",
              desc: "AIがあなたのエントリーを読み、隠れた知恵を発掘します——何を学んだか、どう成長したか、どんな強さが生まれたか。"
            },
            {
              num: "三",
              title: "亀裂を集める",
              desc: "時間とともに、失敗はあなたの成長のギャラリーになります。それぞれの亀裂が、あなたになっていく物語の金の糸。"
            }
          ].map(step => (
            <div key={step.num} className="text-center">
              <div className="text-3xl mb-4" style={{color: '#c9a84c'}}>{step.num}</div>
              <h3 className="text-lg mb-3" style={{color: '#e8e0d0'}}>{step.title}</h3>
              <p className="text-sm leading-relaxed opacity-50">{step.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Pricing */}
      <section className="max-w-4xl mx-auto px-8 py-16 border-t" style={{borderColor: '#1a1710'}}>
        <h2 className="text-center text-2xl font-light mb-4" style={{color: '#c9a84c'}}>シンプルな料金</h2>
        <p className="text-center text-sm opacity-40 mb-12">基本的な実践は常に無料です。</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-2xl mx-auto">
          <div className="p-8 rounded border" style={{borderColor: '#252218', backgroundColor: '#111009'}}>
            <div className="text-lg mb-2" style={{color: '#e8e0d0'}}>無料</div>
            <div className="text-3xl font-light mb-6" style={{color: '#c9a84c'}}>$0</div>
            <ul className="space-y-3 text-sm opacity-60">
              <li>✓ 無制限のジャーナルエントリー</li>
              <li>✓ AI金発掘（月5回）</li>
              <li>✓ ブラウザローカル保存</li>
              <li>✓ ビジュアル亀裂ギャラリー</li>
            </ul>
            <Link href="/journal" className="block mt-8 text-center py-2 rounded border text-sm transition-opacity hover:opacity-80"
              style={{borderColor: '#c9a84c', color: '#c9a84c'}}>
              無料で始める
            </Link>
          </div>
          <div className="p-8 rounded border relative overflow-hidden" style={{borderColor: '#c9a84c', backgroundColor: '#111009'}}>
            <div className="absolute top-3 right-3 text-xs px-2 py-1 rounded" style={{backgroundColor: '#c9a84c', color: '#0d0b07'}}>
              人気
            </div>
            <div className="text-lg mb-2" style={{color: '#e8e0d0'}}>Gold</div>
            <div className="text-3xl font-light mb-1" style={{color: '#c9a84c'}}>$5<span className="text-sm opacity-60">/月</span></div>
            <div className="text-xs opacity-30 mb-6">$45/年 · 25%お得</div>
            <ul className="space-y-3 text-sm opacity-60">
              <li>✓ 無料プランのすべて</li>
              <li>✓ AI分析無制限</li>
              <li>✓ クラウド同期・バックアップ</li>
              <li>✓ 週次知恵ダイジェスト</li>
              <li>✓ エントリー横断のパターン分析</li>
            </ul>
            <GoldButton />
          </div>
        </div>
      </section>

      {/* Quote */}
      <section className="py-20 text-center">
        <blockquote className="max-w-lg mx-auto px-8">
          <p className="text-xl font-light italic opacity-60 leading-relaxed">
            &ldquo;苦い試練に見えるものは、しばしば変装した祝福である。&rdquo;
          </p>
          <footer className="mt-4 text-xs tracking-widest uppercase opacity-30">オスカー・ワイルド</footer>
        </blockquote>
      </section>

      {/* Footer */}
      <footer className="border-t py-8 text-center text-xs opacity-30" style={{borderColor: '#1a1710'}}>
        <p>金継ぎジャーナル · 丁寧に作られました · 金継ぎ</p>
      </footer>
    </div>
  );
}
