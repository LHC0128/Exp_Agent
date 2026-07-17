import type { ReactNode } from "react";

type PageHeadProps = {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
};

export function PageHead({ eyebrow, title, description, action }: PageHeadProps) {
  return (
    <header className="page-head">
      <div>
        <p>{eyebrow}</p>
        <h1>{title}</h1>
        <span>{description}</span>
      </div>
      {action}
    </header>
  );
}
