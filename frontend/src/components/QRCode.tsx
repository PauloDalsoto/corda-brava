import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { QRCodeSVG } from "qrcode.react";

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
            <QRCodeSVG
              value={url}
              size={Math.min(window.innerWidth, window.innerHeight) * 0.7}
              marginSize={2}
            />
            <p>{url}</p>
            <small>Clique ou pressione Esc para fechar</small>
          </div>,
          document.body,
        )}
    </>
  );
}
