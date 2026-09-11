import { useState } from "react";
import type { RunSummary, SchemaField } from "../../types/api";
import { RunList } from "./RunList";
import { RunDetails } from "./RunDetails";

type HistoryViewProps = {
  experimentId: string;
  fields: SchemaField[];
  onFillBack: (parameters: Record<string, unknown>) => void;
  onJumpToRun: () => void;
};

export function HistoryView({ experimentId, fields, onFillBack, onJumpToRun }: HistoryViewProps) {
  const [selected, setSelected] = useState<RunSummary | null>(null);
  return (
    <div className="zaw-history">
      <RunList selectedRunId={selected?.id ?? null} onSelect={setSelected} />
      <RunDetails
        experimentId={experimentId}
        run={selected}
        fields={fields}
        onFillBack={onFillBack}
        onJumpToRun={onJumpToRun}
      />
    </div>
  );
}
