/**
 * HintLayer — a single global hover-tooltip surface.
 *
 * Any element anywhere in the app that carries a `data-hint="…"` attribute
 * gets a styled tooltip on hover. One delegated mouseover/mouseout listener
 * on `document` drives one fixed-position bubble, so:
 *   - it's never clipped by a scroll container or a `contain: paint` card
 *     (which a pure-CSS `::after` tooltip would be),
 *   - adding a hint to a new control is just an attribute, no wiring,
 *   - there's exactly one tooltip node in the DOM regardless of control count.
 *
 * Mount it once, at the app root.
 */
import { useEffect, useState } from "react";

type HintState = { text: string; x: number; y: number; below: boolean };

/** Walk up from an event target to the nearest element carrying data-hint. */
function findHintEl(target: EventTarget | null): HTMLElement | null {
  let node = target as HTMLElement | null;
  while (node && node !== document.body) {
    if (node.dataset && node.dataset.hint) return node;
    node = node.parentElement;
  }
  return null;
}

export default function HintLayer() {
  const [hint, setHint] = useState<HintState | null>(null);

  useEffect(() => {
    let current: HTMLElement | null = null;

    const show = (el: HTMLElement) => {
      const text = el.dataset.hint;
      if (!text) { setHint(null); return; }
      const r = el.getBoundingClientRect();
      // Prefer above; flip below if there isn't ~64px of headroom.
      const below = r.top < 64;
      // Clamp x so the (max 280px) bubble can't run off either edge.
      const half = 145;
      const x = Math.min(
        Math.max(r.left + r.width / 2, half),
        window.innerWidth - half,
      );
      setHint({ text, x, y: below ? r.bottom + 8 : r.top - 8, below });
    };

    const onOver = (e: MouseEvent) => {
      const el = findHintEl(e.target);
      if (el === current) return;
      current = el;
      if (el) show(el);
      else setHint(null);
    };
    const onOut = (e: MouseEvent) => {
      // Hide when the pointer leaves to something with no hint (incl. window).
      const el = findHintEl(e.relatedTarget);
      if (el !== current) {
        current = el;
        if (el) show(el);
        else setHint(null);
      }
    };
    // Any scroll / focus change invalidates the cached rect — just hide.
    const dismiss = () => { current = null; setHint(null); };

    document.addEventListener("mouseover", onOver);
    document.addEventListener("mouseout", onOut);
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("blur", dismiss);
    return () => {
      document.removeEventListener("mouseover", onOver);
      document.removeEventListener("mouseout", onOut);
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("blur", dismiss);
    };
  }, []);

  if (!hint) return null;
  return (
    <div
      className={`hint-tip ${hint.below ? "below" : "above"}`}
      style={{ left: hint.x, top: hint.y }}
      role="tooltip"
    >
      {hint.text}
    </div>
  );
}
