import { useEffect, useState } from "react";
import { Check, FileCog, Loader2, RotateCw, TriangleAlert, X } from "lucide-react";

import { getLLMSettings, openProjectEnvFile } from "../../api/client";
import type { LLMSettings } from "../../types";

type LoadPhase = "loading" | "ready" | "error";
type OpenPhase = "idle" | "opening" | "opened" | "error";

export function SettingsPage() {
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [loadPhase, setLoadPhase] = useState<LoadPhase>("loading");
  const [loadError, setLoadError] = useState("");
  const [openPhase, setOpenPhase] = useState<OpenPhase>("idle");
  const [openError, setOpenError] = useState("");

  const loadSettings = () => {
    setLoadPhase("loading");
    setLoadError("");
    void getLLMSettings()
      .then(result => {
        setSettings(result);
        setLoadPhase("ready");
      })
      .catch(error => {
        setLoadError(error instanceof Error ? error.message : "设置读取失败，请重试");
        setLoadPhase("error");
      });
  };

  useEffect(() => {
    loadSettings();
  }, []);

  const handleOpenEnv = () => {
    if (openPhase === "opening") return;
    setOpenPhase("opening");
    setOpenError("");
    void openProjectEnvFile()
      .then(() => setOpenPhase("opened"))
      .catch(error => {
        setOpenError(error instanceof Error ? error.message : "无法打开项目 .env 文件");
        setOpenPhase("error");
      });
  };

  return (
    <section className="flex h-full min-h-0 min-w-0 flex-col bg-[#f5f5f1] text-[#242528]">
      <header className="shrink-0 border-b border-[#ddded8] bg-[#f5f5f1] px-6 py-5">
        <h1 className="text-lg font-semibold">设置</h1>
      </header>

      <div className="workspace-scroll min-h-0 flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-3xl">
          <h2 className="text-sm font-semibold text-[#303238]">主 LLM</h2>

          {loadPhase === "loading" && (
            <div className="mt-4 flex items-center gap-2 text-sm text-[#73767b]">
              <Loader2 size={16} className="animate-spin" />
              读取中
            </div>
          )}

          {loadPhase === "error" && (
            <div className="mt-4 flex items-center gap-3 text-sm text-[#8a3e24]" role="alert">
              <TriangleAlert size={17} className="shrink-0" />
              <span>{loadError}</span>
              <button
                type="button"
                className="grid size-8 shrink-0 place-items-center rounded-md border border-[#e6b7a5] hover:bg-[#fff2ec]"
                aria-label="重新读取设置"
                title="重新读取设置"
                onClick={loadSettings}
              >
                <RotateCw size={14} />
              </button>
            </div>
          )}

          {loadPhase === "ready" && settings && (
            <div className="mt-4 divide-y divide-[#e3e3dd] border-y border-[#d9dad4] bg-white">
              <SettingRow
                label="API Key"
                value={settings.apiKeyConfigured ? "已配置" : "未配置"}
                status={settings.apiKeyConfigured ? "positive" : "negative"}
              />
              <SettingRow label="模型" value={settings.model || "未配置"} />
              <SettingRow label="Base URL" value={settings.baseUrl || "未配置"} />
              <SettingRow
                label="可用性"
                value={settings.available ? "可用" : "不可用"}
                status={settings.available ? "positive" : "negative"}
              />
            </div>
          )}

          <div className="mt-8 border-t border-[#ddded8] pt-5">
            <button
              type="button"
              className="flex h-10 items-center gap-2 rounded-md bg-[#25272b] px-4 text-sm font-medium text-white hover:bg-[#36383d] disabled:cursor-not-allowed disabled:opacity-60"
              onClick={handleOpenEnv}
              disabled={openPhase === "opening"}
            >
              {openPhase === "opening" ? (
                <Loader2 size={16} className="animate-spin" />
              ) : openPhase === "opened" ? (
                <Check size={16} />
              ) : (
                <FileCog size={16} />
              )}
              打开 .env
            </button>
            {openPhase === "error" && (
              <div className="mt-3 flex items-center gap-2 text-sm text-[#8a3e24]" role="alert">
                <X size={16} className="shrink-0" />
                <span>{openError}</span>
              </div>
            )}
            {openPhase === "opened" && (
              <p className="mt-3 text-sm text-[#36775a]" role="status">
                .env 已打开
              </p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

function SettingRow({
  label,
  value,
  status,
}: {
  label: string;
  value: string;
  status?: "positive" | "negative";
}) {
  return (
    <div className="flex min-h-14 items-center justify-between gap-5 px-4 py-3">
      <span className="shrink-0 text-xs font-medium text-[#62656b]">{label}</span>
      <span
        className={`min-w-0 truncate text-right text-sm ${
          status === "positive"
            ? "text-[#36775a]"
            : status === "negative"
              ? "text-[#a7512d]"
              : "text-[#303238]"
        }`}
      >
        {value}
      </span>
    </div>
  );
}
