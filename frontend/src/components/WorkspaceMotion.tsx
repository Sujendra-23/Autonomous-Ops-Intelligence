import { useEffect, useState } from "react";
import type { View } from "../navigation";
import { Icon } from "./Icon";
import "./motion.css";

// One flow through the workspace: a reading line tracks scroll position while
// newly reached work sections open once. Content is always visible without JS.
export function WorkspaceMotion({ view }: { view: View }) {
  const [showTop, setShowTop] = useState(false);
  useEffect(() => {
    const content = document.querySelector<HTMLElement>(".studio-content");
    const main = document.getElementById("workspace-main");
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    let frame = 0;
    let previousTop: boolean | null = null;
    const update = () => {
      frame = 0;
      const distance =
        document.documentElement.scrollHeight - window.innerHeight;
      const progress =
        distance > 0 ? Math.max(0, Math.min(1, window.scrollY / distance)) : 0;
      content?.style.setProperty("--workspace-progress", String(progress));
      const nextTop = window.scrollY > 500;
      if (nextTop !== previousTop) {
        setShowTop(nextTop);
        previousTop = nextTop;
      }
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    const resize = window.ResizeObserver ? new ResizeObserver(schedule) : null;
    if (main) resize?.observe(main);

    const animations = new Set<Animation>();
    const visited = new WeakSet<Element>();
    const play = (element: Element) => {
      element.setAttribute("data-flow-state", "reached");
      if (preference.matches || element.contains(document.activeElement))
        return;
      const animation = element.animate(
        [
          {
            opacity: 0.78,
            transform: "translateY(14px)",
            clipPath: "inset(0 0 5% 0)",
          },
          {
            opacity: 1,
            transform: "translateY(0)",
            clipPath: "inset(0 0 0 0)",
          },
        ],
        { duration: 480, easing: "cubic-bezier(.16,1,.3,1)" },
      );
      animations.add(animation);
      void animation.finished
        .catch(() => {})
        .finally(() => animations.delete(animation));
    };
    const observer = window.IntersectionObserver
      ? new IntersectionObserver(
          (entries) => {
            for (const entry of entries) {
              if (!entry.isIntersecting) continue;
              observer?.unobserve(entry.target);
              play(entry.target);
            }
          },
          { threshold: 0.06 },
        )
      : null;
    const scan = () => {
      main
        ?.querySelectorAll(
          ".dashboard-work-grid, .connections-strip, .task-ledger, .meeting-ledger, .desktop-capture-setup, .browser-capture-setup, .roadmap-timeline, .pricing-plans, .weekly-summary section",
        )
        .forEach((element) => {
          if (visited.has(element)) return;
          visited.add(element);
          if (element.getBoundingClientRect().top >= window.innerHeight - 32) {
            observer?.observe(element);
          }
        });
      schedule();
    };
    const mutations = new MutationObserver(scan);
    if (main) mutations.observe(main, { childList: true, subtree: true });
    scan();
    const reduce = () => {
      if (preference.matches) {
        animations.forEach((animation) => animation.cancel());
        animations.clear();
      }
    };
    preference.addEventListener("change", reduce);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      preference.removeEventListener("change", reduce);
      resize?.disconnect();
      mutations.disconnect();
      observer?.disconnect();
      animations.forEach((animation) => animation.cancel());
    };
  }, [view]);
  return showTop ? (
    <button
      className="back-to-top"
      aria-label="Back to top"
      onClick={() => {
        const reduced = window.matchMedia(
          "(prefers-reduced-motion: reduce)",
        ).matches;
        window.scrollTo({ top: 0, behavior: reduced ? "instant" : "smooth" });
        document
          .getElementById("workspace-main")
          ?.focus({ preventScroll: true });
      }}
    >
      <Icon name="arrow" />
    </button>
  ) : null;
}
