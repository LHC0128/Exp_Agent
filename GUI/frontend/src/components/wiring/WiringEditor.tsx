import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ApiError } from "../../api";
import { useUnsavedChanges } from "../NavigationGuard";
import { PageHead } from "../PageHead";
import { WiringCanvas, type WiringCanvasHandle } from "./WiringCanvas";
import { nodePlacementSize } from "./wiringPlacement";
import { type WiringComparison, type WiringDocument as WiringResponse, type WiringEdge, type WiringGraph } from "./wiringTypes";
import { WiringChecklist } from "./WiringChecklist";
import { portUsage, type PortUsage } from "./portUsage";
import { useWiringHistory } from "./useWiringHistory";
import { compareNodeProfile, syncNodeFromProfile, wiringSyncBadgeLabel, wiringSyncSummaryLines, type WiringSyncBadge, type WiringSyncResult } from "./wiringSync";
import { buildWiringPreflight, requiredDevicePresent, wiringPreflightEmptyText, type PreflightItem, type PreflightSeverity, type PreflightSource } from "./wiringPreflight";
import type { ExperimentDefinition } from "../../types/api";
import type { components } from "../../types/openapi";
import "../../pages/wiring.css";

type WiringRevision = components["schemas"]["WiringRevision"];
type WiringEndpoint = components["schemas"]["WiringEndpoint"];
type WiringWarning = components["schemas"]["WiringWarning"];
type DevicePortProfile = components["schemas"]["DevicePortProfile"];
type PhysicalPort = components["schemas"]["PhysicalPort"];
type InternalPort = components["schemas"]["WiringInternalPort"];
type WiringSetting = components["schemas"]["WiringSetting"];
const usageName = (value: string) => value === "used" ? "使用" : "未用";
const preflightSeverityLabel: Record<PreflightSeverity, string> = { blocking: "阻断", warning: "警告", info: "提示" };
const preflightSourceLabel: Record<PreflightSource, string> = { validation: "校验", requirement: "要求", follow: "跟随", diagnostic: "诊断", route: "路由", profile: "档案" };
type DevicePortsResponse = components["schemas"]["DevicePortsResponse"];
type Draft = Pick<WiringResponse, "site" | "targets">;
const FIRST_TARGET = "rf-sensitivity-aw";
const FIRST_TARGET_FALLBACK = "RF 灵敏度（DirectAW）";
const emptyGraph = (): WiringGraph => ({ nodes: [], edges: [] });
const NO_GRAPH: WiringGraph = emptyGraph();
const portLabel = (deviceName: string, port: { group: string; name: string }) => [deviceName, port.group, port.name].filter(Boolean).join(" / ");
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const signalName = (value: WiringEdge["signal_type"] | null) => value === "internal" ? "内部信号（非线缆）" : value === "optical" ? "光学" : value === "electrical" ? "电学" : "光/电待确认";
const cableInput = (value: string) => value.trim() || null;
const cableSummary = (edge: WiringEdge) => [edge.connector, edge.cable_id].filter(Boolean).join(" / ");
const sameEndpoint = (a: WiringEndpoint, b: WiringEndpoint) => a.device_id === b.device_id && a.port_id === b.port_id;
const mappingKeysToken = (keys: string[]) => JSON.stringify([...keys].sort());
const attached = (edge: WiringEdge, deviceId: string, portId?: string) => [edge.source, edge.target].some(endpoint => endpoint.device_id === deviceId && (portId === undefined || endpoint.port_id === portId));
function endpointName(graph: WiringGraph, endpoint: WiringEndpoint) {
  const node = graph.nodes.find(item => item.device.id === endpoint.device_id);
  const port = node?.device.ports.find(item => item.id === endpoint.port_id);
  const internal = node?.internal_ports?.find(item => item.id === endpoint.port_id);
  return [node?.device.name || "未找到器件", internal ? "内部" : port?.group, internal?.name || port?.name || "未找到端口"].filter(Boolean).join(" / ");
}
function checkedDocument(value: WiringResponse): WiringResponse {
  if (value.schema_version !== 1 || typeof value.revision !== "string" || !Array.isArray(value.site?.nodes) || !Array.isArray(value.site?.edges)
    || !value.targets || typeof value.targets !== "object" || Array.isArray(value.targets)
    || !Object.values(value.targets).every(graph => Array.isArray(graph?.nodes) && Array.isArray(graph?.edges))
    || !Array.isArray(value.diagnostics?.site) || !value.diagnostics?.targets) throw new Error("接线图格式不受支持，未载入；请检查服务端后重试。");
  return value;
}

