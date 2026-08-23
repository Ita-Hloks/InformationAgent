import { useEffect, useState } from "react";
import {
  Check,
  FileCog,
  HardDrive,
  Loader2,
  RotateCw,
  Search,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";

import {
  clearLogDirectory,
  getLLMSettings,
  getLogSettings,
  getSearchLLMSettings,
  openProjectEnvFile,
} from "../../api/client";
import type { LLMSettings, LogSettings, SearchLLMSettings } from "../../types";

type LoadPhase = "loading" | "ready" | "error";
type OpenPhase = "idle" | "opening" | "opened" | "error";
type ClearPhase = "idle" | "clearing" | "cleared" | "error";

export function SettingsPage() {
  const [settings, setSettings] = useState<LLMSettings | null>(null);
  const [loadPhase, setLoadPhase] = useState<LoadPhase>("loading");
  const [loadError, setLoadError] = useState("");
  const [searchSettings, setSearchSettings] = useState<SearchLLMSettings | null>(null);
  const [searchLoadPhase, setSearchLoadPhase] = useState<LoadPhase>("loading");
  const [searchLoadError, setSearchLoadError] = useState("");
  const [logSettings, setLogSettings] = useState<LogSettings | null>(null);
  const [logLoadPhase, setLogLoadPhase] = useState<LoadPhase>("loading");
  const [logLoadError, setLogLoadError] = useState("");
  const [clearPhase, setClearPhase] = useState<ClearPhase>("idle");
  const [clearError, setClearError] = useState("");
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

  const loadSearchSettings = () => {
    setSearchLoadPhase("loading");
    setSearchLoadError("");
    void getSearchLLMSettings()
      .then(result => {
        setSearchSettings(result);
        setSearchLoadPhase("ready");
      })
      .catch(error => {
        setSearchLoadError(error instanceof Error ? error.message : "联网搜索设置读取失败，请重试");
        setSearchLoadPhase("error");
      });
  };

  const loadLogSettings = () => {
    setLogLoadPhase("loading");
    setLogLoadError("");
    void getLogSettings()
      .then(result => {
        setLogSettings(result);
        setLogLoadPhase("ready");
      })
      .catch(error => {
        setLogLoadError(error instanceof Error ? error.message : "日志设置读取失败，请重试");
        setLogLoadPhase("error");
      });
  };

  useEffect(() => {
    loadSettings();
  }, []);

  useEffect(() => {
    loadSearchSettings();
  }, []);

  useEffect(() => {
    loadLogSettings();
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

  const handleClearLogs = () => {
    if (clearPhase === "clearing") return;
    if (!window.confirm("确认清理全部日志文件？")) return;

    setClearPhase("clearing");
    setClearError("");
    void clearLogDirectory()
      .then(() => {
        setClearPhase("cleared");
        loadLogSettings();
      })
      .catch(error => {
        setClearError(error instanceof Error ? error.message : "日志清理失败，请重试");
        setClearPhase("error");
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
            <div className="flex items-center gap-2">
              <Search size={16} className="text-[#3978a8]" />
              <h2 className="text-sm font-semibold text-[#303238]">联网搜索</h2>
            </div>

            {searchLoadPhase === "loading" && (
              <div className="mt-4 flex items-center gap-2 text-sm text-[#73767b]">
                <Loader2 size={16} className="animate-spin" />
                读取中
              </div>
            )}

            {searchLoadPhase === "error" && (
              <div className="mt-4 flex items-center gap-3 text-sm text-[#8a3e24]" role="alert">
                <TriangleAlert size={17} className="shrink-0" />
                <span>{searchLoadError}</span>
                <button
                  type="button"
                  className="grid size-8 shrink-0 place-items-center rounded-md border border-[#e6b7a5] hover:bg-[#fff2ec]"
                  aria-label="重新读取联网搜索设置"
                  title="重新读取联网搜索设置"
                  onClick={loadSearchSettings}
                >
                  <RotateCw size={14} />
                </button>
              </div>
            )}

            {searchLoadPhase === "ready" && searchSettings && (
              <div className="mt-4 divide-y divide-[#e3e3dd] border-y border-[#d9dad4] bg-white">
                <SettingRow
                  label="API Key"
                  value={searchSettings.apiKeyConfigured ? "已配置" : "未配置"}
                  status={searchSettings.apiKeyConfigured ? "positive" : "negative"}
                />
                <SettingRow label="模型" value={searchSettings.model || "未配置"} />
                <SettingRow label="Base URL" value={searchSettings.baseUrl || "未配置"} />
                <SettingRow
                  label="结果数量"
                  value={
                    searchSettings.resultCount === null
                      ? "未配置"
                      : `${searchSettings.resultCount} 条`
                  }
                />
                <SettingRow label="内容大小" value={searchSettings.contentSize || "未配置"} />
                <SettingRow
                  label="超时"
                  value={
                    searchSettings.timeoutSeconds === null
                      ? "未配置"
                      : `${searchSettings.timeoutSeconds} 秒`
                  }
                />
                <SettingRow
                  label="可用性"
                  value={searchSettings.available ? "可用" : "不可用"}
                  status={searchSettings.available ? "positive" : "negative"}
                />
                {searchSettings.error && (
                  <SettingRow label="错误" value={searchSettings.error} status="negative" />
                )}
              </div>
            )}
          </div>

          <div className="mt-8 border-t border-[#ddded8] pt-5">
            <div className="flex items-center gap-2">
              <HardDrive size={16} className="text-[#3978a8]" />
              <h2 className="text-sm font-semibold text-[#303238]">日志</h2>
            </div>

            {logLoadPhase === "loading" && (
              <div className="mt-4 flex items-center gap-2 text-sm text-[#73767b]">
                <Loader2 size={16} className="animate-spin" />
                读取中
              </div>
            )}

            {logLoadPhase === "error" && (
              <div className="mt-4 flex items-center gap-3 text-sm text-[#8a3e24]" role="alert">
                <TriangleAlert size={17} className="shrink-0" />
                <span>{logLoadError}</span>
                <button
                  type="button"
                  className="grid size-8 shrink-0 place-items-center rounded-md border border-[#e6b7a5] hover:bg-[#fff2ec]"
                  aria-label="重新读取日志设置"
                  title="重新读取日志设置"
                  onClick={loadLogSettings}
                >
                  <RotateCw size={14} />
                </button>
              </div>
            )}

            {logLoadPhase === "ready" && logSettings && (
              <div className="mt-4 divide-y divide-[#e3e3dd] border-y border-[#d9dad4] bg-white">
                <SettingRow label="文件数量" value={`${logSettings.fileCount} 个`} />
                <SettingRow label="占用" value={formatBytes(logSettings.totalBytes)} />
                <SettingRow label="最早时间" value={formatDateTime(logSettings.earliestAt)} />
                <SettingRow label="保留期限" value={`${logSettings.retentionDays} 天`} />
                <SettingRow label="容量上限" value={formatBytes(logSettings.maxBytes)} />
              </div>
            )}

            <div className="mt-4">
              <button
                type="button"
                className="flex h-10 items-center gap-2 rounded-md border border-[#c9cbc5] bg-white px-4 text-sm font-medium text-[#303238] hover:bg-[#f0f1ec] disabled:cursor-not-allowed disabled:opacity-60"
                onClick={handleClearLogs}
                disabled={clearPhase === "clearing"}
              >
                {clearPhase === "clearing" ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : clearPhase === "cleared" ? (
                  <Check size={16} />
                ) : (
                  <Trash2 size={16} />
                )}
                清理日志
              </button>
              {clearPhase === "error" && (
                <div className="mt-3 flex items-center gap-2 text-sm text-[#8a3e24]" role="alert">
                  <X size={16} className="shrink-0" />
                  <span>{clearError}</span>
                </div>
              )}
              {clearPhase === "cleared" && (
                <p className="mt-3 text-sm text-[#36775a]" role="status">
                  日志已清理
                </p>
              )}
            </div>
          </div>

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

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;

  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes;
  let unitIndex = -1;
  do {
    value /= 1024;
    unitIndex += 1;
  } while (value >= 1024 && unitIndex < units.length - 1);

  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}

function formatDateTime(value: string | null): string {
  if (!value) return "暂无";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
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
