import type { ReactNode } from "react";

export function Status({ tone = "ok", children }: { tone?: string; children: ReactNode }) {
  return (
    <span className={`status ${tone}`}>
      <i />
      {children}
    </span>
  );
}
