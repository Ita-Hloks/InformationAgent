import { type FormEvent, useMemo, useRef, useState } from "react";
import { Check, Plus, Rss, X } from "lucide-react";

import { useOverlayDialog } from "../../hooks/useOverlayDialog";
import type { Feed } from "../../types";

type AddFeedDialogProps = {
  open: boolean;
  feeds: Feed[];
  onAddFeed: (input: { url: string; title?: string }) => Promise<void>;
  onClose: () => void;
};

const recommendations: Feed[] = [
  {
    id: "wired",
    url: "https://www.wired.com/feed/rss",
    name: "WIRED",
    domain: "wired.com/feed/rss",
    unread: 0,
    color: "#1d1d1f",
  },
  {
    id: "ars",
    url: "https://feeds.arstechnica.com/arstechnica/index",
    name: "Ars Technica",
    domain: "feeds.arstechnica.com",
    unread: 0,
    color: "#d9682c",
  },
  {
    id: "solidot",
    url: "https://feeds.feedburner.com/solidot",
    name: "Solidot",
    domain: "solidot.org/index.rss",
    unread: 0,
    color: "#3978a8",
  },
  {
    id: "ifanr",
    url: "https://www.ifanr.com/feed",
    name: "爱范儿",
    domain: "ifanr.com/feed",
    unread: 0,
    color: "#2b9b7a",
  },
];

export function AddFeedDialog({ open, feeds, onAddFeed, onClose }: AddFeedDialogProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const [customUrl, setCustomUrl] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const addedIds = useMemo(() => new Set(feeds.map(feed => feed.id)), [feeds]);

  useOverlayDialog(open, onClose, closeButtonRef);

  const addCustomFeed = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = customUrl.trim();
    if (!value) return;

    try {
      const parsed = new URL(value.includes("://") ? value : `https://${value}`);
      const domain = parsed.hostname.replace(/^www\./, "");
      setPending(true);
      await onAddFeed({ url: parsed.toString(), title: domain });
      setCustomUrl("");
      setError("");
      onClose();
    } catch (error) {
      setError(error instanceof Error ? error.message : "无法添加这个地址");
    } finally {
      setPending(false);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/35 p-4"
      role="presentation"
      onMouseDown={event => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="w-full max-w-[560px] overflow-hidden rounded-lg border border-[var(--reader-workspace-border)] bg-[var(--reader-workspace-raised)] shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-feed-title"
      >
        <header className="flex h-14 items-center justify-between border-b border-[#e1e1dc] px-4">
          <div className="flex items-center gap-2.5">
            <Rss size={17} className="text-[#c3623b]" />
            <h2 id="add-feed-title" className="text-sm font-semibold text-[#26272a]">
              查找并添加来源
            </h2>
          </div>
          <button
            type="button"
            ref={closeButtonRef}
            className="grid size-8 place-items-center rounded-md text-[#6f7277] hover:bg-[var(--reader-workspace-hover)]"
            aria-label="关闭添加来源"
            title="关闭添加来源"
            onClick={onClose}
          >
            <X size={17} />
          </button>
        </header>

        <div className="p-4">
          <div>
            <p className="text-[11px] font-semibold text-[#77797e]">推荐来源</p>
            <div className="mt-2 divide-y divide-[#e3e3de] border-y border-[#e3e3de]">
              {recommendations.map(feed => {
                const added = addedIds.has(feed.id) || feeds.some(item => item.url === feed.url);
                return (
                  <div key={feed.id} className="flex min-h-14 items-center gap-3 py-2">
                    <span
                      className="grid size-8 shrink-0 place-items-center rounded-md text-[11px] font-semibold text-white"
                      style={{ backgroundColor: feed.color }}
                    >
                      {feed.name.slice(0, 1)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <strong className="block truncate text-sm font-medium text-[#313236]">
                        {feed.name}
                      </strong>
                      <span className="mt-0.5 block truncate text-[11px] text-[#85878b]">
                        {feed.domain}
                      </span>
                    </span>
                    <button
                      type="button"
                      className={`flex h-8 items-center gap-1.5 rounded-md px-2.5 text-xs font-medium ${
                        added
                          ? "bg-[#e8f3ed] text-[#36775a]"
                          : "border border-[#d5d5cf] bg-white text-[#4d4f54] hover:border-[#bcbcb5]"
                      }`}
                      disabled={added || pending}
                      onClick={() => {
                        setPending(true);
                        onAddFeed({ url: feed.url, title: feed.name })
                          .catch(error =>
                            setError(error instanceof Error ? error.message : "无法添加这个来源"),
                          )
                          .finally(() => setPending(false));
                      }}
                    >
                      {added ? <Check size={14} /> : <Plus size={14} />}
                      {added ? "已添加" : "关注"}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>

          <form className="mt-5" onSubmit={addCustomFeed}>
            <label className="text-[11px] font-semibold text-[#77797e]" htmlFor="custom-feed-url">
              RSS 或网站地址
            </label>
            <div className="mt-2 flex gap-2">
              <input
                id="custom-feed-url"
                className="h-10 min-w-0 flex-1 rounded-md border border-[#d7d7d1] bg-white px-3 text-sm text-[#28292c] outline-none placeholder:text-[#9b9c9f] focus:border-[#aaa9a2]"
                placeholder="https://example.com/feed.xml"
                value={customUrl}
                onChange={event => setCustomUrl(event.target.value)}
              />
              <button
                type="submit"
                className="h-10 rounded-md bg-[#25272b] px-4 text-xs font-medium text-white hover:bg-[#36383d]"
              >
                {pending ? "添加中" : "添加"}
              </button>
            </div>
            <p className="mt-1.5 min-h-4 text-[11px] text-[#b7523c]">{error}</p>
          </form>
        </div>
      </section>
    </div>
  );
}
