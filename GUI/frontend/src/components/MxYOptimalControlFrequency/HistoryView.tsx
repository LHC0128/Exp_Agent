import { useState } from "react";
import type { SchemaField } from "../../types/api";
import type { MxYOptimalControlFrequencyRunItem } from "./types";
import { RunList } from "./RunList";
import { RunDetails } from "./RunDetails";

type HistoryViewProps = {
  experimentId: string;
  fields: SchemaField[];
  onFillBack: (parameters: Record<string, unknown>) => void;
  onJumpToRun: () => void;
};

export function HistoryView({ experimentId, fields, onFillBack, onJumpToRun }: HistoryViewProps) {
  const [selected, setSelected] = useState<MxYOptimalControlFrequencyRunItem | null>(null);
  return (
    <div className="zaw-history">
      <RunList selectedRunId={selected?.run_id ?? null} onSelect={setSelected} />
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
