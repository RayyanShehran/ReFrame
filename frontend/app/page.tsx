import { Connectivity } from "./connectivity";

export default function Home() {
  return (
    <main className="shell">
      <header className="topbar"><span className="brand">Reframe<span className="brand-dot">.</span></span><Connectivity /></header>
      <section className="hero" aria-labelledby="hero-title">
        <p className="eyebrow">A new way to make an edit your own</p>
        <h1 id="hero-title">Your reference.<br /><em>Your story.</em></h1>
        <p className="intro">Reframe will study the style of an edit you admire, turn it into choices you can shape, then apply those choices to your footage.</p>
      </section>
      <section className="empty-state" aria-labelledby="empty-title">
        <div className="empty-mark" aria-hidden="true">↗</div>
        <div><p className="eyebrow">Coming next</p><h2 id="empty-title">A place for your media</h2><p>Reference and footage uploads arrive in the next milestone. For now, this space shows the foundation is ready.</p></div>
      </section>
      <footer>Reference → Style Blueprint → Your choices → Edit Plan → Render</footer>
    </main>
  );
}
