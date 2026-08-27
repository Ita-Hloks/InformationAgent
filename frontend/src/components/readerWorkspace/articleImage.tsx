import { ExternalLink, ImageOff } from "lucide-react";
import { useEffect, useState } from "react";

type ArticleImageProps = {
  src: string;
  alt: string;
  className?: string;
  errorClassName?: string;
  imageClassName?: string;
  fallbackHref?: string;
  loading?: "eager" | "lazy";
  variant?: "hero" | "inline" | "thumbnail";
};

export function ArticleImage({
  src,
  alt,
  className = "",
  errorClassName,
  imageClassName = "h-full w-full object-cover",
  fallbackHref,
  loading,
  variant = "inline",
}: ArticleImageProps) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [src]);

  if (failed) {
    const compact = variant === "thumbnail";
    return (
      <div
        className={`${errorClassName ?? className} flex items-center justify-center overflow-hidden border border-[#e3a39a] bg-[#fff5f2] text-[#a33f31]`}
        role="alert"
      >
        <div className={`flex max-w-full items-center ${compact ? "gap-1" : "flex-col gap-2"}`}>
          <ImageOff size={compact ? 13 : 20} className="shrink-0" aria-hidden="true" />
          <div className={compact ? "min-w-0" : "text-center"}>
            <p className={compact ? "truncate text-[10px] font-medium" : "text-sm font-medium"}>
              {compact ? "图片不可用" : "来源站限制或图片暂时不可访问"}
            </p>
            {!compact && (
              <p className="mt-0.5 max-w-[36rem] break-words text-xs leading-5 text-[#8f493d]">
                {imageErrorDetail(src)}
              </p>
            )}
          </div>
          {!compact && fallbackHref && (
            <a
              href={fallbackHref}
              target="_blank"
              rel="noreferrer"
              className="mt-1 inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-[#e3a39a] bg-white px-2.5 text-xs font-medium text-[#8f493d] hover:bg-[#fbe2dc]"
              aria-label="打开原文查看图片"
              title="打开原文查看图片"
            >
              <ExternalLink size={14} />
              打开原文
            </a>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={className}>
      <img
        className={imageClassName}
        src={src}
        alt={alt}
        loading={loading}
        onError={() => setFailed(true)}
      />
    </div>
  );
}

function imageErrorDetail(src: string): string {
  try {
    const host = new URL(src).host;
    return host ? `浏览器无法从 ${host} 加载图片` : "图片地址无效或无法访问";
  } catch {
    return "图片地址无效或无法访问";
  }
}
