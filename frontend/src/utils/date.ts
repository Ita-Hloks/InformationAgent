const DAY_IN_MS = 24 * 60 * 60 * 1000;

function parseDate(isoDate: string | null): Date | null {
  if (!isoDate) return null;
  const date = new Date(isoDate);
  return Number.isNaN(date.getTime()) ? null : date;
}

function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function formatTime(date: Date): string {
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${hour}:${minute}`;
}

function formatChineseDate(date: Date, includeYear: boolean): string {
  const year = includeYear ? `${date.getFullYear()}年` : "";
  return `${year}${date.getMonth() + 1}月${date.getDate()}日`;
}

export function isArticleToday(isoDate: string | null, now = new Date()): boolean {
  const date = parseDate(isoDate);
  return date !== null && startOfDay(date).getTime() === startOfDay(now).getTime();
}

export function formatArticleListDate(isoDate: string | null, now = new Date()): string {
  const date = parseDate(isoDate);
  if (!date) return "未标注时间";

  const dayDifference = Math.round(
    (startOfDay(now).getTime() - startOfDay(date).getTime()) / DAY_IN_MS,
  );
  if (dayDifference === 0) return formatTime(date);
  if (dayDifference === 1) return `昨天 ${formatTime(date)}`;
  return formatChineseDate(date, date.getFullYear() !== now.getFullYear());
}

export function formatArticleFullDate(isoDate: string | null): string {
  const date = parseDate(isoDate);
  return date ? `${formatChineseDate(date, true)} ${formatTime(date)}` : "未标注时间";
}
