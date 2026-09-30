import { Connectivity } from "./connectivity";
import { Workspace } from "./workspace";

export default function Home() {
  return (
    <main className="shell">
      <header className="topbar"><span className="brand">Reframe<span className="brand-dot">.</span></span><Connectivity /></header>
      <section className="hero" aria-labelledby="hero-title">
        <p className="eyebrow">A new way to make an edit your own</p>
        <h1 id="hero-title">Your reference.<br /><em>Your story.</em></h1>
        <p className="intro">Reframe is being built to study a TikTok reference link, turn its style into choices you can shape, then apply those choices to clips you upload.</p>
      </section>
      <Workspace />
      <footer>TikTok link → Reference analysis → Style Blueprint → Your choices → Edit Plan → Render</footer>
    </main>
  );
}
