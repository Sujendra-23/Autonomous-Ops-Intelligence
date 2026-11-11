import { useEffect, useState } from "react";
import type { View } from "../navigation";
import { Icon } from "./Icon";
import "./motion.css";

export function WorkspaceMotion({ view }: { view: View }) {
  const [showTop, setShowTop] = useState(false);
  useEffect(() => {
    const update = () => setShowTop(window.scrollY > 500);
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, []);
  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (preference.matches || !window.IntersectionObserver) return;
    const animations: Animation[] = [];
    const main = document.getElementById("workspace-main");
    if (main) animations.push(main.animate([
      { opacity: .6, transform: "translateY(7px)" },
      { opacity: 1, transform: "translateY(0)" },
    ], { duration: 220, easing: "cubic-bezier(.16,1,.3,1)" }));
    const observer = new IntersectionObserver(entries => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        animations.push(entry.target.animate([
          { opacity: .45, transform: "translateY(20px)" },
          { opacity: 1, transform: "translateY(0)" },
        ], { duration: 420, easing: "cubic-bezier(.16,1,.3,1)" }));
        observer.unobserve(entry.target);
      }
    }, { threshold: .08 });
    main?.querySelectorAll(".dashboard-work-grid, .connections-strip, .task-ledger, .setup-steps, .pricing-plans").forEach(element => {
      if (element.getBoundingClientRect().top >= window.innerHeight - 30) observer.observe(element);
    });
    const cancel = () => { observer.disconnect(); animations.forEach(animation => animation.cancel()); };
    preference.addEventListener("change", cancel);
    return () => { cancel(); preference.removeEventListener("change", cancel); };
  }, [view]);
  return showTop ? <button className="back-to-top" aria-label="Back to top" onClick={() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: reduced ? "instant" : "smooth" });
    document.getElementById("workspace-main")?.focus({ preventScroll: true });
  }}><Icon name="arrow"/></button> : null;
}
