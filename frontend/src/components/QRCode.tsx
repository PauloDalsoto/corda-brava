import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { QRCodeSVG } from "qrcode.react";
import { PixelButton } from "./PixelButton";
import { PixelPanel } from "./PixelPanel";

/** Clickable QR; click opens a fullscreen overlay with a large QR + the URL. Escape or click closes. */
export default function QRCode({ url }: { url: string }) {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const overlay = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const button = btn.current;
    overlay.current?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      button?.focus();
    };
  }, [open]);
  return (
    <>
      <button
        ref={btn}
        className="qr-btn"
        onClick={() => setOpen(true)}
        aria-label="Ampliar QR code"
      >
        <QRCodeSVG value={url} size={240} marginSize={2} />
      </button>
      {open &&
        createPortal(
          <div
            ref={overlay}
            tabIndex={-1}
            className="qr-overlay"
            role="dialog"
            aria-modal="true"
            aria-label="QR code da partida"
            onClick={() => setOpen(false)}
          >
            <div className="qr-card" onClick={(e) => e.stopPropagation()}>
              <PixelPanel className="qr-card-panel">
                <PixelButton variant="ghost" className="qr-close" aria-label="Fechar" onClick={() => setOpen(false)}>
                  ✕
                </PixelButton>
                <h2 className="pixel-title anim-blink qr-modal-title">APONTE A CÂMERA</h2>
                <div className="qr-tile">
                  <QRCodeSVG value={url} size={512} marginSize={2} />
                </div>
                <small className="qr-hint">Toque fora ou Esc para fechar</small>
              </PixelPanel>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
