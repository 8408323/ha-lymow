// Small, fully custom-styled UI kit for the panel (see styles.css, `ly-` prefix).
// Only glyphs come from HA (<ha-icon>, mdi set); everything else is ours.

import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "ha-icon": { icon: string; class?: string; style?: React.CSSProperties };
      "ha-camera-stream": any;
    }
  }
}

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

export function Icon({ name, size }: { name: string; size?: number }) {
  return <ha-icon icon={name} class="ly-icon" style={size ? ({ "--mdc-icon-size": `${size}px` } as any) : undefined} />;
}

type BtnVariant = "primary" | "secondary" | "ghost" | "danger" | "success";

export function Button({
  children,
  icon,
  variant = "secondary",
  size,
  busy,
  disabled,
  onClick,
  title,
  block,
}: {
  children?: ReactNode;
  icon?: string;
  variant?: BtnVariant;
  size?: "sm" | "lg";
  busy?: boolean;
  disabled?: boolean;
  onClick?: (e: React.MouseEvent) => unknown;
  title?: string;
  block?: boolean;
}) {
  const [running, setRunning] = useState(false);
  const isBusy = busy || running;
  return (
    <button
      type="button"
      className={cx("ly-btn", `ly-btn--${variant}`, size && `ly-btn--${size}`, block && "ly-btn--block", !children && "ly-btn--icon")}
      disabled={disabled || isBusy}
      title={title}
      aria-label={title}
      onClick={async (e) => {
        const r = onClick?.(e);
        if (r instanceof Promise) {
          setRunning(true);
          try {
            await r;
          } finally {
            setRunning(false);
          }
        }
      }}
    >
      {isBusy ? <span className="ly-spinner" /> : icon && <Icon name={icon} size={size === "lg" ? 22 : 18} />}
      {children && <span>{children}</span>}
    </button>
  );
}

export function Card({ title, icon, actions, children, className }: { title?: ReactNode; icon?: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("ly-card", className)}>
      {(title || actions) && (
        <header className="ly-card__head">
          {icon && <Icon name={icon} />}
          <h2>{title}</h2>
          <div className="ly-card__actions">{actions}</div>
        </header>
      )}
      <div className="ly-card__body">{children}</div>
    </section>
  );
}

export function Toggle({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => unknown; disabled?: boolean; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className={cx("ly-toggle", checked && "ly-toggle--on")}
      onClick={() => onChange(!checked)}
    >
      <span className="ly-toggle__knob" />
    </button>
  );
}

export function Slider({
  value,
  min,
  max,
  step = 1,
  unit,
  onChange,
  format,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  onChange: (v: number) => void;
  format?: (v: number) => string;
}) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="ly-slider">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        style={{ "--pct": `${pct}%` } as any}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <output>
        {format ? format(value) : value}
        {unit && <small> {unit}</small>}
      </output>
    </div>
  );
}

export function Field({ label, hint, children }: { label: ReactNode; hint?: ReactNode; children: ReactNode }) {
  const id = useId();
  return (
    <div className="ly-field">
      <label htmlFor={id}>{label}</label>
      <div className="ly-field__control" id={id}>
        {children}
      </div>
      {hint && <p className="ly-field__hint">{hint}</p>}
    </div>
  );
}

