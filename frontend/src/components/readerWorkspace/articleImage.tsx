import { ImageOff, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

type ArticleImageProps = {
  src: string;
  alt: string;
  label: string;
  className?: string;
  errorClassName?: string;
  imageClassName?: string;
  loading?: "eager" | "lazy";
  showRetry?: boolean;
  variant?: "hero" | "inline" | "thumbnail";
};

export function ArticleImage({
  src,
  alt,
  label,
  className = "",
  errorClassName,
  imageClassName = "h-full w-full object-cover",
  loading,
  showRetry = true,
  variant = "inline",
}: ArticleImageProps) {
  const [failed, setFailed] = useState(false);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    setFailed(false);
    setRetryCount(0);
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
              {compact ? "图片不可用" : `${label}加载失败`}
            </p>
            {!compact && (
              <p className="mt-0.5 max-w-[36rem] break-words text-xs leading-5 text-[#8f493d]">
                {imageErrorDetail(src)}
              </p>
            )}
          </div>
          {showRetry && !compact && (
            <button
              type="button"
              className="mt-1 inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-[#e3a39a] bg-white px-2.5 text-xs font-medium text-[#8f493d] hover:bg-[#fbe2dc]"
              aria-label={`重试${label}`}
              title={`重试${label}`}
              onClick={() => {
                setFailed(false);
                setRetryCount(current => current + 1);
              }}
            >
              <RefreshCw size={14} />
              重试
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={className}>
      <img
        key={`${src}-${retryCount}`}
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
