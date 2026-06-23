/** 将 API 返回的 ISO / 时间字符串格式化为 `YYYY-MM-DD HH:mm:ss`。 */
export function formatDateTime(value?: string | null, fallback = '-'): string {
  if (!value) return fallback;

  const text = value.trim();
  if (!text) return fallback;

  const d = new Date(text.includes('T') ? text : text.replace(' ', 'T'));
  if (Number.isNaN(d.getTime())) return value;

  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
