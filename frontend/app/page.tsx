import { Connectivity } from "./connectivity";

export default function Home() {
  return (
    <main className="shell">
      <header className="topbar"><span className="brand">Reframe<span className="brand-dot">.</span></span><Connectivity /></header>
      <section className="hero" aria-labelledby="hero-title">
        <p className="eyebrow">A new way to make an edit your own</p>
        <h1 id="hero-title">Your reference.<br /><em>Your story.</em></h1>
        <p className="intro">Reframe is being built to study a TikTok reference link, turn its style into choices you can shape, then apply those choices to clips you upload.</p>
      </section>
      <section className="empty-state" aria-labelledby="empty-title">
        <div className="empty-mark" aria-hidden="true">↗</div>
        <div><p className="eyebrow">In development</p><h2 id="empty-title">TikTok link first</h2><p>Your reference will come from a TikTok link. Uploading your own clips is a separate future capability. Reference analysis is not available yet.</p></div>
      </section>
      <footer>TikTok link → Reference analysis → Style Blueprint → Your choices → Edit Plan → Render</footer>
    </main>
  );
}
