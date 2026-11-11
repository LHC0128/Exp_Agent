import { useState } from "react";

import { useJobActivity } from "../JobActivity";
import { useSetups, type Setup } from "./SetupContext";

export function useSetupSwitch() {
  const { switchTo } = useSetups();
  const { hardwareBusy } = useJobActivity();
  const [switching, setSwitching] = useState(false);
  const [notice, setNotice] = useState("");
  const switchSetup = async (setup: Pick<Setup, "id" | "label">) => {
    if (hardwareBusy || switching || !window.confirm(`切换装置会重新载入物理量映射与现场接线图；尚未保存的映射或接线草稿请先保存。继续切换到“${setup.label}”？`)) return;
    setSwitching(true); setNotice("");
    try { await switchTo(setup.id); setNotice(`已切换到 ${setup.label}`); }
    catch { /* 操作错误由共享装置状态展示。 */ }
    finally { setSwitching(false); }
  };
  return { hardwareBusy, switching, notice, switchSetup };
}
