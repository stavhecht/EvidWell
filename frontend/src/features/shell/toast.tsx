/**
 * The one transient message in the product.
 *
 * Save, move, share and "sign up to keep this" all need to say something
 * without taking over the page or moving what the reader was looking at. A
 * pill at the bottom of the viewport is what the comp uses and it is the right
 * shape: it is the only element allowed to appear without being asked for, so
 * there is exactly one of them and it always says one sentence.
 *
 * What it is not: an error channel. A failed request that the reader has to act
 * on belongs next to the control that failed, where it stays put and can be
 * read twice. This disappears after 2.6 seconds.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { TOAST } from "./styles";

const ToastContext = createContext<((message: string) => void) | null>(null);

const DISMISS_AFTER_MS = 2600;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState("");
  const timer = useRef<number | undefined>(undefined);

  const flash = useCallback((next: string) => {
    // Clearing first means a second save while the first toast is still up
    // restarts the clock rather than inheriting the remainder of it.
    window.clearTimeout(timer.current);
    setMessage(next);
    timer.current = window.setTimeout(() => setMessage(""), DISMISS_AFTER_MS);
  }, []);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const value = useMemo(() => flash, [flash]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/*
        `role="status"` with `aria-live="polite"` rather than an alert: this
        confirms something the reader just did, so it should be announced after
        whatever they are currently reading rather than interrupting it. The
        region is always in the tree so a screen reader has something to watch;
        only its contents change.
      */}
      <div role="status" aria-live="polite">
        {message ? <div className={TOAST}>{message}</div> : null}
      </div>
    </ToastContext.Provider>
  );
}

/** Show a one-line confirmation. No-op outside the provider, never a crash. */
export function useToast(): (message: string) => void {
  return useContext(ToastContext) ?? noop;
}

function noop(): void {}
