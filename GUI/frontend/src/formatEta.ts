/** 把剩余秒数格式化为 MM:SS，超过一小时时使用 H:MM:SS。 */
export function formatEta(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "";
  const total = Math.round(seconds);
  const [hours, minute, second] = [
    Math.floor(total / 3600),
    Math.floor((total % 3600) / 60),
    total % 60,
  ];
  const mmss = `${String(minute).padStart(2, "0")}:${String(second).padStart(2, "0")}`;
  return hours > 0 ? `${hours}:${mmss}` : mmss;
}
