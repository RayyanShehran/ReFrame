import { cloneElement, isValidElement, useId, type ButtonHTMLAttributes, type HTMLAttributes, type ReactElement, type ReactNode } from "react";

// UI primitives: native elements with the design-token styles in globals.css. Every prop passes
// through, so accessible names, roles and handlers stay exactly what the caller gives.

const join = (...names: (string | false | undefined)[]) => names.filter(Boolean).join(" ");

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export function Button({ variant = "secondary", size = "md", className, type = "button", ...props }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; size?: "md" | "lg" }) {
  return <button type={type} className={join("btn", `btn-${variant}`, `btn-${size}`, className)} {...props} />;
}

export type BadgeState = "ready" | "needs-input" | "working" | "needs-attention";
const badgeText: Record<BadgeState, string> = { "ready": "Ready", "needs-input": "Needs input", "working": "Working", "needs-attention": "Needs attention" };
export function Badge({ state, children, className, ...props }: HTMLAttributes<HTMLSpanElement> & { state: BadgeState }) {
  return <span className={join("badge", `badge-${state}`, className)} {...props}><span className="badge-dot" aria-hidden="true" />{children ?? badgeText[state]}</span>;
}

export type Tone = "info" | "success" | "warning" | "danger";
/** A tinted message. Give `role="alert"` or `role="note"` where the old markup had them. */
export function Banner({ tone = "info", lead, children, className, ...props }: HTMLAttributes<HTMLDivElement> & { tone?: Tone; lead?: ReactNode }) {
  return <div className={join("banner", `banner-${tone}`, className)} {...props}>{lead && <strong className="banner-lead">{lead}</strong>}{children}</div>;
}

export function Panel({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return <section className={join("panel", className)} {...props} />;
}

/** A label and optional hint wired to one native control (input, select or textarea). */
export function Field({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactElement<{ id?: string; "aria-describedby"?: string }>; className?: string }) {
  const auto = useId();
  if (!isValidElement(children)) return null;
  const id = children.props.id ?? `${auto}-control`, hintId = `${auto}-hint`;
  const describedBy = [children.props["aria-describedby"], hint ? hintId : undefined].filter(Boolean).join(" ") || undefined;
  return <div className={join("field", className)}>
    <label htmlFor={id}>{label}</label>
    {cloneElement(children, { id, "aria-describedby": describedBy })}
    {hint && <p id={hintId} className="hint">{hint}</p>}
  </div>;
}

export function EmptyState({ title, children, action, className, ...props }: HTMLAttributes<HTMLDivElement> & { title: ReactNode; action?: ReactNode }) {
  return <div className={join("empty-state", className)} {...props}><p className="empty-state-title">{title}</p>{children && <div className="hint">{children}</div>}{action}</div>;
}

/** Ordered steps with the current one marked; earlier steps read as done. */
export function ProgressSteps({ steps, current, className, ...props }: HTMLAttributes<HTMLOListElement> & { steps: readonly string[]; current: string }) {
  const at = steps.indexOf(current);
  return <ol className={join("progress-steps", className)} {...props}>{steps.map((step, i) =>
    <li key={step} data-state={i < at ? "done" : i === at ? "current" : "upcoming"} aria-current={i === at ? "step" : undefined}>
      {step}{i < at && <span className="visually-hidden"> (done)</span>}
    </li>)}</ol>;
}
