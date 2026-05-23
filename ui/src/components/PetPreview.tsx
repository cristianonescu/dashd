/**
 * <PetPreview slug="…" />
 *
 * Asks the agent for the pet's spritesheet (via the IPC `pets_preview` cmd),
 * then paints individual frames into a small canvas, cycling through a
 * random animation every ~5 s.
 *
 * Two anti-flash measures:
 *   1. We *fill* the canvas with the theme background color each frame
 *      (instead of clearRect), so there's never a moment of transparency
 *      bleeding through to whatever's underneath.
 *   2. We respect each animation's real frame count (from frames_per_state)
 *      rather than wrapping by the grid column count — many pets have anims
 *      shorter than 8 frames, and the old cycler walked onto empty cells.
 */
import { useEffect, useRef, useState } from "react";

export type PetPreviewData = {
  slug: string;
  displayName: string;
  creator?: string;
  source_url?: string;
  rows: number;
  cols: number;
  states: string[];
  frames_per_state?: number[];
  image_data_uri: string;
};

type Props = {
  slug: string;
  size?: number;
  fps?: number;
  switchEverySec?: number;
  className?: string;
};

function readBgColor(fallback = "#131820"): string {
  try {
    const v = getComputedStyle(document.documentElement).getPropertyValue("--bg-2").trim();
    return v || fallback;
  } catch {
    return fallback;
  }
}

export function PetPreview({ slug, size = 96, fps = 8, switchEverySec = 5, className }: Props) {
  const [data, setData] = useState<PetPreviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const stateRef = useRef({ animRow: 0, frame: 0, lastFrameMs: 0, lastSwitchMs: 0 });
  const bgRef = useRef<string>(readBgColor());

  useEffect(() => {
    setData(null);
    setError(null);
    const off = window.dashd.onMessage((m: any) => {
      if (m.type !== "event") return;
      if (m.name === "pets_preview" && m.slug === slug) {
        setData(m as PetPreviewData);
      } else if (m.name === "pets_preview_failed" && m.slug === slug) {
        setError(m.error || "preview failed");
      }
    });
    window.dashd.sendCmd({ name: "pets_preview", slug });
    bgRef.current = readBgColor();
    return () => { off(); };
  }, [slug]);

  useEffect(() => {
    if (!data) return;
    const img = new Image();
    let started = false;
    img.onload = () => {
      imgRef.current = img;
      if (!started) { started = true; rafRef.current = requestAnimationFrame(draw); }
    };
    img.src = data.image_data_uri;

    const frameInterval = 1000 / Math.max(1, fps);

    const framesIn = (row: number): number => {
      const arr = data.frames_per_state;
      const n = arr && arr[row] != null ? arr[row] : data.cols;
      return Math.max(1, n);
    };

    const pickRandomAnim = () => {
      const choices: number[] = [];
      for (let i = 0; i < data.rows; i++) if (framesIn(i) > 0) choices.push(i);
      const next = choices.length
        ? choices[Math.floor(Math.random() * choices.length)]
        : 0;
      stateRef.current.animRow = next;
      stateRef.current.frame = 0;
    };
    pickRandomAnim();

    const draw = (t: number) => {
      const c = canvasRef.current;
      const im = imgRef.current;
      if (!c || !im) {
        rafRef.current = requestAnimationFrame(draw);
        return;
      }
      const ctx = c.getContext("2d");
      if (!ctx) return;

      const s = stateRef.current;
      if (t - s.lastSwitchMs >= switchEverySec * 1000) {
        s.lastSwitchMs = t;
        pickRandomAnim();
      }
      if (t - s.lastFrameMs >= frameInterval) {
        s.lastFrameMs = t;
        s.frame = (s.frame + 1) % framesIn(s.animRow);
      }

      const cellW = im.naturalWidth / data.cols;
      const cellH = im.naturalHeight / data.rows;

      // Fill with the card background FIRST (instead of clearRect) so the
      // transparent sprite cells composite onto a solid color, never on a
      // transparent canvas — that's what kills the black flash on switch.
      ctx.fillStyle = bgRef.current;
      ctx.fillRect(0, 0, c.width, c.height);
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(
        im,
        s.frame * cellW, s.animRow * cellH, cellW, cellH,
        0, 0, c.width, c.height,
      );
      rafRef.current = requestAnimationFrame(draw);
    };

    return () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      started = false;
    };
  }, [data, fps, switchEverySec]);

  if (error) {
    return (
      <div className={className}
           style={{ width: size, height: size, display: "flex", alignItems: "center",
                    justifyContent: "center", border: "1px dashed var(--crit)",
                    borderRadius: 6, fontSize: 10, color: "var(--crit)", textAlign: "center", padding: 4 }}>
        {error}
      </div>
    );
  }

  if (!data) {
    return (
      <div className={className}
           style={{ width: size, height: size, display: "flex", alignItems: "center",
                    justifyContent: "center", border: "1px dashed var(--border)",
                    borderRadius: 6, fontSize: 10, color: "var(--dim)" }}>
        loading…
      </div>
    );
  }

  return (
    <canvas
      ref={canvasRef}
      width={size}
      height={size}
      className={className}
      style={{
        width: size, height: size,
        background: "var(--bg-2)",
        border: "1px solid var(--border)", borderRadius: 6,
        imageRendering: "pixelated",
      }}
      aria-label={`${data.displayName} preview`}
      data-hint={
        `${data.displayName}${data.creator ? ` — by ${data.creator}` : ""}. `
        + "Animated preview; it cycles through the pet's animations every few seconds."
      }
    />
  );
}
