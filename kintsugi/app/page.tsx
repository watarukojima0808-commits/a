import Link from "next/link";

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
            Open Journal
          </Link>
          <Link href="/journal" className="text-sm px-4 py-2 rounded border transition-colors hover:text-black" style={{borderColor: '#c9a84c', color: '#c9a84c', backgroundColor: 'transparent'}}
            onMouseEnter={undefined}>
            Start Free →
          </Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="max-w-4xl mx-auto px-8 py-24 text-center">
        <CrackSVG />
        <p className="text-sm tracking-[0.3em] uppercase mb-4 opacity-50" style={{color: '#c9a84c'}}>
          The AI Failure Journal
        </p>
        <h1 className="text-5xl font-light leading-tight mb-6" style={{color: '#e8e0d0'}}>
          Your failures are not scars.<br />
          <span style={{color: '#c9a84c'}}>They are gilded cracks.</span>
        </h1>
        <p className="text-lg opacity-60 max-w-xl mx-auto mb-12 leading-relaxed">
          Kintsugi is the Japanese art of repairing broken pottery with gold —
          making it more beautiful for having been broken. This journal does the same for your life.
        </p>
        <Link href="/journal"
          className="inline-block px-8 py-4 text-sm tracking-widest uppercase transition-all hover:opacity-90"
          style={{backgroundColor: '#c9a84c', color: '#0d0b07', borderRadius: '2px'}}>
          Begin Your Practice
        </Link>
        <p className="text-xs mt-4 opacity-30">Free forever · No account required · Your data stays in your browser</p>
      </section>

      {/* How it works */}
      <section className="max-w-4xl mx-auto px-8 py-16 border-t" style={{borderColor: '#1a1710'}}>
        <h2 className="text-center text-2xl font-light mb-16" style={{color: '#c9a84c'}}>The Practice</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-12">
          {[
            {
              num: "一",
              title: "Write the Break",
              desc: "Describe your failure, setback, or mistake honestly. Don't soften it. The gold only shows in honest cracks."
            },
            {
              num: "二",
              title: "AI Finds the Gold",
              desc: "Our AI reads your entry and uncovers the hidden wisdom — what you learned, how you grew, what strength emerged."
            },
            {
              num: "三",
              title: "Collect Your Cracks",
              desc: "Over time, your failures become a gallery of growth. Each crack a golden thread in the tapestry of who you're becoming."
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
        <h2 className="text-center text-2xl font-light mb-4" style={{color: '#c9a84c'}}>Simple Pricing</h2>
        <p className="text-center text-sm opacity-40 mb-12">The core practice is always free.</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-2xl mx-auto">
          <div className="p-8 rounded border" style={{borderColor: '#252218', backgroundColor: '#111009'}}>
            <div className="text-lg mb-2" style={{color: '#e8e0d0'}}>Free</div>
            <div className="text-3xl font-light mb-6" style={{color: '#c9a84c'}}>$0</div>
            <ul className="space-y-3 text-sm opacity-60">
              <li>✓ Unlimited journal entries</li>
              <li>✓ AI gold-finding (5/month)</li>
              <li>✓ Local browser storage</li>
              <li>✓ Visual crack gallery</li>
            </ul>
            <Link href="/journal" className="block mt-8 text-center py-2 rounded border text-sm transition-opacity hover:opacity-80"
              style={{borderColor: '#c9a84c', color: '#c9a84c'}}>
              Start Free
            </Link>
          </div>
          <div className="p-8 rounded border relative overflow-hidden" style={{borderColor: '#c9a84c', backgroundColor: '#111009'}}>
            <div className="absolute top-3 right-3 text-xs px-2 py-1 rounded" style={{backgroundColor: '#c9a84c', color: '#0d0b07'}}>
              Popular
            </div>
            <div className="text-lg mb-2" style={{color: '#e8e0d0'}}>Gold</div>
            <div className="text-3xl font-light mb-1" style={{color: '#c9a84c'}}>$5<span className="text-sm opacity-60">/mo</span></div>
            <div className="text-xs opacity-30 mb-6">$45/year · save 25%</div>
            <ul className="space-y-3 text-sm opacity-60">
              <li>✓ Everything in Free</li>
              <li>✓ Unlimited AI analysis</li>
              <li>✓ Cloud sync & backup</li>
              <li>✓ Weekly wisdom digest</li>
              <li>✓ Pattern insights across entries</li>
            </ul>
            <Link href="/journal" className="block mt-8 text-center py-2 rounded text-sm transition-opacity hover:opacity-90"
              style={{backgroundColor: '#c9a84c', color: '#0d0b07'}}>
              Start Gold Trial
            </Link>
          </div>
        </div>
      </section>

      {/* Quote */}
      <section className="py-20 text-center">
        <blockquote className="max-w-lg mx-auto px-8">
          <p className="text-xl font-light italic opacity-60 leading-relaxed">
            &ldquo;What seems to us as bitter trials are often blessings in disguise.&rdquo;
          </p>
          <footer className="mt-4 text-xs tracking-widest uppercase opacity-30">Oscar Wilde</footer>
        </blockquote>
      </section>

      {/* Footer */}
      <footer className="border-t py-8 text-center text-xs opacity-30" style={{borderColor: '#1a1710'}}>
        <p>Kintsugi Journal · Made with care · 金継ぎ</p>
      </footer>
    </div>
  );
}