/** 全局接线页与固定实验目标共用编辑实现；目标模式只保存当前实验的草稿。 */
export function WiringEditor({ experimentId, experimentTitle, active: visible = true }: { experimentId?: string; experimentTitle?: string; active?: boolean }) {
  const [saved, setSaved] = useState<WiringResponse | null>(null);
  const [draft, setDraft] = useState<Draft>({ site: emptyGraph(), targets: {} });
  const [view, setView] = useState<"site" | "target">(experimentId ? "target" : "site");
  const [targetId, setTargetId] = useState(experimentId ?? FIRST_TARGET);
  const [selectedDevice, setSelectedDevice] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);
  const [blockedEdgeIds, setBlockedEdgeIds] = useState<string[]>([]);
  const [pair, setPair] = useState<{ source: WiringEndpoint; target: WiringEndpoint } | null>(null);
  const [pairType, setPairType] = useState<"" | WiringEdge["signal_type"]>("");
  const [pairLabel, setPairLabel] = useState("");
  const [pairError, setPairError] = useState("");
  const [canvasEpoch, setCanvasEpoch] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState("");
  const [remoteUpdate, setRemoteUpdate] = useState<WiringResponse | null>(null);
  const [updateError, setUpdateError] = useState("");
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  const backgroundRead = useRef<AbortController | null>(null);
  const revisionUnsupported = useRef(false);
  const previousVisible = useRef(visible);
  const [reload, setReload] = useState(0);
  const [profiles, setProfiles] = useState<DevicePortProfile[]>([]);
  const [profilesLoading, setProfilesLoading] = useState(true);
  const [profilesError, setProfilesError] = useState("");
  const [syncPreview, setSyncPreview] = useState<WiringSyncResult | null>(null);
  const [experiments, setExperiments] = useState<ExperimentDefinition[]>([]);
  const [experimentsError, setExperimentsError] = useState("");
  const [experimentsLoading, setExperimentsLoading] = useState(true);
  const [requirements, setRequirements] = useState<ExperimentDefinition | null>(null);
  const [requirementsLoading, setRequirementsLoading] = useState(false);
  const [requirementsError, setRequirementsError] = useState("");
  const [comparison, setComparison] = useState<WiringComparison | null>(null);
  const [comparing, setComparing] = useState(false);
  const [compareError, setCompareError] = useState("");
  const compareSequence = useRef(0);
  const canvasRef = useRef<WiringCanvasHandle>(null);
  // 每张图只在本次编辑会话首次进入时自动取景一次，切回或再次渲染不再改变视角。
  const autoFitted = useRef(new Set<string>());
  const prefix = useId();
  const savedFingerprint = useMemo(() => saved && (experimentId
    ? JSON.stringify(saved.targets[experimentId])
    : JSON.stringify({ site: saved.site, targets: saved.targets })), [saved, experimentId]);
  const draftFingerprint = useMemo(() => experimentId
    ? JSON.stringify(draft.targets[experimentId])
    : JSON.stringify(draft), [draft, experimentId]);
  const dirty = saved !== null && savedFingerprint !== draftFingerprint;
  const locked = loading || saving || saved === null;
  useUnsavedChanges(dirty);
  const graph = useMemo(() => view === "site" ? draft.site : draft.targets[targetId] ?? NO_GRAPH, [view, draft, targetId]);
  const targetGraph = useMemo(() => draft.targets[targetId] ?? NO_GRAPH, [draft, targetId]);
  const graphKey = view === "site" ? "site" : `target:${targetId}`;
  const history = useWiringHistory<WiringGraph>(graphKey);
  const activeNode = graph.nodes.find(node => node.device.id === selectedDevice);
  const active = activeNode?.device;
  const activeEdge = graph.edges.find(edge => edge.id === selectedEdge);
  const mappingGroups = saved?.mapping_groups ?? [];
  const supportsFollow = saved?.mapping_groups !== undefined && saved.follow_status !== undefined;
  const followStatuses = view === "site" ? saved?.follow_status?.site ?? [] : saved?.follow_status?.targets?.[targetId] ?? [];
  function groupsForDevice(deviceLibraryId: string | null) {
    return deviceLibraryId ? mappingGroups.filter(group => group.device_library_id === deviceLibraryId) : [];
  }
  // 手工节点没有设备库身份，只保留独立快照；已有绑定不能因过滤而静默消失。
  const manualDevice = active?.device_library_id == null;
  const activeGroups = active ? groupsForDevice(active.device_library_id) : [];
  // 档案未读完或读取失败时不推断“不存在”，避免辅助数据降级造成误报徽标。
  const profileBadges = useMemo(() => {
    const badges: Partial<Record<string, WiringSyncBadge[]>> = {};
    if (profilesLoading || profilesError) return badges;
    for (const node of graph.nodes) {
      if (!node.device.device_library_id) continue;
      const comparison = compareNodeProfile(node, profiles);
      if (comparison.badges.length) badges[node.device.id] = comparison.badges;
    }
    return badges;
  }, [graph, profiles, profilesLoading, profilesError]);
  const activeProfile = !profilesLoading && !profilesError && active?.device_library_id
    ? profiles.find(profile => profile.device_library_id === active.device_library_id) ?? null
    : null;
  const activeBadges = active ? profileBadges[active.id] ?? [] : [];
  const boundKeys = activeNode?.follow_mapping ?? null;
  const hasBoundKeys = boundKeys !== null && boundKeys.length > 0;
  const boundGroup = hasBoundKeys ? activeGroups.find(group => mappingKeysToken(group.keys) === mappingKeysToken(boundKeys)) : undefined;
  const foreignGroup = hasBoundKeys && !boundGroup ? mappingGroups.find(group => mappingKeysToken(group.keys) === mappingKeysToken(boundKeys)) : undefined;
  const followValue = hasBoundKeys ? mappingKeysToken(boundKeys) : manualDevice ? "independent" : boundKeys === null ? "auto" : "independent";
  const currentRead = useRef({ saved, dirty, loading, saving, pair, visible, supportsFollow });
  currentRead.current = { saved, dirty, loading, saving, pair, visible, supportsFollow };
  const targetOptions = useMemo(() => {
    if (experimentId) return [{ id: experimentId, name: experimentTitle || experimentId }];
    const options = new Map<string, string>();
    experiments.forEach(experiment => options.set(experiment.id, `${experiment.title}${experiment.archived ? "（已归档）" : ""}`));
    if (!options.has(FIRST_TARGET)) options.set(FIRST_TARGET, FIRST_TARGET_FALLBACK);
    Object.keys(draft.targets).forEach(id => { if (!options.has(id)) options.set(id, `${id}（已有目标，实验未在当前列表）`); });
    return Array.from(options, ([id, name]) => ({ id, name }));
  }, [experiments, draft.targets, experimentId, experimentTitle]);
  const targetName = (id: string) => targetOptions.find(option => option.id === id)?.name ?? id;
  const validation = useMemo(() => {
    const problems: string[] = [];
    const graphs: Array<[string, WiringGraph]> = experimentId
      ? [[experimentTitle || experimentId, draft.targets[experimentId] ?? emptyGraph()]]
      : [["现场", draft.site], ...Object.entries(draft.targets)];
    for (const [name, value] of graphs) {
      for (const node of value.nodes) {
        if (!node.device.name.trim()) problems.push(`${name}：器件名称不能为空。`);
        if (node.device.ports.some(port => !port.name.trim())) problems.push(`${name} / ${node.device.name || "未命名器件"}：端口名称不能为空。`);
        if (node.internal_ports?.some(port => !port.name.trim())) problems.push(`${name} / ${node.device.name || "未命名器件"}：内部端口名称不能为空。`);
        if (node.settings?.some(setting => !setting.name.trim() || !setting.options.length || setting.options.some(option => !option.value.trim() || !option.label.trim()) || new Set(setting.options.map(option => option.value)).size !== setting.options.length || setting.value != null && !setting.options.some(option => option.value === setting.value))) problems.push(`${name} / ${node.device.name || "未命名器件"}：图内设置名称、唯一选项及选定值需有效。`);
      }
    }
    return problems;
  }, [draft, experimentId, experimentTitle]);
  // 固定实验沿用独立 requests；全局目标视图只从已载入的实验列表取定义，不额外请求。
  const requirementInfo = experimentId ? requirements : view === "target" ? experiments.find(item => item.id === targetId) ?? null : null;
  const requirementsUnavailable = !experimentId && view === "target" && !experimentsLoading && !experimentsError && !requirementInfo;
  const savedDiagnostics = useMemo(() => saved === null ? [] : experimentId
    ? saved.diagnostics.targets[experimentId] ?? []
    : view === "site" ? saved.diagnostics.site : saved.diagnostics.targets[targetId] ?? [], [saved, experimentId, view, targetId]);
  const preflight = useMemo(() => buildWiringPreflight({
    validation,
    requirements: requirementInfo ? { required_devices: requirementInfo.required_devices, required_mapping_keys: requirementInfo.required_mapping_keys } : null,
    requirementsError: experimentId ? requirementsError : "",
    // 目标视图与固定实验下 graph 即目标图；现场视图用它为诊断/阻塞连线取端点名称。
    targetGraph: graph,
    mappingGroupsAvailable: saved?.mapping_groups !== undefined,
    followStatuses,
    diagnostics: savedDiagnostics,
    diagnosticsStale: dirty,
    blockedEdgeIds,
    profileBadges,
    deviceName: id => graph.nodes.find(node => node.device.id === id)?.device.name || id,
  }), [validation, requirementInfo, requirementsError, experimentId, targetGraph, saved, followStatuses, savedDiagnostics, dirty, blockedEdgeIds, profileBadges, graph]);
  const preflightBlocking = preflight.filter(item => item.severity === "blocking").length;
  const preflightWarnings = preflight.filter(item => item.severity === "warning").length;
  const preflightInfo = preflight.length - preflightBlocking - preflightWarnings;

  useEffect(() => { setBlockedEdgeIds([]); }, [graphKey]);

  useEffect(() => {
    const controller = new AbortController();
    backgroundRead.current?.abort(); backgroundRead.current = null;
    revisionUnsupported.current = false;
    setCheckingUpdate(false); setRemoteUpdate(null); setUpdateError("");
    setLoading(true); setLoadError(""); setProfilesLoading(true); setProfilesError(""); setExperimentsLoading(true); setExperimentsError(""); setSyncPreview(null);
    setRequirements(null); setRequirementsError(""); setRequirementsLoading(!!experimentId);
    void api<WiringResponse>("/api/wiring", { signal: controller.signal }).then(checkedDocument).then(document => {
      if (controller.signal.aborted) return;
      setSaved(document); setDraft(structuredClone({ site: document.site, targets: document.targets }));
      history.clear();
      setSelectedDevice(null); setSelectedEdge(null); setPair(null); setPairError(""); setCanvasEpoch(value => value + 1);
      setSaveError(""); setConflict(false); setNotice(""); setSyncPreview(null); compareSequence.current += 1; setComparison(null); setCompareError(""); setComparing(false);
    }).catch(error => { if (!controller.signal.aborted) setLoadError(message(error)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    // 辅助数据独立降级，绝不能用失败的档案/实验列表清空已保存的图。
    void api<DevicePortsResponse>("/api/device-ports", { signal: controller.signal }).then(document => {
      if (!Array.isArray(document.devices)) throw new Error("端口档案格式不受支持");
      if (!controller.signal.aborted) setProfiles(document.devices);
    }).catch(error => { if (!controller.signal.aborted) { setProfiles([]); setProfilesError(message(error)); } })
      .finally(() => { if (!controller.signal.aborted) setProfilesLoading(false); });
    if (experimentId) {
      setExperimentsLoading(false);
      // 仅展示实验定义中的接线要点与设备要求；读取失败只提示，不阻塞图编辑。
      void api<ExperimentDefinition>(`/api/experiments/${encodeURIComponent(experimentId)}`, { signal: controller.signal }).then(document => {
        if (!Array.isArray(document?.wiring_notes) || !Array.isArray(document.required_devices) || !Array.isArray(document.required_mapping_keys)) throw new Error("实验接线要点格式不受支持");
        if (!controller.signal.aborted) setRequirements(document);
      }).catch(error => { if (!controller.signal.aborted) { setRequirements(null); setRequirementsError(message(error)); } })
        .finally(() => { if (!controller.signal.aborted) setRequirementsLoading(false); });
    } else void api<ExperimentDefinition[]>("/api/experiments", { signal: controller.signal }).then(document => {
      if (!Array.isArray(document)) throw new Error("实验列表格式不受支持");
      if (!controller.signal.aborted) setExperiments(document);
    }).catch(error => { if (!controller.signal.aborted) { setExperiments([]); setExperimentsError(message(error)); } })
      .finally(() => { if (!controller.signal.aborted) setExperimentsLoading(false); });
    return () => { controller.abort(); compareSequence.current += 1; };
  }, [reload, experimentId]);

  // 后台检查先只读组合修订，未变化时不拉取完整文档；不碰配置写入口，回执到达时再次核对草稿和保存状态。
  async function readRevision(signal: AbortSignal): Promise<string | null> {
    if (revisionUnsupported.current) return null;
    try {
      const value = await api<WiringRevision>("/api/wiring/revision", { signal });
      if (typeof value?.revision !== "string") throw new Error("接线图修订接口返回格式不受支持");
      return value.revision;
    } catch (error) {
      // 旧后端没有增量接口时只静默回退为一次全量读取，不反复提示同一错误。
      if ((error as ApiError).status === 404) { revisionUnsupported.current = true; return null; }
      throw error;
    }
  }
  async function checkUpdates() {
    const before = currentRead.current;
    if (!before.supportsFollow || !before.visible || before.loading || before.saving || !before.saved || backgroundRead.current) return;
    const controller = new AbortController();
    backgroundRead.current = controller; setCheckingUpdate(true); setUpdateError("");
    try {
      const revision = await readRevision(controller.signal);
      if (controller.signal.aborted) return;
      const current = currentRead.current;
      if (current.loading || current.saving || !current.saved || current.saved.revision !== before.saved.revision) return;
      if (revision !== null && revision === current.saved.revision) { setRemoteUpdate(null); return; }
      const document = checkedDocument(await api<WiringResponse>("/api/wiring", { signal: controller.signal }));
      const now = currentRead.current;
      if (controller.signal.aborted || now.loading || now.saving || !now.saved || now.saved.revision !== before.saved.revision) return;
      if (document.revision === now.saved.revision) { setRemoteUpdate(null); return; }
      if (now.dirty || now.pair || !now.visible) {
        setRemoteUpdate(document); clearComparison();
      } else {
        setSaved(document); setDraft(structuredClone({ site: document.site, targets: document.targets }));
        resetSelection(); clearComparison(); history.clear(); setRemoteUpdate(null); setConflict(false); setSaveError("");
        setNotice("已按最新用途映射刷新现场和全部实验目标的显示；未操作硬件，无法同步的图保留原记录并提示。");
      }
    } catch (error) { if (!controller.signal.aborted) setUpdateError(message(error)); }
    finally {
      if (backgroundRead.current === controller) { backgroundRead.current = null; setCheckingUpdate(false); }
    }
  }
  useEffect(() => {
    const focus = () => { if (document.visibilityState !== "hidden") void checkUpdates(); };
    const visibleAgain = () => { if (document.visibilityState === "visible") void checkUpdates(); };
    window.addEventListener("focus", focus); document.addEventListener("visibilitychange", visibleAgain);
    return () => { window.removeEventListener("focus", focus); document.removeEventListener("visibilitychange", visibleAgain); backgroundRead.current?.abort(); };
  }, []);
  useEffect(() => {
    const wasVisible = previousVisible.current; previousVisible.current = visible;
    if (visible && !wasVisible) void checkUpdates();
  }, [visible]);
  const historyActions = useRef({ undo: () => {}, redo: () => {}, active: true, locked: true });
  historyActions.current = { undo: undoGraph, redo: redoGraph, active: visible, locked };
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      const current = historyActions.current;
      if (!current.active || current.locked || !(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== "z") return;
      // 输入框保留原生文本撤销；只在非文本控件上处理图级撤销。
      const target = event.target instanceof Element ? event.target : null;
      if (target?.closest("input,textarea,select,[contenteditable='true']")) return;
      event.preventDefault();
      if (event.shiftKey) current.redo(); else current.undo();
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, []);

  function clearComparison() { compareSequence.current += 1; setComparison(null); setCompareError(""); setComparing(false); }
  function resetSelection() { setSelectedDevice(null); setSelectedEdge(null); setPair(null); setPairError(""); setSyncPreview(null); setCanvasEpoch(value => value + 1); }
  function locatePreflight(item: PreflightItem) {
    if (!item.focus) return;
    if (item.focus.kind === "device") { setSelectedDevice(item.focus.deviceId); setSelectedEdge(null); }
    else { setSelectedEdge(item.focus.edgeId); setSelectedDevice(null); }
    setPair(null); setPairError(""); setSyncPreview(null);
  }
  function changeGraph(next: WiringGraph, mergeKey: string | null = null) {
    if (locked || JSON.stringify(next) === JSON.stringify(graph)) return;
    history.record(graph, mergeKey);
    setDraft(current => view === "site" ? { ...current, site: next } : { ...current, targets: { ...current.targets, [targetId]: next } });
    clearComparison(); setSyncPreview(null); setNotice("");
  }
  function restoreGraph(next: WiringGraph, action: "撤销" | "重做") {
    setDraft(current => view === "site" ? { ...current, site: next } : { ...current, targets: { ...current.targets, [targetId]: next } });
    setPair(null); setPairError("");
    setSelectedDevice(id => id !== null && next.nodes.some(node => node.device.id === id) ? id : null);
    setSelectedEdge(id => id !== null && next.edges.some(edge => edge.id === id) ? id : null);
    setSyncPreview(null); setCanvasEpoch(value => value + 1);
    clearComparison(); setNotice(`已${action}一步图内草稿；仅修改当前图记录，尚未保存。`);
  }
  function undoGraph() {
    if (locked) return;
    const previous = history.undo(graph);
    if (previous) restoreGraph(previous, "撤销");
  }
  function redoGraph() {
    if (locked) return;
    const next = history.redo(graph);
    if (next) restoreGraph(next, "重做");
  }
  function switchView(next: "site" | "target") {
    if (experimentId || locked || next === view) return;
    setView(next); resetSelection();
  }
  function chooseTarget(id: string) {
    if (experimentId || locked || id === targetId) return;
    setTargetId(id); clearComparison(); setSyncPreview(null); if (view === "target") resetSelection();
  }
  function previewSync() {
    if (locked || !activeNode || !activeProfile) return;
    setSyncPreview(syncNodeFromProfile(activeNode, activeProfile));
  }
  function applySync() {
    if (locked || !activeNode || !syncPreview?.ok || !syncPreview.changed) return;
    const synced = structuredClone(syncPreview.node);
    const deviceId = activeNode.device.id;
    changeGraph({ ...graph, nodes: graph.nodes.map(node => node.device.id === deviceId ? synced : node) });
    setSyncPreview(null);
    setNotice("已从设备端口档案同步当前图器件快照；仅修改当前图草稿，尚未保存，原档案、其它图和硬件均未改变。");
  }
  function alreadyAdded(profile: DevicePortProfile) {
    return graph.nodes.some(node => node.device.id === profile.id || (profile.device_library_id !== null && node.device.device_library_id === profile.device_library_id));
  }
  function addDevice(profile?: DevicePortProfile) {
    if (locked || (profile && alreadyAdded(profile))) return;
    // 档案快照完整保留设备、端口、模式 ID；新空白器件才生成独立实体 ID。
    const device: DevicePortProfile = profile ? structuredClone(profile) : { id: `device-${crypto.randomUUID()}`, name: "新器件", model: "", device_library_id: null, ports: [] };
    const groups = groupsForDevice(device.device_library_id);
    // 从视口左上起在已测得的外框中找空网格位；无画布测量时退回左上角。
    const slot = canvasRef.current?.findFreeSlot(nodePlacementSize(device)) ?? { x: 60, y: 60 };
    changeGraph({ ...graph, nodes: [...graph.nodes, { device, x: slot.x, y: slot.y,
      ...(supportsFollow ? { follow_mapping: device.device_library_id ? groups.length === 1 ? [...groups[0].keys] : null : [] } : {}) }] });
    setSelectedDevice(device.id); setSelectedEdge(null); setPair(null); setCanvasEpoch(value => value + 1);
    setNotice(supportsFollow ? groups.length === 1 ? "器件已按唯一用途建立跟随绑定；保存后，同用途换机可联动现场和全部目标。仅更新记录，不代替真实改接。" : "器件已加入图内快照；请在右侧确认用途跟随，多用途必须明确选择，独立器件可关闭跟随。" : "器件已加入当前图内快照；原档案与其它图未修改，后续档案变化不会自动替换本图。");
  }
  function setFollowMapping(keys: string[] | null) {
    if (!activeNode || locked) return;
    changeGraph({ ...graph, nodes: graph.nodes.map(node => node.device.id === activeNode.device.id ? { ...node, follow_mapping: keys } : node) });
  }
  function setPortDirection(deviceId: string, portId: string, direction: "in" | "out") {
    if (locked) return;
    const node = graph.nodes.find(item => item.device.id === deviceId);
    if (node?.device.ports.find(port => port.id === portId)?.direction !== "inout") return;
    changeGraph({ ...graph, nodes: graph.nodes.map(item => item === node ? { ...item, port_directions: { ...item.port_directions, [portId]: direction } } : item) });
    setPair(null); setCanvasEpoch(value => value + 1);
  }
  function setPortUsage(deviceId: string, portId: string, usage: PortUsage) {
    if (locked || !graph.nodes.some(node => node.device.id === deviceId && node.device.ports.some(port => port.id === portId))) return;
    if (usage === "unused" && graph.edges.some(edge => attached(edge, deviceId, portId))) {
      setPairError("此端口已有连线，不能标为未用；请先明确删除相关连线，端口与连线不会自动隐藏或删除。"); return;
    }
    changeGraph({ ...graph, nodes: graph.nodes.map(node => node.device.id === deviceId ? { ...node, port_usage: { ...node.port_usage, [portId]: usage } } : node) });
    setPair(null); setPairError(""); setCanvasEpoch(value => value + 1);
  }
  function setPortMode(deviceId: string, portId: string, modeId: string | null) {
    if (locked) return;
    const node = graph.nodes.find(item => item.device.id === deviceId);
    const port = node?.device.ports.find(item => item.id === portId);
    if (!node || port?.direction !== "switchable" || (modeId !== null && !port.modes.some(mode => mode.id === modeId && (mode.direction === "in" || mode.direction === "out")))) return;
    const directionOf = (id: string | null) => port.modes.find(mode => mode.id === id)?.direction ?? null;
    const nextMode = port.modes.find(mode => mode.id === modeId);
    // 已连线的端口换到另一方向的模式会改变线路语义，仅改当前图记录。
    if (directionOf(modeId) !== directionOf(port.selected_mode) && graph.edges.some(edge => attached(edge, deviceId, portId))
      && !window.confirm(`端口“${portLabel(node.device.name, port)}”已有连线，改为${nextMode ? `记录模式“${nextMode.name}”` : "未记录模式"}会改变方向。仅修改当前图记录，不操作硬件；会改变线路语义和比较结果。继续？`)) return;
    changeGraph({ ...graph, nodes: graph.nodes.map(item => item === node ? { ...item, device: { ...item.device, ports: item.device.ports.map(value => value.id === portId ? { ...value, selected_mode: modeId } : value) } } : item) });
    setPair(null); setCanvasEpoch(value => value + 1);
  }
  function updateDevice(change: Partial<DevicePortProfile>, mergeKey: string | null = null) {
    if (!active || locked) return;
    changeGraph({ ...graph, nodes: graph.nodes.map(node => node.device.id === active.id ? { ...node, device: { ...node.device, ...change } } : node) }, mergeKey);
  }
  function setSettingChange(deviceId: string, settingId: string, value: string | null) {
    if (locked) return;
    const node = graph.nodes.find(item => item.device.id === deviceId);
    const setting = node?.settings?.find(item => item.id === settingId);
    if (!setting || value !== null && !setting.options.some(option => option.value === value)) return;
    changeGraph({ ...graph, nodes: graph.nodes.map(item => item === node ? { ...item, settings: item.settings?.map(entry => entry === setting ? { ...entry, value } : entry) } : item) });
  }
  function updateInternalPort(id: string, change: Partial<Pick<InternalPort, "name" | "direction">>, mergeKey: string | null = null) {
    if (!activeNode || locked) return;
    if (change.direction && graph.edges.some(edge => attached(edge, activeNode.device.id, id))) return;
    changeGraph({ ...graph, nodes: graph.nodes.map(node => node === activeNode ? { ...node, internal_ports: node.internal_ports?.map(port => port.id === id ? { ...port, ...change } : port) } : node) }, mergeKey);
    setPair(null); setCanvasEpoch(value => value + 1);
  }
  function removeInternalPort(port: InternalPort) {
    if (!activeNode || locked) return;
    const count = graph.edges.filter(edge => attached(edge, activeNode.device.id, port.id)).length;
    if (!window.confirm(`仅从当前图删除内部端口“${activeNode.device.name} / ${port.name}”及 ${count} 条内部路由？物理端口、其它图和硬件不变。`)) return;
    changeGraph({ ...graph, nodes: graph.nodes.map(node => node === activeNode ? { ...node, internal_ports: node.internal_ports?.filter(item => item.id !== port.id) } : node), edges: graph.edges.filter(edge => !attached(edge, activeNode.device.id, port.id)) });
    setSelectedEdge(null); setPair(null); setCanvasEpoch(value => value + 1);
  }
  function updateSetting(id: string, change: Partial<Omit<WiringSetting, "id">>, mergeKey: string | null = null) {
    if (!activeNode || locked) return;
    changeGraph({ ...graph, nodes: graph.nodes.map(node => node === activeNode ? { ...node, settings: node.settings?.map(setting => setting.id === id ? { ...setting, ...change } : setting) } : node) }, mergeKey);
  }
  function updatePort(id: string, change: Partial<PhysicalPort>, mergeKey: string | null = null) {
    if (!active || locked) return;
    const port = active.ports.find(item => item.id === id);
    const semantic = port && ((change.direction !== undefined && change.direction !== port.direction)
      || (change.signal_type !== undefined && change.signal_type !== port.signal_type));
    if (port && semantic && graph.edges.some(edge => attached(edge, active.id, id))
      && !window.confirm(`端口“${portLabel(active.name, port)}”已有连线，修改方向或光电类型会改变线路语义。仅修改当前图记录，不操作硬件；会改变线路语义和比较结果。继续？`)) return;
    changeGraph({ ...graph, nodes: graph.nodes.map(node => {
      if (node.device.id !== active.id) return node;
      const ports = active.ports.map(port => port.id === id ? { ...port, ...change } : port);
      const directions = Object.fromEntries(Object.entries(node.port_directions ?? {}).filter(([key]) => ports.some(port => port.id === key && port.direction === "inout")));
      return { ...node, device: { ...node.device, ports }, ...(node.port_directions ? { port_directions: directions } : {}) };
    }) }, mergeKey);
    setPair(null); setCanvasEpoch(value => value + 1);
  }
  function removePort(port: PhysicalPort) {
    if (!active || locked) return;
    const count = graph.edges.filter(edge => attached(edge, active.id, port.id)).length;
    if (!window.confirm(`从当前图内快照删除端口“${active.name} / ${port.group ? `${port.group} / ` : ""}${port.name}”，并删除当前图相关的 ${count} 条连线？原端口档案和其它图不变。`)) return;
    changeGraph({ nodes: graph.nodes.map(node => node.device.id === active.id ? { ...node, device: { ...node.device, ports: node.device.ports.filter(item => item.id !== port.id) }, ...(node.port_directions ? { port_directions: Object.fromEntries(Object.entries(node.port_directions).filter(([id]) => id !== port.id)) } : {}), ...(node.port_usage ? { port_usage: Object.fromEntries(Object.entries(node.port_usage).filter(([id]) => id !== port.id)) } : {}) } : node), edges: graph.edges.filter(edge => !attached(edge, active.id, port.id)) });
    setSelectedEdge(null); setPair(null); setCanvasEpoch(value => value + 1);
  }
  function removeDevice() {
    if (!active || locked || !window.confirm(`仅从当前图删除器件“${active.name}”及其相关连线？原设备端口档案和其它图均不删除。`)) return;
    changeGraph({ nodes: graph.nodes.filter(node => node.device.id !== active.id), edges: graph.edges.filter(edge => !attached(edge, active.id)) });
    resetSelection();
  }
  function pickPair(source: WiringEndpoint, target: WiringEndpoint) {
    if (locked) return;
    setPairError(""); setPair(null);
    const entries = [source, target].map(endpoint => {
      const node = graph.nodes.find(item => item.device.id === endpoint.device_id);
      return { node, physical: node?.device.ports.find(port => port.id === endpoint.port_id), internal: node?.internal_ports?.find(port => port.id === endpoint.port_id) };
    });
    if (entries.some(entry => !entry.node || !entry.physical && !entry.internal)) { setPairError("端口已不存在，请重新选择。"); return; }
    const internal = entries.some(entry => entry.internal);
    if (internal && (entries.some(entry => !entry.internal) || source.device_id !== target.device_id)) { setPairError("内部信号只能连接同一器件的内部端口，不能跨器件或混接物理端口。"); return; }
    if (internal && (entries[0].internal?.direction !== "out" || entries[1].internal?.direction !== "in")) { setPairError("内部信号请从内部输出连接到内部输入，方向不会自动交换。"); return; }
    if (!internal && entries.some((entry, index) => entry.node && portUsage(graph, entry.node, [source, target][index].port_id) === "unused")) {
      setPairError("未用端口不能建立连线；请先点击使用按钮启用此图端口，页面不会自动改变使用状态。"); return;
    }
    if (sameEndpoint(source, target)) { setPairError("不能连接自身同一物理端口；可选同设备的不同端口。"); return; }
    if (graph.edges.some(edge => sameEndpoint(edge.source, source) && sameEndpoint(edge.target, target) || sameEndpoint(edge.source, target) && sameEndpoint(edge.target, source))) {
      setPairError(internal ? "这对内部端口已有连线；请选中原线修改标签，或删除后重连。" : "这对物理端口已有连线（反向也算同一对）；请选中原线修改标签，或删除后重连。"); return;
    }
    setPair({ source, target }); setPairType(internal ? "internal" : ""); setPairLabel(""); setSelectedEdge(null);
  }
  function reconnectEdge(edgeId: string, end: "source" | "target", endpoint: WiringEndpoint) {
    if (locked) return;
    const edge = graph.edges.find(item => item.id === edgeId);
    if (!edge) { setPairError("所选连线已不存在，请重新选择。"); return; }
    // 拖回原端口不构成修改，也不留下过期提示。
    if (sameEndpoint(edge[end], endpoint)) { setPairError(""); return; }
    setPair(null); setPairError("");
    const next: WiringEdge = { ...edge, [end]: endpoint };
    const entries = [next.source, next.target].map(item => {
      const node = graph.nodes.find(value => value.device.id === item.device_id);
      return { node, physical: node?.device.ports.find(port => port.id === item.port_id), internal: node?.internal_ports?.find(port => port.id === item.port_id) };
    });
    if (entries.some(entry => !entry.node || !entry.physical && !entry.internal)) { setPairError("端口已不存在，请重新选择。"); return; }
    if (edge.signal_type === "internal") {
      if (entries.some(entry => !entry.internal) || next.source.device_id !== next.target.device_id) { setPairError("内部信号只能连接同一器件的内部端口，不能跨器件或混接物理端口。"); return; }
      if (entries[0].internal?.direction !== "out" || entries[1].internal?.direction !== "in") { setPairError("内部信号请从内部输出连接到内部输入，方向不会自动交换。"); return; }
    } else if (entries.some(entry => entry.internal)) { setPairError("外部连线不能改接到内部端口；内部信号只能在同一器件内部连接。"); return; }
    else if (entries.some((entry, index) => entry.node && portUsage(graph, entry.node, [next.source, next.target][index].port_id) === "unused")) {
      setPairError("未用端口不能建立连线；请先点击使用按钮启用此图端口，页面不会自动改变使用状态。"); return;
    }
    if (sameEndpoint(next.source, next.target)) { setPairError("不能连接自身同一物理端口；可选同设备的不同端口。"); return; }
    // 改接可能落到已有端口对；排除被编辑的这条线本身，其余规则与新建一致。
    if (graph.edges.some(item => item.id !== edge.id && (sameEndpoint(item.source, next.source) && sameEndpoint(item.target, next.target) || sameEndpoint(item.source, next.target) && sameEndpoint(item.target, next.source)))) {
      setPairError(edge.signal_type === "internal" ? "这对内部端口已有连线；请选中原线修改标签，或删除后重连。" : "这对物理端口已有连线（反向也算同一对）；请选中原线修改标签，或删除后重连。"); return;
    }
    // 只换端点：保留连线 ID、标签与光电类型，不改变路由算法以外的记录。
    changeGraph({ ...graph, edges: graph.edges.map(item => item.id === edge.id ? next : item) });
    setSelectedEdge(edge.id); setCanvasEpoch(value => value + 1);
  }
  function connect() {
    if (locked || !pair || !pairType) return;
    changeGraph({ ...graph, edges: [...graph.edges, { id: `edge-${crypto.randomUUID()}`, ...pair, signal_type: pairType, label: pairLabel }] });
    setPair(null); setCanvasEpoch(value => value + 1);
  }
  function updateEdge(change: Partial<Pick<WiringEdge, "label" | "signal_type" | "connector" | "cable_id" | "note">>, mergeKey: string | null = null) {
    if (locked || !activeEdge || change.signal_type && (activeEdge.signal_type === "internal" ? change.signal_type !== "internal" : change.signal_type === "internal")) return;
    changeGraph({ ...graph, edges: graph.edges.map(edge => edge.id === activeEdge.id ? { ...edge, ...change } : edge) }, mergeKey);
  }
  function createTarget() {
    if (locked || Object.hasOwn(draft.targets, targetId)) return;
    setDraft(current => ({ ...current, targets: { ...current.targets, [targetId]: emptyGraph() } })); clearComparison(); setNotice("已显式新建空目标草稿；没有复制现场连接。");
  }
  function copySite() {
    if (locked || !window.confirm(`将现场图完整复制到“${targetName(targetId)}”目标草稿？这会覆盖该目标的全部器件、位置、模式与连线；不改现场、其它目标、原档案或硬件。`)) return;
    setDraft(current => ({ ...current, targets: { ...current.targets, [targetId]: structuredClone(current.site) } }));
    clearComparison(); resetSelection(); setNotice("已复制现场到当前实验目标草稿，尚未保存；没有自动改接或下发设置。");
  }
  function discard() {
    if (locked || !saved || !dirty || !window.confirm(experimentId ? "放弃当前实验目标的未保存修改，恢复到上次读取或成功保存的目标接线图？" : "放弃全部现场和实验目标的未保存修改，恢复到上次读取或成功保存的接线图？")) return;
    setDraft(structuredClone({ site: saved.site, targets: saved.targets })); resetSelection(); clearComparison(); history.clear(); setSaveError(""); setNotice(experimentId ? "已放弃当前实验目标草稿；没有重新读取服务端。" : "已放弃全部接线草稿；没有重新读取服务端，若需获取其他窗口更改请重新载入。");
  }
  function reloadDocument() {
    if (loading || saving || (dirty && !window.confirm(experimentId ? "重新载入将丢弃当前实验目标的未保存修改，读取服务端最新接线图。确定？" : "重新载入将丢弃全部现场和实验目标的未保存修改，读取服务端最新接线图。确定？"))) return;
    clearComparison(); setReload(value => value + 1);
  }
  async function save() {
    if (locked || !saved || !dirty || validation.length) return;
    if (loadError) { setSaveError("重新载入尚未成功，已暂停保存；现有草稿保留，请重新载入成功后再保存。"); return; }
    backgroundRead.current?.abort(); backgroundRead.current = null; setCheckingUpdate(false);
    setSaving(true); setSaveError(""); setNotice(""); setPair(null); setCanvasEpoch(value => value + 1);
    // 现有接口以整份文档做修订检查；内嵌编辑明确保留已读取的现场和其他实验，仅替换当前目标。
    const body: components["schemas"]["WiringBody"] = experimentId
      ? { base_revision: saved.revision, site: saved.site, targets: { ...saved.targets, [experimentId]: draft.targets[experimentId] } }
      : { base_revision: saved.revision, ...draft };
    try {
      const document = checkedDocument(await api<WiringResponse>("/api/wiring", { method: "PUT", body: JSON.stringify(body) }));
      setSaved(document); setDraft(structuredClone({ site: document.site, targets: document.targets })); setConflict(false); setRemoteUpdate(null); setUpdateError(""); clearComparison(); history.clear();
      setNotice(experimentId ? "当前实验目标接线图已保存；现场和其他实验目标保持不变，未操作硬件。" : "全部现场和实验目标接线图已保存；未连接硬件、未改软件映射或原设备端口档案。");
    } catch (error) {
      setConflict((error as ApiError).code === "revision_conflict" || /409/.test(message(error))); setSaveError(message(error));
    } finally { setSaving(false); }
  }
  async function compare() {
    if (locked || comparing || !Object.hasOwn(draft.targets, targetId)) return;
    const sequence = ++compareSequence.current;
    setComparing(true); setCompareError(""); setComparison(null);
    const body: components["schemas"]["WiringCompareBody"] = { site: draft.site, target: targetGraph };
    try {
      const result = await api<WiringComparison>("/api/wiring/compare", { method: "POST", body: JSON.stringify(body) });
      if (sequence === compareSequence.current) setComparison(result);
    } catch (error) { if (sequence === compareSequence.current) setCompareError(message(error)); }
    finally { if (sequence === compareSequence.current) setComparing(false); }
  }
  function warnings(items: WiringWarning[], value: WiringGraph) {
    return items.length ? <ul>{items.map((warning, index) => {
      const edge = value.edges.find(item => item.id === warning.edge_id);
      return <li key={`${warning.edge_id}-${warning.code}-${index}`}>{edge ? `${endpointName(value, edge.source)} ↔ ${endpointName(value, edge.target)}：` : "连线提示："}{warning.message}</li>;
    })}</ul> : <p>无服务端提示；这不代表连接正确或安全。</p>;
  }
  function edgeList(title: string, edges: WiringEdge[], value: WiringGraph) {
    return <section><h3>{title} <small>{edges.length}</small></h3>{edges.length ? <ul>{edges.map(edge => { const cable = cableSummary(edge); return <li key={edge.id}>{endpointName(value, edge.source)} {edge.signal_type === "internal" ? "→" : "↔"} {endpointName(value, edge.target)} · {signalName(edge.signal_type)}{edge.label ? ` · ${edge.label}` : ""}{cable ? ` · 线缆 ${cable}` : ""}</li>; })}</ul> : <p>无</p>}</section>;
  }

  return <div className="wiring-page">
    {experimentId ? <header><h2>目标接线图</h2><p>当前实验：{targetName(experimentId)}。</p><div className="wiring-head-links"><Link to="/instruments/wiring">全局现场与目标接线图</Link><Link to="/instruments/ports">设备端口建档</Link></div></header>
      : <PageHead eyebrow="WIRING EDITOR" title="实验接线图" description="手工维护共享现场图和各实验独立目标；绑定用途可跟随已保存的设备选择，独立快照保持原样。"
        action={<div className="wiring-head-links"><Link to="/instruments/ports">设备端口建档</Link><Link to="/instruments">返回仪器控制</Link></div>} />}
    <div className="wiring-safety-note" role="note"><strong>仅人工接线记录，绝非硬件实际状态或安全判定。</strong>不连接硬件、不修改软件映射、不启停实验；方向 / 光电冲突只由服务端提示，不阻止记录、不构成实验预检。不会推断或自动画出 DirectAW 装置真实连接。</div>
    <div className="wiring-toolbar"><div><strong>{saved === null ? "接线图尚未载入" : dirty ? experimentId ? "当前目标有未保存修改" : "全部图有未保存修改" : experimentId ? "当前目标无未保存修改" : "全部接线图已保存"}</strong><span>{experimentId ? "保存 / 放弃仅作用于当前实验目标；不更改现场或其他实验。" : "保存 / 放弃均作用于现场及全部实验目标"}{saved ? ` · 修订 ${saved.revision}` : ""}</span></div>
      <div><button className="secondary" onClick={undoGraph} disabled={locked || !history.canUndo} title="撤销上一步图内修改（Ctrl+Z）">撤销</button><button className="secondary" onClick={redoGraph} disabled={locked || !history.canRedo} title="重做上一步图内修改（Ctrl+Shift+Z）">重做</button><button onClick={() => void save()} disabled={locked || !!loadError || !dirty || validation.length > 0}>{saving ? experimentId ? "正在保存当前目标…" : "正在保存全部图…" : experimentId ? "保存当前实验目标" : "保存全部接线图"}</button><button className="secondary" onClick={discard} disabled={locked || !dirty}>{experimentId ? "放弃当前目标草稿" : "放弃全部草稿"}</button><button className="secondary" onClick={reloadDocument} disabled={loading || saving}>重新载入</button></div></div>
    {loading && <p className="alert" role="status">正在读取接线图…</p>}
    {loadError && <div className="alert error" role="alert">{saved ? "重新载入失败，现有全部草稿保留；可继续查看和编辑草稿，但保存已暂停，需重新载入成功后才能保存：" : "接线图读取失败，已禁止编辑和空图保存；尚无可用草稿，需重新载入成功后才能继续："}{loadError}<span>新接口若尚未加载，需操作者在任务安全结束后重启现有后端；页面不启停服务。</span><button className="secondary" disabled={loading || saving} onClick={reloadDocument}>重试读取接线图</button></div>}
    {saveError && <div className="alert error" role="alert">{conflict ? experimentId ? "保存冲突（409），当前目标草稿与基础修订已保留；不会覆盖其他窗口更改。" : "保存冲突（409），全部草稿与基础修订已保留；请确认后重新载入，或先保留本页草稿核对。" : experimentId ? "保存失败，当前目标草稿与基础修订已保留，可重试。" : "保存失败，全部草稿与基础修订已保留，可重试。"}<span>{saveError}</span>{conflict && <button className="secondary" onClick={reloadDocument} disabled={loading || saving}>确认重新载入最新接线图</button>}</div>}
    {supportsFollow && <div className="wiring-follow-tools"><button className="secondary" onClick={() => void checkUpdates()} disabled={loading || saving || checkingUpdate}>{checkingUpdate ? "正在检查用途更新…" : "检查用途与接线图更新"}</button><small>重新聚焦页面 / 回到目标 Tab 时检查；无草稿自动刷新，有草稿只提示。</small></div>}
    {remoteUpdate && <div className="alert" role="alert"><strong>设备用途或接线图已有更新，当前草稿与基础修订已保留，未自动覆盖。</strong><span>请先核对草稿；按旧修订保存可能返回 409。确认放弃草稿后重新载入，才能采用最新的现场及全部目标记录。</span><button className="secondary" onClick={reloadDocument} disabled={loading || saving}>确认重新载入用途更新</button></div>}
    {updateError && <p className="wiring-warning" role="alert">用途更新检查失败，现有草稿和基础修订保留：{updateError}</p>}
    {notice && <p className="alert success" role="status">{notice}</p>}
    {validation.length > 0 && <div className="alert error" role="alert"><strong>{experimentId ? "请补全名称后保存当前目标：" : "请补全名称后保存全部图："}</strong><ul>{validation.map((problem, index) => <li key={index}>{problem}</li>)}</ul></div>}
    <fieldset className="wiring-graph-toolbar" disabled={locked} aria-label="选择接线图">
      {!experimentId && <><div className="wiring-view-buttons"><button className={view === "site" ? "active" : "secondary"} aria-pressed={view === "site"} onClick={() => switchView("site")}>共享现场图</button><button className={view === "target" ? "active" : "secondary"} aria-pressed={view === "target"} onClick={() => switchView("target")}>实验目标图</button></div>
      <label><span>目标实验</span><select value={targetId} onChange={event => chooseTarget(event.target.value)}>{targetOptions.map(option => <option key={option.id} value={option.id}>{option.name}</option>)}</select></label></>}
      <div className="wiring-target-actions"><button className="secondary" onClick={createTarget} disabled={Object.hasOwn(draft.targets, targetId)}>显式新建空目标</button><button className="secondary" onClick={copySite}>复制现场到当前实验目标草稿</button></div>
      {!experimentId && experimentsLoading && <small role="status">正在读取实验列表…</small>}{!experimentId && experimentsError && <p className="wiring-warning" role="alert">实验列表读取失败：{experimentsError}。现场仍可维护，已有未知 / 退役目标保留，不会丢弃。</p>}
      <p className="wiring-help">当前：{view === "site" ? "共享现场图" : `实验目标 · ${targetName(targetId)}`} · {graph.nodes.length} 个器件 / {graph.edges.length} 条连线。{!Object.hasOwn(draft.targets, targetId) && "此目标尚未建立。"}</p>
    </fieldset>
    <WiringChecklist active={visible} graph={graph} viewName={view === "site" ? "共享现场图" : `实验目标 · ${targetName(targetId)}`}
      revision={saved?.revision ?? ""} dirty={dirty} disabled={saved === null} />
    {supportsFollow && followStatuses.length > 0 && <section className="wiring-follow-status" aria-label="当前图用途跟随状态"><h3>当前图用途跟随</h3><ul>{followStatuses.map(status => <li className={status.state === "pending" || status.state === "blocked" ? "wiring-warning" : ""} key={status.device_id}><strong>{graph.nodes.find(node => node.device.id === status.device_id)?.device.name ?? status.device_id}</strong>：{status.message}{status.mapping_keys.length > 0 ? `（${status.mapping_keys.join(" / ")}）` : ""}</li>)}</ul>{dirty && <small>这是最近读取的用途状态，当前草稿尚未保存；调整用途后请保存或检查更新核对。</small>}</section>}
    <div className="wiring-workspace">
      <fieldset className="wiring-panel wiring-library" disabled={locked} aria-label="加入器件">
        <h2>加入器件</h2><p className="wiring-help">从已保存档案建立器件，保留图内稳定 ID。一图同一设备仅加入一次；可按唯一用途自动绑定，多用途需明确选择，或保留独立快照。</p>
        {profilesLoading && <p role="status">正在读取已保存端口档案…</p>}
        {profilesError && <p className="wiring-warning" role="alert">端口档案读取失败：{profilesError}。仍可维护图内快照或添加空白器件，不会以空档覆盖。</p>}
        <div className="wiring-profile-list">{profiles.map(device => <button className="secondary" key={device.id} disabled={profilesLoading || !!profilesError || alreadyAdded(device)} onClick={() => addDevice(device)}><strong>{device.name}</strong><small>{device.model || "型号待确认"} · {device.ports.length} 个端口{alreadyAdded(device) ? " · 当前图已加入" : ""}</small><span>加入档案快照</span></button>)}</div>
        {!profilesLoading && !profilesError && !profiles.length && <p>还没有已保存的设备端口档案。</p>}
        <button className="secondary" onClick={() => addDevice()}>添加空白图内器件</button><p className="wiring-help">可手工建立分路器 / 光学器件；名称、型号及物理端口均需人工确认，不从图片或型号推断。</p>
        <Link to="/instruments/ports">维护原端口档案与模式定义 →</Link>
      </fieldset>
      <div className="wiring-canvas-column"><h2>{view === "site" ? "共享现场图" : "实验目标图"} <small>图内草稿</small></h2>
        {(experimentId || (!experimentId && view === "target")) && <section className="wiring-panel wiring-target-requirements" aria-label="实验接线要点与设备要求">
          <h3>人工接线要点与设备要求 <small>存在性提示</small></h3>
          <p className="wiring-help">来自该实验定义，只提示要点与型号 / 用途是否存在；不推断或自动添加连线、不自动添加器件，不代表安全预检，不连接硬件。</p>
          {requirementsLoading && <p role="status">正在读取实验接线要点…</p>}
          {requirementsError && <p className="wiring-warning" role="alert">实验接线要点读取失败，不影响当前目标编辑与保存：{requirementsError}</p>}
          {requirementInfo && <>
            <h4>接线要点</h4>
            {requirementInfo.wiring_notes.length ? <ul>{requirementInfo.wiring_notes.map((note, index) => <li key={index}>{note}</li>)}</ul> : <p>该实验未提供人工接线要点。</p>}
            <h4>设备要求</h4>
            {requirementInfo.required_devices.length ? <ul>{requirementInfo.required_devices.map((device, index) => {
              const present = requiredDevicePresent(targetGraph, device);
              return <li key={`${device}-${index}`} className={present ? "" : "wiring-warning"}><span>{device}</span>{present ? <small>图中已有此型号器件（不代表连接正确）。</small> : <small>图中未找到此型号器件；请人工核对，页面不添加器件。</small>}</li>;
            })}</ul> : <p>该实验未声明型号要求。</p>}
            <h4>用途要求</h4>
            {!requirementInfo.required_mapping_keys.length ? <p>该实验未声明用途要求。</p> : saved?.mapping_groups === undefined ? <p className="wiring-warning">当前接线图未提供用途组信息，无法确认绑定；请人工核对。</p>
              : <ul>{requirementInfo.required_mapping_keys.map(key => {
                const group = mappingGroups.find(item => item.keys.includes(key));
                const bound = targetGraph.nodes.some(node => (node.follow_mapping ?? []).includes(key));
                return <li key={key} className={bound ? "" : "wiring-warning"}><span>{key}</span>{group ? <span> · {group.label}</span> : <span> · 当前设备映射中未找到此用途组</span>}{bound ? <small>图中已有器件绑定此用途（不代表连接正确）。</small> : <small>图中无器件绑定此用途组；请人工核对，页面不自动绑定或添加器件。</small>}</li>;
              })}</ul>}
          </>}
          {requirementsUnavailable && <p className="wiring-help">该目标未在当前实验列表，未核对型号/用途要求。</p>}
        </section>}
        <section className="wiring-panel wiring-preflight" aria-label="接线预检清单（只读汇总）">
          <h3>接线预检清单（只读汇总） <small>不自动修复</small></h3>
          <p className="wiring-help">汇总当前可自动识别的记录提示，仅用于人工核对；不自动修复、不改变保存与运行阻塞规则，也不代表硬件安全校验。</p>
          <div className="wiring-preflight-counts"><span className="wiring-preflight-count severity-blocking">阻断 {preflightBlocking} 项</span><span className="wiring-preflight-count severity-warning">警告 {preflightWarnings} 项</span>{preflightInfo > 0 && <span className="wiring-preflight-count severity-info">提示 {preflightInfo} 项</span>}</div>
          {preflight.length ? <ul className="wiring-preflight-list">{preflight.map(item => <li key={item.id} className={`wiring-preflight-item severity-${item.severity}`}>
            <span className="wiring-preflight-severity">{preflightSeverityLabel[item.severity]}</span>
            <span className="wiring-preflight-source">{preflightSourceLabel[item.source]}</span>
            <span className="wiring-preflight-message">{item.message}</span>
            {item.focus && <button className="secondary wiring-preflight-locate" aria-label={`定位：${item.message}`} onClick={() => locatePreflight(item)}>定位</button>}
          </li>)}</ul> : <p className="wiring-preflight-empty">{wiringPreflightEmptyText}</p>}
          {dirty && <p className="wiring-help">草稿有未保存修改：跟随与诊断条目基于最近读取 / 保存，尚未反映当前草稿。</p>}
        </section>
        <WiringCanvas ref={canvasRef} key={view === "site" ? "site" : `target-${targetId}`} graph={graph} locked={locked} active={visible} resetToken={canvasEpoch} selectedDevice={selectedDevice} selectedEdge={selectedEdge} profileBadges={profileBadges} onBlockedRoutesChange={setBlockedEdgeIds}
          autoFit={saved !== null && !autoFitted.current.has(graphKey)} onAutoFitSettled={() => autoFitted.current.add(graphKey)}
          onPortDirection={setPortDirection} onPortMode={setPortMode} onPortUsage={setPortUsage} onSettingChange={setSettingChange}
          onSelectDevice={id => { if (!locked) { setSelectedDevice(id); setSelectedEdge(null); setSyncPreview(null); } }} onSelectEdge={id => { if (!locked) { setSelectedEdge(id); setPair(null); setSyncPreview(null); } }}
          onMove={(id, x, y) => changeGraph({ ...graph, nodes: graph.nodes.map(node => node.device.id === id ? { ...node, x, y } : node) })}
          onPair={pickPair} onReconnect={reconnectEdge} onCancel={() => { setPair(null); setPairError(""); }} endpointLabel={endpoint => endpointName(graph, endpoint)} />
        {pairError && <p className="alert error" role="alert">{pairError}</p>}
        {pair && <fieldset className="wiring-panel wiring-pair" disabled={locked} aria-label="待建立连线"><h3>确认新连线</h3><p>{endpointName(graph, pair.source)} ↔ {endpointName(graph, pair.target)}</p>
          {pairType === "internal" ? <p>内部信号（非线缆）：仅同一器件内部输出 → 内部输入，不代表外部插拔连接。</p> : <label><span>新连线光电类型</span><select value={pairType} onChange={event => { if (!locked) setPairType(event.target.value as "" | "optical" | "electrical"); }}><option value="">请明确选择，不自动推断</option><option value="optical">光学</option><option value="electrical">电学</option></select></label>}
          <label><span>新连线标签</span><input value={pairLabel} onChange={event => { if (!locked) setPairLabel(event.target.value); }} /></label><button disabled={!pairType} onClick={connect}>记录连线</button><button className="secondary" onClick={() => { setPair(null); setCanvasEpoch(value => value + 1); }}>取消新连线</button></fieldset>}
        <fieldset className="wiring-panel wiring-edge-editor" disabled={locked} aria-label="连线列表与编辑"><h3>当前图连线 <small>{graph.edges.length}</small></h3><p className="wiring-help">可点画布线或下列记录选中；选中后拖动两端手柄改接端点，标签与光电类型保持不变。删除不影响其它图。</p>
          <div className="wiring-edge-list">{graph.edges.map(edge => <button key={edge.id} className={edge.id === selectedEdge ? "active" : "secondary"} onClick={() => { if (!locked) { setSelectedEdge(edge.id); setPair(null); } }} aria-pressed={edge.id === selectedEdge} aria-label={`选择连线记录：${endpointName(graph, edge.source)} ↔ ${endpointName(graph, edge.target)}`}><span>{endpointName(graph, edge.source)} ↔ {endpointName(graph, edge.target)}</span><small>{signalName(edge.signal_type)}{edge.label ? ` · ${edge.label}` : ""}{cableSummary(edge) ? ` · 线缆 ${cableSummary(edge)}` : ""}</small></button>)}</div>
          {activeEdge && <div className="wiring-fields"><label><span>连线标签</span><input value={activeEdge.label} onChange={event => updateEdge({ label: event.target.value }, `edge:${activeEdge.id}:label`)} /></label>{activeEdge.signal_type === "internal" ? <p>内部信号（非线缆），不能改成外部光／电线；改变路由可拖动端点手柄改接。</p> : <><label><span>连线光电类型</span><select value={activeEdge.signal_type} onChange={event => updateEdge({ signal_type: event.target.value as "optical" | "electrical" })}><option value="optical">光学</option><option value="electrical">电学</option></select></label>
            <label><span>线缆接头形式</span><input value={activeEdge.connector ?? ""} placeholder="如 BNC / SMA / 自由空间" onChange={event => updateEdge({ connector: cableInput(event.target.value) }, `edge:${activeEdge.id}:connector`)} /></label>
            <label><span>线缆编号</span><input value={activeEdge.cable_id ?? ""} placeholder="如 C-01" onChange={event => updateEdge({ cable_id: cableInput(event.target.value) }, `edge:${activeEdge.id}:cable_id`)} /></label>
            <label><span>线缆备注</span><input value={activeEdge.note ?? ""} onChange={event => updateEdge({ note: cableInput(event.target.value) }, `edge:${activeEdge.id}:note`)} /></label>
            <p className="wiring-help">线缆记录仅用于人工核对；比较时按端点对匹配，接头 / 编号 / 备注差异单列，不改变连线身份。</p></>}<button className="secondary wiring-danger" onClick={() => { if (locked) return; changeGraph({ ...graph, edges: graph.edges.filter(edge => edge.id !== activeEdge.id) }); setSelectedEdge(null); }}>删除所选连线</button></div>}
          {!graph.edges.length && <p>暂无连线；页面不会代建真实装置连接。</p>}
        </fieldset>
      </div>
      <fieldset className="wiring-panel wiring-device-editor" disabled={locked} aria-label="器件快照编辑"><h2>图内器件快照</h2>
        {active ? <><p className="wiring-help">修改只影响当前图，不写原档案、不改变其它图或硬件。选模式是人工记录；新增 / 修改模式定义请回端口建档页。</p>
          <label><span>图内器件名称</span><input value={active.name} onChange={event => updateDevice({ name: event.target.value }, `device:${active.id}:name`)} /></label><label><span>图内器件型号</span><input value={active.model} onChange={event => updateDevice({ model: event.target.value }, `device:${active.id}:model`)} /></label>
          <div className="wiring-profile-sync" aria-label="设备端口档案差异与同步">
            <div className="wiring-section-head"><h3>设备端口档案</h3>{activeProfile && <button className="secondary" onClick={previewSync} disabled={locked}>从档案同步本器件</button>}</div>
            {activeBadges.length > 0 && <div className="wiring-profile-badges">{activeBadges.map(badge => <span key={badge} className={`wiring-profile-badge ${badge}`}>{wiringSyncBadgeLabel[badge]}</span>)}</div>}
            {activeProfile ? <p className="wiring-help">{activeBadges.length > 0 ? "图内快照与同库档案存在差异；同步只改当前图草稿，不写档案、不自动保存。" : "当前图快照与档案一致；重新同步仍只更新当前图草稿。"}</p>
              : active.device_library_id && !profilesLoading && !profilesError ? <p className="wiring-help">同库档案不存在，无法同步；可继续维护独立图内快照。</p>
                : profilesLoading ? <p className="wiring-help">正在读取设备端口档案…</p>
                  : profilesError ? <p className="wiring-help">档案读取失败，暂不能比较或同步；图内快照仍可编辑。</p> : null}
            {syncPreview && <div className="wiring-sync-preview" role="region" aria-label="从档案同步差异摘要">
              {syncPreview.ok ? <>
                <h4>差异摘要</h4>
                <ul>{wiringSyncSummaryLines(syncPreview.summary).map((line, index) => <li key={index}>{line}</li>)}</ul>
                <p className="wiring-help">保留器件 ID、连线、用途跟随、工作方向、使用状态、图内设置与坐标；新增端口默认未用 / 待确认。仅改当前图草稿，不写档案、不自动保存。</p>
                <div className="wiring-target-actions"><button onClick={applySync} disabled={locked || !syncPreview.changed}>{syncPreview.changed ? "确认同步到当前图草稿" : "没有可同步的差异"}</button><button className="secondary" onClick={() => setSyncPreview(null)}>取消同步</button></div>
              </> : <>
                <p className="alert error" role="alert">无法从档案同步：{syncPreview.reason}</p>
                <button className="secondary" onClick={() => setSyncPreview(null)}>关闭差异摘要</button>
              </>}
            </div>}
          </div>
          {supportsFollow && activeNode && <><label><span>用途跟随（当前图器件）</span><select value={followValue} onChange={event => {
            const value = event.target.value;
            if (value === "auto") setFollowMapping(null);
            else if (value === "independent") setFollowMapping([]);
            else { const chosen = activeGroups.find(group => mappingKeysToken(group.keys) === value); if (chosen) setFollowMapping([...chosen.keys]); }
          }}>
            {!manualDevice && <option value="auto">按当前设备自动识别（多用途需选择）</option>}
            <option value="independent">独立快照，不跟随换机</option>
            {hasBoundKeys && !boundGroup ? <option value={mappingKeysToken(boundKeys)} disabled>已绑定用途（{foreignGroup ? "用途组属于其他设备" : "当前配置中未找到"}，请改选或设为独立）：{boundKeys.join(" / ")}</option> : null}
            {activeGroups.map(group => <option key={mappingKeysToken(group.keys)} value={mappingKeysToken(group.keys)}>{group.label}</option>)}
          </select></label><p className="wiring-help">{manualDevice ? "手工节点没有设备库身份，只能保留独立快照；不参与设备库用途跟随换机。" : "仅随已保存的用途映射换机。自动识别为多用途时不会猜；确认具体用途并保存，可让旧图后续稳定跟随。若端口不能完整对应，整张图保留原样并提示未同步。"}</p></>}
          <button className="secondary wiring-danger" onClick={removeDevice}>删除当前图器件及相关连线</button>
          <div className="wiring-section-head"><h3>物理端口</h3><button className="secondary" onClick={() => { if (!active || locked) return; updateDevice({ ports: [...active.ports, { id: `port-${crypto.randomUUID()}`, name: `端口 ${active.ports.length + 1}`, group: "", signal_type: null, connector: "", purpose: "", direction: null, modes: [], selected_mode: null }] }); }}>添加图内物理端口</button></div>
          {!active.ports.length && <p>尚无端口，请对照实物手工增加，不自动推断。</p>}
          {active.ports.map((port, index) => <fieldset className="wiring-port-editor" key={port.id} aria-label={`图内端口 ${index + 1}`}><legend>{[port.group, port.name].filter(Boolean).join(" / ") || `端口 ${index + 1}`}</legend>
            <label><span>图内端口名称</span><input value={port.name} onChange={event => updatePort(port.id, { name: event.target.value }, `port:${port.id}:name`)} /></label><label><span>图内端口分组</span><input value={port.group} onChange={event => updatePort(port.id, { group: event.target.value }, `port:${port.id}:group`)} /></label>
            <label><span>图内端口光电类型</span><select value={port.signal_type ?? ""} onChange={event => updatePort(port.id, { signal_type: (event.target.value || null) as PhysicalPort["signal_type"] })}><option value="">待确认</option><option value="optical">光学</option><option value="electrical">电学</option></select></label>
            {activeNode && <div><span>图内端口使用状态</span><button className="wiring-usage-toggle" aria-label={`图内端口使用状态：${active.name} / ${[port.group, port.name].filter(Boolean).join(" / ")}`} aria-pressed={portUsage(graph, activeNode, port.id) === "used"}
              disabled={locked || graph.edges.some(edge => attached(edge, active.id, port.id))}
              onClick={() => setPortUsage(active.id, port.id, portUsage(graph, activeNode, port.id) === "used" ? "unused" : "used")}>{usageName(portUsage(graph, activeNode, port.id))}</button></div>}
            {graph.edges.some(edge => attached(edge, active.id, port.id)) && <small>已有连线，不能标为未用；请先明确删除相关连线。</small>}
            {port.direction === "switchable" ? <label><span>图内记录模式（非硬件状态）</span><select value={port.selected_mode ?? ""} onChange={event => setPortMode(active.id, port.id, event.target.value || null)}><option value="">待确认（未记录）</option>{port.modes.map(mode => <option key={mode.id} value={mode.id} disabled={mode.direction === "inout"}>{mode.name}{mode.direction === "inout" ? "（历史方向待确认）" : ""}</option>)}</select></label>
              : <label><span>图内端口方向</span><select value={port.direction ?? ""} onChange={event => updatePort(port.id, { direction: (event.target.value || null) as PhysicalPort["direction"] })}><option value="">待确认</option><option value="in">输入</option><option value="out">输出</option>{port.direction === "inout" && <option value="inout" disabled>历史双向记录（请确认单向）</option>}</select></label>}
            <button className="secondary wiring-danger" onClick={() => removePort(port)}>删除图内端口及相关连线</button>
          </fieldset>)}
          {activeNode && <><div className="wiring-section-head"><h3>内部端口（非物理口）</h3><button className="secondary" onClick={() => changeGraph({ ...graph, nodes: graph.nodes.map(node => node === activeNode ? { ...node, internal_ports: [...node.internal_ports ?? [], { id: `internal-${crypto.randomUUID()}`, name: `内部端口 ${(node.internal_ports?.length ?? 0) + 1}`, direction: "out" }] } : node) })}>添加图内内部端口</button></div>
            <p className="wiring-help">内部信号仅在同一器件内部连接，先选输出再选输入；不代表 BNC 等物理端口。已连线的方向需删除相关路由后再改。</p>
            {activeNode.internal_ports?.map((port, index) => <fieldset className="wiring-port-editor" key={port.id} aria-label={`图内内部端口 ${index + 1}`}><legend>{port.name}</legend>
              <label><span>内部端口名称</span><input value={port.name} onChange={event => updateInternalPort(port.id, { name: event.target.value }, `internal:${port.id}:name`)} /></label>
              <label><span>内部信号方向</span><select value={port.direction} disabled={graph.edges.some(edge => attached(edge, active.id, port.id))} onChange={event => updateInternalPort(port.id, { direction: event.target.value as InternalPort["direction"] })}><option value="out">内部输出</option><option value="in">内部输入</option></select></label>
              <button className="secondary wiring-danger" onClick={() => removeInternalPort(port)}>删除内部端口及相关路由</button>
            </fieldset>)}
            <div className="wiring-section-head"><h3>器件图内设置</h3><button className="secondary" onClick={() => changeGraph({ ...graph, nodes: graph.nodes.map(node => node === activeNode ? { ...node, settings: [...node.settings ?? [], { id: `setting-${crypto.randomUUID()}`, name: `设置 ${(node.settings?.length ?? 0) + 1}`, value: null, options: [{ value: "1", label: "选项 1" }] }] } : node) })}>添加图内设置</button></div>
            <p className="wiring-help">如 Noise Eater 的高／低档与子档 1–4。这些是图内记录，不增加物理输入，不下发硬件。选项值用于稳定比较，标签用于显示。</p>
            {activeNode.settings?.map((setting, index) => <fieldset className="wiring-port-editor" key={setting.id} aria-label={`器件图内设置 ${index + 1}`}><legend>{setting.name}</legend>
              <label><span>设置名称</span><input value={setting.name} onChange={event => updateSetting(setting.id, { name: event.target.value }, `setting:${setting.id}:name`)} /></label>
              <label><span>当前记录值</span><select value={setting.value ?? ""} onChange={event => setSettingChange(active.id, setting.id, event.target.value || null)}><option value="">未记录</option>{setting.options.map((option, optionIndex) => <option key={optionIndex} value={option.value}>{option.label}</option>)}</select></label>
              {setting.options.map((option, optionIndex) => <div className="wiring-setting-option" key={optionIndex}>
                <label><span>选项值</span><input value={option.value} onChange={event => { const value = event.target.value; updateSetting(setting.id, { options: setting.options.map((item, at) => at === optionIndex ? { ...item, value } : item), ...(setting.value === option.value ? { value: value || null } : {}) }, `setting:${setting.id}:option:${optionIndex}:value`); }} /></label>
                <label><span>选项标签</span><input value={option.label} onChange={event => updateSetting(setting.id, { options: setting.options.map((item, at) => at === optionIndex ? { ...item, label: event.target.value } : item) }, `setting:${setting.id}:option:${optionIndex}:label`)} /></label>
                <button className="secondary" disabled={setting.options.length === 1} onClick={() => { if (setting.value === option.value && !window.confirm("删除当前选中的选项并将当前图记录清为未记录？不改变硬件。")) return; updateSetting(setting.id, { options: setting.options.filter((_, at) => at !== optionIndex), ...(setting.value === option.value ? { value: null } : {}) }); }}>删除此选项</button>
              </div>)}
              <button className="secondary" onClick={() => updateSetting(setting.id, { options: [...setting.options, { value: `option-${crypto.randomUUID()}`, label: `选项 ${setting.options.length + 1}` }] })}>添加设置选项</button>
              <button className="secondary wiring-danger" onClick={() => { if (window.confirm(`仅删除当前图内设置“${setting.name}”？不改变硬件。`)) changeGraph({ ...graph, nodes: graph.nodes.map(node => node === activeNode ? { ...node, settings: node.settings?.filter(item => item.id !== setting.id) } : node) }); }}>删除图内设置</button>
            </fieldset>)}
          </>}
        </> : <p className="wiring-help">点击器件标题选择；拖动标题可吸附网格，标题聚焦后方向键每次移动 20。</p>}
      </fieldset>
    </div>
    <section className="wiring-panel wiring-compare" aria-labelledby={`${prefix}-compare`}><h2 id={`${prefix}-compare`}>现场与目标的草稿比较</h2><p>目标：{targetName(targetId)}。物理连线按端点与光电类型比较；内部信号按同器件的输出 → 输入单独比较，不计入外部改接线。忽略连线标签、ID 与位置，线缆记录（接头 / 编号 / 备注）也不参与匹配而是单列差异，图内设置按稳定设置 ID 的选值单列；只提供人工核对清单，不自动改接或下发硬件。任何后续图修改都会清除过期结果。</p>
      <button onClick={() => void compare()} disabled={locked || comparing || !Object.hasOwn(draft.targets, targetId)}>{comparing ? "正在比较草稿…" : "比较现场与当前目标草稿"}</button>{!Object.hasOwn(draft.targets, targetId) && <p>此目标尚未建立，请显式新建空目标或编辑目标后再比较。</p>}
      {compareError && <p className="alert error" role="alert">草稿比较失败：{compareError}</p>}
      {comparison && <div aria-label="草稿比较结果"><p className="alert">当前完整草稿的比较结果，不代表真实接线或安全状态，未自动更改任何连接。</p><div className="wiring-comparison-columns">{edgeList("保留", comparison.retained, draft.site)}{edgeList("拆除", comparison.removed, draft.site)}{edgeList("新增", comparison.added, targetGraph)}</div>
        <section aria-label="内部信号比较"><h3>内部信号变化（非线缆）</h3><div className="wiring-comparison-columns">{edgeList("内部保留", comparison.internal_retained ?? [], draft.site)}{edgeList("内部移除", comparison.internal_removed ?? [], draft.site)}{edgeList("内部新增", comparison.internal_added ?? [], targetGraph)}</div></section>
        <section aria-label="线缆记录比较"><h3>线缆记录变化 <small>{comparison.cable_changes?.length ?? 0}</small></h3>{comparison.cable_changes?.length ? <ul>{comparison.cable_changes.map(change => {
          const edge = targetGraph.edges.find(item => item.id === change.edge_id);
          const route = edge ? `${endpointName(targetGraph, edge.source)} ↔ ${endpointName(targetGraph, edge.target)}` : change.edge_id;
          const fields: [string, string | null, string | null][] = [["接头", change.site_connector, change.target_connector], ["编号", change.site_cable_id, change.target_cable_id], ["备注", change.site_note, change.target_note]];
          return <li key={change.edge_id}>{route}：{fields.filter(([, site, target]) => site !== target).map(([name, site, target]) => `${name} ${site ?? "未记录"} → ${target ?? "未记录"}`).join("；")}</li>;
        })}</ul> : <p>无线缆记录变化</p>}</section>
        <section aria-label="图内设置比较"><h3>图内设置变化 <small>{comparison.setting_changes?.length ?? 0}</small></h3>{comparison.setting_changes?.length ? <ul>{comparison.setting_changes.map(change => <li key={`${change.device_id}-${change.setting_id}`}>{change.device_name} / {change.setting_name}：{change.site_value ?? "未记录"} → {change.target_value ?? "未记录"}</li>)}</ul> : <p>无图内设置变化</p>}</section>
        <h3>记录模式变化 <small>{comparison.mode_changes.length}</small></h3>{comparison.mode_changes.length ? <ul>{comparison.mode_changes.map((change, index) => <li key={`${change.device_id}-${change.port_id}-${index}`}>{change.device_name} / {change.port_name}：{change.site_mode ?? "待确认"} → {change.target_mode ?? "待确认"}</li>)}</ul> : <p>无模式变化</p>}
        <h3>端口使用状态变化 <small>{comparison.usage_changes?.length ?? 0}</small></h3>{comparison.usage_changes?.length ? <ul>{comparison.usage_changes.map(change => <li key={`${change.device_id}-${change.port_id}`}>{change.device_name} / {change.port_name}：{usageName(change.site_usage)} → {usageName(change.target_usage)}</li>)}</ul> : <p>无使用状态变化</p>}
        <h3>现场草稿提示</h3>{warnings(comparison.site_warnings, draft.site)}<h3>目标草稿提示</h3>{warnings(comparison.target_warnings, targetGraph)}</div>}
    </section>
    <section className="wiring-panel wiring-diagnostics"><h2>最近读取 / 保存的服务端提示</h2>{dirty ? <p>当前已编辑，旧提示已隐藏；请{experimentId ? "保存当前目标" : "保存全部图"}或比较草稿以获取新提示。服务端提示不是安全校验。</p> : saved ? experimentId ? warnings(saved.diagnostics.targets[experimentId] ?? [], saved.targets[experimentId] ?? emptyGraph()) : <><h3>共享现场图</h3>{warnings(saved.diagnostics.site, saved.site)}{Object.entries(saved.diagnostics.targets).map(([id, items]) => <div key={id}><h3>{targetName(id)}</h3>{warnings(items, saved.targets[id] ?? emptyGraph())}</div>)}</> : <p>接线图尚未载入，没有可用提示。</p>}</section>
  </div>;
}