export function Segmented<T extends string | number>({ value, options, onChange }: { value: T; options: { value: T; label: ReactNode; icon?: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="ly-seg" role="radiogroup">
      {options.map((o) => (
        <button
          type="button"
          role="radio"
          aria-checked={o.value === value}
          key={String(o.value)}
          className={cx("ly-seg__opt", o.value === value && "ly-seg__opt--on")}
          onClick={() => onChange(o.value)}
        >
          {o.icon && <Icon name={o.icon} size={16} />}
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Select<T extends string | number>({ value, options, onChange, disabled }: { value: T | undefined; options: { value: T; label: string }[]; onChange: (v: T) => void; disabled?: boolean }) {
  return (
    <div className="ly-select">
      <select
        value={value === undefined ? "" : String(value)}
        disabled={disabled}
        onChange={(e) => {
          const o = options.find((x) => String(x.value) === e.target.value);
          if (o) onChange(o.value);
        }}
      >
        {value === undefined && <option value="">—</option>}
        {options.map((o) => (
          <option key={String(o.value)} value={String(o.value)}>
            {o.label}
          </option>
        ))}
      </select>
      <Icon name="mdi:chevron-down" size={18} />
    </div>
  );
}

export function Chip({ on, onClick, children, icon }: { on?: boolean; onClick?: () => void; children: ReactNode; icon?: string }) {
  return (
    <button type="button" className={cx("ly-chip", on && "ly-chip--on")} aria-pressed={on} onClick={onClick}>
      {icon && <Icon name={icon} size={16} />}
      {children}
    </button>
  );
}

export function Badge({ tone = "neutral", icon, children }: { tone?: "neutral" | "good" | "warn" | "bad" | "info"; icon?: string; children: ReactNode }) {
  return (
    <span className={cx("ly-badge", `ly-badge--${tone}`)}>
      {icon && <Icon name={icon} size={14} />}
      {children}
    </span>
  );
}

export function Empty({ icon, title, children }: { icon: string; title: string; children?: ReactNode }) {
  return (
    <div className="ly-empty">
      <Icon name={icon} size={40} />
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}

export function TextInput(props: React.ComponentProps<"input">) {
  return <input {...props} className={cx("ly-input", props.className)} />;
}

// ── Dialogs & toasts ─────────────────────────────────────────────────────────

interface DialogReq {
  title: string;
  body?: ReactNode;
  confirm?: string;
  danger?: boolean;
  input?: { label: string; value: string; placeholder?: string; maxLength?: number };
  resolve: (v: string | boolean | null) => void;
}

interface Toast {
  id: number;
  text: string;
  tone: "good" | "bad";
}

interface UiApi {
  confirm(opts: { title: string; body?: ReactNode; confirm?: string; danger?: boolean }): Promise<boolean>;
  prompt(opts: { title: string; label: string; value?: string; placeholder?: string; confirm?: string; maxLength?: number }): Promise<string | null>;
  toast(text: string, tone?: "good" | "bad"): void;
}

const UiContext = createContext<UiApi | null>(null);

export function useUi(): UiApi {
  const ui = useContext(UiContext);
  if (!ui) throw new Error("UiProvider missing");
  return ui;
}

export function UiProvider({ children }: { children: ReactNode }) {
  const [dialog, setDialog] = useState<DialogReq | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const toast = useCallback((text: string, tone: "good" | "bad" = "good") => {
    const id = nextId.current++;
    setToasts((t) => [...t, { id, text, tone }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "bad" ? 6000 : 3000);
  }, []);

  const api: UiApi = {
    confirm: (o) => new Promise((resolve) => setDialog({ ...o, resolve: (v) => resolve(Boolean(v)) })),
    prompt: (o) =>
      new Promise((resolve) =>
        setDialog({ title: o.title, confirm: o.confirm ?? "Save", input: { label: o.label, value: o.value ?? "", placeholder: o.placeholder, maxLength: o.maxLength }, resolve: (v) => resolve(typeof v === "string" ? v : null) }),
      ),
    toast,
  };

  return (
    <UiContext.Provider value={api}>
      {children}
      {dialog && <DialogView req={dialog} close={(v) => (dialog.resolve(v), setDialog(null))} />}
      <div className="ly-toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={cx("ly-toast", `ly-toast--${t.tone}`)}>
            <Icon name={t.tone === "good" ? "mdi:check-circle" : "mdi:alert-circle"} size={18} />
            {t.text}
          </div>
        ))}
      </div>
    </UiContext.Provider>
  );
}

function DialogView({ req, close }: { req: DialogReq; close: (v: string | boolean | null) => void }) {
  const [text, setText] = useState(req.input?.value ?? "");
  const inputRef = useRef<HTMLInputElement>(null);
  const okRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    (inputRef.current ?? okRef.current)?.focus();
    inputRef.current?.select();
  }, []);
  const ok = () => close(req.input ? text.trim() || null : true);
  return (
    <div className="ly-scrim" onMouseDown={(e) => e.target === e.currentTarget && close(req.input ? null : false)}>
      <div
        className="ly-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={req.title}
        onKeyDown={(e) => {
          if (e.key === "Escape") close(req.input ? null : false);
          if (e.key === "Enter" && req.input) ok();
        }}
      >
        <h3>{req.title}</h3>
        {req.body && <div className="ly-dialog__body">{req.body}</div>}
        {req.input && (
          <Field label={req.input.label}>
            <TextInput ref={inputRef} value={text} placeholder={req.input.placeholder} maxLength={req.input.maxLength} onChange={(e) => setText(e.target.value)} />
          </Field>
        )}
        <div className="ly-dialog__actions">
          <Button variant="ghost" onClick={() => close(req.input ? null : false)}>
            Cancel
          </Button>
          <button ref={okRef} type="button" className={cx("ly-btn", req.danger ? "ly-btn--danger" : "ly-btn--primary")} onClick={ok}>
            {req.confirm ?? "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}
