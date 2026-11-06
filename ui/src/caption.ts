/** Must match build_caption() in core/video_publisher/platforms/telegram_bot.py. */
export const TELEGRAM_CAPTION_LIMIT = 1024;

export function parseHashtags(raw: string): string[] {
  const seen: string[] = [];
  for (const part of raw.split(/[\s,]+/)) {
    const tag = part.replace(/^#+/, "").trim();
    if (tag && !seen.includes(tag)) seen.push(tag);
  }
  return seen;
}

export function buildCaption(title: string, description: string, hashtags: string[]): string {
  const tags = hashtags.map((t) => `#${t}`).join(" ");
  return [title.trim(), description.trim(), tags].filter(Boolean).join("\n\n");
}

export function fileName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path;
}
