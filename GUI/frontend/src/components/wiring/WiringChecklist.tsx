import { createPortal } from "react-dom";
import type { components } from "../../types/openapi";
import { type WiringGraph } from "./wiringTypes";
import "./wiring.css";

type WiringNode = components["schemas"]["WiringNode"];
type PhysicalPort = components["schemas"]["PhysicalPort"];
type WiringInternalPort = components["schemas"]["WiringInternalPort"];
type WiringEndpoint = components["schemas"]["WiringEndpoint"];

type ChecklistRow = { key: string; port: string; direction: string; peer: string; signal: string; label: string };

const PLACEHOLDER = "—";
const directionName = (value: string | null | undefined) => value === "in" ? "输入" : value === "out" ? "输出" : value === "inout" ? "双向（方向待确认）" : "方向待确认";
const signalName = (value: string | null | undefined) => value === "internal" ? "内部信号（非线缆）" : value === "optical" ? "光学" : value === "electrical" ? "电学" : "光/电待确认";
const physicalPortName = (port: PhysicalPort) => [port.group, port.name || "未命名端口"].filter(Boolean).join(" / ");
const internalPortName = (port: WiringInternalPort) => port.name || "未命名内部端口";
const deviceName = (node: WiringNode) => node.device.name || "未命名器件";

function endpointLabel(graph: WiringGraph, endpoint: WiringEndpoint) {
  const node = graph.nodes.find(item => item.device.id === endpoint.device_id);
  if (!node) return "未找到器件";
  const physical = node.device.ports.find(port => port.id === endpoint.port_id);
  if (physical) return [deviceName(node), physicalPortName(physical)].join(" / ");
  const internal = node.internal_ports?.find(port => port.id === endpoint.port_id);
  return [deviceName(node), internal ? `${internalPortName(internal)}（内部）` : "未找到端口"].join(" / ");
}

function portDirection(node: WiringNode, port: PhysicalPort) {
  if (port.direction === "switchable") {
    const mode = port.modes.find(item => item.id === port.selected_mode);
    return mode ? `模式：${mode.name}（${directionName(mode.direction)}）` : "模式待确认";
  }
  if (port.direction === "inout") {
    const recorded = node.port_directions?.[port.id];
    return recorded ? `历史双向，已记录${directionName(recorded)}` : "历史双向（方向待确认）";
  }
  return directionName(port.direction);
}

// 未记录连线的端口也列出，便于对照实物确认哪些口应当空置。
function deviceRows(graph: WiringGraph, node: WiringNode): ChecklistRow[] {
  const deviceId = node.device.id;
  const rows: ChecklistRow[] = [];
  const attached = (portId: string) => (edge: WiringGraph["edges"][number]) =>
    edge.source.device_id === deviceId && edge.source.port_id === portId || edge.target.device_id === deviceId && edge.target.port_id === portId;
  for (const port of node.device.ports) {
    const edges = graph.edges.filter(edge => edge.signal_type !== "internal" && attached(port.id)(edge));
    if (!edges.length) {
      rows.push({ key: `port:${port.id}`, port: physicalPortName(port), direction: portDirection(node, port), peer: "未连接", signal: signalName(port.signal_type), label: PLACEHOLDER });
      continue;
    }
    for (const edge of edges) {
      const source = edge.source.device_id === deviceId && edge.source.port_id === port.id;
      rows.push({ key: `edge:${edge.id}:${port.id}`, port: physicalPortName(port), direction: portDirection(node, port), peer: endpointLabel(graph, source ? edge.target : edge.source), signal: signalName(edge.signal_type), label: edge.label || PLACEHOLDER });
    }
  }
  for (const port of node.internal_ports ?? []) {
    const edges = graph.edges.filter(edge => edge.signal_type === "internal" && attached(port.id)(edge));
    for (const edge of edges) {
      const source = edge.source.device_id === deviceId && edge.source.port_id === port.id;
      rows.push({ key: `internal:${edge.id}:${port.id}`, port: `${internalPortName(port)}（内部）`, direction: port.direction === "out" ? "内部输出" : "内部输入", peer: endpointLabel(graph, source ? edge.target : edge.source), signal: signalName(edge.signal_type), label: edge.label || PLACEHOLDER });
    }
  }
  return rows;
}

/** 打印当前视图草稿的人工核对清单；只读内存中的图，不保存、不请求后端、不访问硬件。 */
export function WiringChecklist({ graph, viewName, revision, dirty = false, active = true, disabled = false }: {
  graph: WiringGraph;
  viewName: string;
  revision: string;
  dirty?: boolean;
  active?: boolean;
  disabled?: boolean;
}) {
  const revisionText = revision || "尚未载入";
  const printContent = <article className="wiring-checklist-print" aria-label="人工接线核对清单">
    <header>
      <h1>人工接线核对清单</h1>
      <p className="wiring-checklist-meta">{viewName} · 修订号 {revisionText}{dirty ? " · 含未保存草稿修改" : ""}</p>
      <p className="wiring-checklist-warning">人工记录，非硬件状态或安全判定。仅导出当前视图草稿，不保存、不请求后端、不连接硬件。</p>
      <p>共 {graph.nodes.length} 个器件，{graph.edges.length} 条连线。</p>
    </header>
    {graph.nodes.length === 0 ? <p>此图尚无器件，没有可核对的端口记录。</p> : graph.nodes.map(node => {
      const rows = deviceRows(graph, node);
      return <section className="wiring-checklist-device" key={node.device.id}>
        <h2>{deviceName(node)}{node.device.model ? `（${node.device.model}）` : ""}</h2>
        {rows.length ? <table>
          <thead><tr><th>端口</th><th>方向 / 模式</th><th>对端</th><th>光 / 电类型</th><th>连线标签</th></tr></thead>
          <tbody>{rows.map(row => <tr key={row.key}><td>{row.port}</td><td>{row.direction}</td><td>{row.peer}</td><td>{row.signal}</td><td>{row.label}</td></tr>)}</tbody>
        </table> : <p>此器件尚无端口记录。</p>}
      </section>;
    })}
  </article>;
  return <>
    <section className="wiring-checklist" aria-label="导出人工接线核对清单">
      <div>
        <h2>人工接线核对清单</h2>
        <p>当前视图：{viewName} · 修订号 {revisionText}。按器件列出端口、方向 / 模式、对端、光 / 电类型与连线标签；只读取当前草稿，不保存、不请求后端、不访问硬件。</p>
      </div>
      <button className="secondary" onClick={() => window.print()} disabled={disabled}>导出/打印核对清单</button>
    </section>
    {active && createPortal(<div className="wiring-print-root">{printContent}</div>, document.body)}
  </>;
}
