import type { DeviceConfig, PhysicalMappingsDocument } from "../../types/api";

export function endpointLabel(kind: string, index: number) {
  if (kind === "demod") return `Demod ${index}`;
  if (kind === "laser_channel") return `Laser ${index}`;
  return `CH${index}`;
}

export function deviceOptionLabel(device: DeviceConfig) {
  const serial = device.connection.serial_number || device.connection.device_id
    || (device.resource?.toUpperCase().startsWith("USB") ? device.resource.split("::")[3] : "");
  const address = device.resource || `${device.connection.host || ""}:${device.connection.port || ""}`;
  return `${device.label} · ${device.model} · ${address}${serial ? ` · ${serial}` : ""}`;
}

export function linkedMappingKeys(document: PhysicalMappingsDocument, key: string) {
  const members = new Set([key]);
  const groups = [...Object.values(document.constraints.colocation_groups),
    ...Object.values(document.constraints.shared_channel_groups)];
  let changed = true;
  while (changed) {
    changed = false;
    for (const group of groups) {
      if (!group.some(member => members.has(member))) continue;
      for (const member of group) {
        if (!members.has(member)) { members.add(member); changed = true; }
      }
    }
  }
  return [...members];
}

export function switchMappingDevice(
  document: PhysicalMappingsDocument, devices: Record<string, DeviceConfig>, keys: string[], deviceId: string,
) {
  const device = devices[deviceId];
  const channels = (device?.capabilities.channels as number[] | undefined) || [];
  for (const key of keys) {
    const mapping = document.mapping[key];
    if (!mapping) throw new Error(`组成员“${key}”不是已存在的物理量键`);
    if (!device || device.instrument !== mapping.instrument) {
      throw new Error(`目标设备不支持物理量 ${key} 的设备类型`);
    }
    if (mapping.endpoint && !channels.includes(mapping.endpoint.index)) {
      throw new Error(`设备 ${device.label} 缺少 ${key} 所需的 ${endpointLabel(mapping.endpoint.kind, mapping.endpoint.index)}，整组未切换`);
    }
  }
  const next = structuredClone(document);
  for (const key of keys) next.mapping[key].device_id = deviceId;
  return next;
}
