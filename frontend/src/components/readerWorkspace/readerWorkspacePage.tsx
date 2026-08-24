import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useMatch, useNavigate, useOutlet, useSearchParams } from "react-router-dom";

import { feedPath, viewFromPath, viewPaths, viewTitles } from "../../app/navigation";
import {
  addFeed as createFeed,
  createResearchAgentRequestId,
  createResearchRun,
  deleteArticle,
  getArticles,
  getArticleResearch,
  getReaderAutomationSettings,
  getFeeds,
  refreshFeed,
  removeFeed,
  getResearchAgentStatus,
  getResearchRuns,
  retryArticleSummary,
  runResearchAgent,
  runArticleResearch,
  stopResearchAgent,
  type ArticleStateUpdate,
  updateArticleStates,
} from "../../api/client";
import { initialArticles, initialFeeds } from "../../data/localState";
import type {
  AgentReport,
  AgentTaskSnapshot,
  Feed,
  LibraryView,
  ArticleResearchRun,
  ReaderAutomationSettings,
  ResearchIngestResult,
  ResearchRun,
} from "../../types";
import { isArticleToday } from "../../utils/date";
import { AppSidebar } from "../appShell";
import { AddFeedDialog } from "./addFeedDialog";
import { ArticleList } from "./articleList";
import { AskPanel } from "./askPanel";
import { ReaderPane } from "./readerPane";
import { ResearchWorkspace } from "./researchWorkspace";

type OverlayName = "add-feed" | "ask";
type RouteState = {
  overlay?: OverlayName;
};

type ApiStatus = "connecting" | "connected" | "unavailable";

function bindArticleSources(articles: typeof initialArticles, feeds: Feed[]) {
  return articles.map(article => ({
    ...article,
    source: feeds.find(feed => feed.id === article.feedId)?.name ?? article.source,
  }));
}

export function ReaderWorkspacePage() {
  const location = useLocation();
  const navigate = useNavigate();
  const outlet = useOutlet();
  const feedMatch = useMatch("/feeds/:feedId");
  const [searchParams] = useSearchParams();
  const [feeds, setFeeds] = useState<Feed[]>(initialFeeds);
  const [articles, setArticles] = useState(initialArticles);
  const [researchRuns, setResearchRuns] = useState<ResearchRun[]>([]);
  const [automationSettings, setAutomationSettings] = useState<ReaderAutomationSettings | null>(
    null,
  );
  const [articleResearchRuns, setArticleResearchRuns] = useState<ArticleResearchRun[]>([]);
  const [articleResearchLoading, setArticleResearchLoading] = useState(false);
  const [articleResearchError, setArticleResearchError] = useState<string | null>(null);
  const [selectedResearchRunId, setSelectedResearchRunId] = useState<string | null>(() =>
    searchParams.get("run_id"),
  );
  const [ingestResult, setIngestResult] = useState<ResearchIngestResult | null>(null);
  const [agentReport, setAgentReport] = useState<AgentReport | null>(null);
  const [agentTask, setAgentTask] = useState<AgentTaskSnapshot | null>(null);
  const [researchPhase, setResearchPhase] = useState<"idle" | "ingesting" | "running-agent">(
    "idle",
  );
  const [researchError, setResearchError] = useState<string | null>(null);
  const agentReportRequestId = useRef(0);
  const agentTaskRequestId = useRef<string | null>(null);
  const [agentPollKey, setAgentPollKey] = useState(0);
  const [searchQuery, setSearchQuery] = useState("");
  const [savedIds, setSavedIds] = useState(
    () => new Set(initialArticles.filter(article => article.starred).map(article => article.id)),
  );
  const [readIds, setReadIds] = useState(
    () => new Set(initialArticles.filter(article => !article.unread).map(article => article.id)),
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [apiStatus, setApiStatus] = useState<ApiStatus>("connecting");
  const [feedActionId, setFeedActionId] = useState<string | null>(null);
  const [articleDeleteId, setArticleDeleteId] = useState<string | null>(null);
  const [articleDeleteError, setArticleDeleteError] = useState<string | null>(null);
  const [readingActivity, setReadingActivity] = useState({
    key: null as string | null,
    progress: 0,
    visibleSeconds: 0,
  });
  const autoResearchAttempts = useRef(new Set<string>());
  const selectedFeedId = feedMatch?.params.feedId ?? null;
  const activeView = selectedFeedId ? "all" : viewFromPath(location.pathname);

  const applyArticleStates = (states: Awaited<ReturnType<typeof updateArticleStates>>) => {
    setReadIds(current => {
      const next = new Set(current);
      states.forEach(state => {
        if (state.is_read) next.add(state.article_id);
        else next.delete(state.article_id);
      });
      return next;
    });
    setSavedIds(current => {
      const next = new Set(current);
      states.forEach(state => {
        if (state.is_saved) next.add(state.article_id);
        else next.delete(state.article_id);
      });
      return next;
    });
  };

  const persistArticleStates = async (articleIds: string[], update: ArticleStateUpdate) => {
    if (articleIds.length === 0) return;
    try {
      const states = await updateArticleStates(articleIds, update);
      applyArticleStates(states);
      setApiStatus("connected");
    } catch {
      setApiStatus("unavailable");
    }
  };

  const applyReaderData = useCallback(
    (loadedFeeds: Feed[], loadedArticles: typeof initialArticles) => {
      const nextArticles = bindArticleSources(loadedArticles, loadedFeeds);
      setFeeds(loadedFeeds);
      setArticles(nextArticles);
      setReadIds(
        new Set(nextArticles.filter(article => !article.unread).map(article => article.id)),
      );
      setSavedIds(
        new Set(nextArticles.filter(article => article.starred).map(article => article.id)),
      );
    },
    [],
  );

  const reloadReaderData = useCallback(async () => {
    const [loadedFeeds, loadedArticles] = await Promise.all([getFeeds(), getArticles()]);
    applyReaderData(loadedFeeds, loadedArticles);
  }, [applyReaderData]);

  const refreshArticles = useCallback(async () => {
    applyReaderData(feeds, await getArticles());
  }, [applyReaderData, feeds]);

  useEffect(() => {
    void Promise.all([reloadReaderData(), getResearchRuns(), getReaderAutomationSettings()])
      .then(([, loadedResearchRuns, loadedAutomationSettings]) => {
        setResearchRuns(loadedResearchRuns);
        setAutomationSettings(loadedAutomationSettings);
        setApiStatus("connected");
      })
      .catch(() => {
        setApiStatus("unavailable");
      });
  }, [reloadReaderData]);

  const hasPendingSummaries = articles.some(article =>
    ["pending", "running"].includes(article.summaryStatus),
  );

  useEffect(() => {
    if (!hasPendingSummaries) return;
    const timer = window.setInterval(() => {
      void refreshArticles().catch(() => setApiStatus("unavailable"));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [hasPendingSummaries, refreshArticles]);

  useEffect(() => {
    if (activeView !== "research") return;
    const urlRunId = searchParams.get("run_id");
    if (urlRunId && urlRunId !== selectedResearchRunId) {
      setSelectedResearchRunId(urlRunId);
      return;
    }
    if (activeView === "research" && !urlRunId && researchRuns[0]) {
      setSelectedResearchRunId(researchRuns[0].id);
      navigate(`/research?run_id=${encodeURIComponent(researchRuns[0].id)}`, { replace: true });
    }
  }, [activeView, navigate, researchRuns, searchParams, selectedResearchRunId]);

  useEffect(() => {
    if (activeView !== "research" || !selectedResearchRunId) {
      setAgentTask(null);
      setAgentReport(null);
      return;
    }
    const requestId = ++agentReportRequestId.current;
    const controller = new AbortController();
    let timer: number | null = null;
    setAgentReport(null);
    const poll = async () => {
      try {
        const task = await getResearchAgentStatus(
          selectedResearchRunId,
          agentTaskRequestId.current,
          controller.signal,
        );
        if (controller.signal.aborted || agentReportRequestId.current !== requestId) return;
        setAgentTask(task);
        setAgentReport(task.report);
        const active = ["created", "running"].includes(task.status);
        setResearchPhase(active ? "running-agent" : "idle");
        if (active) timer = window.setTimeout(() => void poll(), 500);
      } catch (error) {
        if (controller.signal.aborted || agentReportRequestId.current !== requestId) return;
        setAgentTask(null);
        setAgentReport(null);
        if (!(error instanceof Error && error.message === "不存在的 Agent 运行")) {
          setResearchError(error instanceof Error ? error.message : "Agent 状态读取失败");
        }
      }
    };
    void poll();
    return () => {
      controller.abort();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [activeView, agentPollKey, selectedResearchRunId]);

  useEffect(() => {
    if (
      activeView !== "research" ||
      !selectedResearchRunId ||
      searchParams.get("run_id") === selectedResearchRunId
    ) {
      return;
    }
    navigate(`/research?run_id=${encodeURIComponent(selectedResearchRunId)}`, { replace: true });
  }, [activeView, navigate, searchParams, selectedResearchRunId]);

  const articleParam = searchParams.get("article");
  const dialogParam = searchParams.get("dialog");
  const panelParam = searchParams.get("panel");

  const updateSearchParams = useCallback(
    (
      update: (next: URLSearchParams) => void,
      options?: { replace?: boolean; state?: RouteState },
    ) => {
      const next = new URLSearchParams(searchParams);
      update(next);
      const serialized = next.toString();
      navigate(
        { pathname: location.pathname, search: serialized ? `?${serialized}` : "" },
        { replace: options?.replace, state: options?.state },
      );
    },
    [location.pathname, navigate, searchParams],
  );

  const visibleArticles = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();
    return articles
      .filter(article => {
        if (selectedFeedId) return article.feedId === selectedFeedId;
        if (activeView === "inbox") {
          return !readIds.has(article.id) || article.id === articleParam;
        }
        if (activeView === "today") return isArticleToday(article.publishedAtIso);
        if (activeView === "saved") return savedIds.has(article.id);
        return true;
      })
      .filter(article => {
        if (!normalizedQuery) return true;
        return `${article.title} ${article.summary} ${article.source} ${article.category}`
          .toLowerCase()
          .includes(normalizedQuery);
      })
      .map(article => ({ ...article, unread: !readIds.has(article.id) }));
  }, [activeView, articleParam, articles, readIds, savedIds, searchQuery, selectedFeedId]);

  const selectedArticle =
    visibleArticles.find(article => article.id === articleParam) ?? visibleArticles[0] ?? null;
  const selectedArticleId = selectedArticle?.id ?? null;
  const selectedSnapshotId = selectedArticle?.snapshotId ?? null;
  const selectedArticleKey = selectedArticle
    ? `${selectedArticle.id}:${selectedArticle.snapshotId}`
    : null;
  const selectedFeed = feeds.find(feed => feed.id === selectedFeedId);
  const listTitle = selectedFeed?.name ?? viewTitles[activeView];
  const unreadTotal = articles.filter(article => !readIds.has(article.id)).length;
  const articleOpen = selectedArticleId !== null && articleParam === selectedArticleId;
  const routeState = location.state as RouteState | null;

  useEffect(() => {
    setReadingActivity({ key: selectedArticleKey, progress: 0, visibleSeconds: 0 });
    setArticleResearchRuns([]);
    setArticleResearchError(null);
    setArticleDeleteError(null);
  }, [selectedArticleKey]);

  const syncArticleResearchStatus = useCallback((run: ArticleResearchRun) => {
    setArticles(current =>
      current.map(article =>
        article.id === run.articleId
          ? { ...article, researchStatus: run.status, researchMode: run.mode }
          : article,
      ),
    );
  }, []);

  const loadArticleResearch = useCallback(async () => {
    if (!selectedArticleId) return;
    const history = await getArticleResearch(selectedArticleId);
    setArticleResearchRuns(history.runs);
    const automaticRun = history.runs.find(
      run => run.mode === "auto" && run.snapshotId === selectedSnapshotId,
    );
    if (automaticRun && selectedArticleKey) {
      autoResearchAttempts.current.add(selectedArticleKey);
      syncArticleResearchStatus(automaticRun);
    }
  }, [selectedArticleId, selectedArticleKey, selectedSnapshotId, syncArticleResearchStatus]);

  useEffect(() => {
    if (!selectedArticleKey) return;
    let active = true;
    setArticleResearchLoading(true);
    void loadArticleResearch()
      .catch(error => {
        if (active) {
          setArticleResearchError(error instanceof Error ? error.message : "研究记录读取失败");
        }
      })
      .finally(() => {
        if (active) setArticleResearchLoading(false);
      });
    return () => {
      active = false;
    };
  }, [loadArticleResearch, selectedArticleKey]);

  const latestArticleResearch = articleResearchRuns[0] ?? null;
  const articleResearchActive = articleResearchRuns.some(run =>
    ["queued", "running"].includes(run.status),
  );

  useEffect(() => {
    if (!selectedArticleId || !articleResearchActive) return;
    const timer = window.setInterval(() => {
      void loadArticleResearch().catch(error => {
        setArticleResearchError(error instanceof Error ? error.message : "研究记录读取失败");
      });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [articleResearchActive, loadArticleResearch, selectedArticleId]);

  const runArticleResearchForSelected = useCallback(
    async (mode: "auto" | "manual") => {
      if (!selectedArticleId) return;
      setArticleResearchError(null);
      try {
        const run = await runArticleResearch(selectedArticleId, { mode });
        setArticleResearchRuns(current => [run, ...current.filter(item => item.id !== run.id)]);
        syncArticleResearchStatus(run);
        setApiStatus("connected");
      } catch (error) {
        setArticleResearchError(error instanceof Error ? error.message : "研究任务启动失败");
        setApiStatus("unavailable");
      }
    },
    [selectedArticleId, syncArticleResearchStatus],
  );

  useEffect(() => {
    if (
      !automationSettings?.enabled ||
      !selectedArticleId ||
      !selectedArticleKey ||
      activeView === "research" ||
      readingActivity.key !== selectedArticleKey ||
      readingActivity.visibleSeconds < automationSettings.dwellSeconds ||
      readingActivity.progress < automationSettings.readRatio ||
      autoResearchAttempts.current.has(selectedArticleKey)
    ) {
      return;
    }
    autoResearchAttempts.current.add(selectedArticleKey);
    void runArticleResearchForSelected("auto");
  }, [
    activeView,
    automationSettings,
    readingActivity,
    runArticleResearchForSelected,
    selectedArticleId,
    selectedArticleKey,
  ]);

  const reportReaderProgress = useCallback(
    (progress: number) => {
      if (!selectedArticleKey) return;
      setReadingActivity(current =>
        current.key === selectedArticleKey ? { ...current, progress } : current,
      );
    },
    [selectedArticleKey],
  );

  const reportVisibleSeconds = useCallback(
    (visibleSeconds: number) => {
      if (!selectedArticleKey) return;
      setReadingActivity(current =>
        current.key === selectedArticleKey ? { ...current, visibleSeconds } : current,
      );
    },
    [selectedArticleKey],
  );

  const retrySelectedSummary = useCallback(async () => {
    if (!selectedArticleId) return;
    try {
      const retriedArticle = await retryArticleSummary(selectedArticleId);
      const nextArticle = bindArticleSources([retriedArticle], feeds)[0];
      if (!nextArticle) return;
      setArticles(current =>
        current.map(article => (article.id === nextArticle.id ? nextArticle : article)),
      );
      setApiStatus("connected");
    } catch {
      setApiStatus("unavailable");
    }
  }, [feeds, selectedArticleId]);

  useEffect(() => {
    setSidebarOpen(false);
    setSearchQuery("");
  }, [location.pathname]);

  useEffect(() => {
    if (!articleParam || visibleArticles.some(article => article.id === articleParam)) return;
    updateSearchParams(
      next => {
        if (visibleArticles[0]) next.set("article", visibleArticles[0].id);
        else next.delete("article");
        next.delete("panel");
      },
      { replace: true },
    );
  }, [articleParam, updateSearchParams, visibleArticles]);

  const selectView = (view: LibraryView) => {
    navigate(viewPaths[view]);
  };

  const selectFeed = (feedId: string) => {
    navigate(feedPath(feedId));
  };

  const selectResearchRun = (runId: string) => {
    setSelectedResearchRunId(runId);
    setIngestResult(null);
    agentTaskRequestId.current = null;
    setAgentTask(null);
    setAgentReport(null);
    setResearchError(null);
    setAgentPollKey(current => current + 1);
    navigate(`${viewPaths.research}?run_id=${encodeURIComponent(runId)}`);
  };

  const selectArticle = (articleId: string) => {
    setReadIds(current => new Set(current).add(articleId));
    void persistArticleStates([articleId], { isRead: true });
    updateSearchParams(next => {
      next.set("article", articleId);
      next.delete("panel");
      next.delete("dialog");
    });
  };

  const closeArticle = () => {
    updateSearchParams(
      next => {
        next.delete("article");
        next.delete("panel");
      },
      { replace: true },
    );
  };

  const toggleSaved = (articleId: string) => {
    const nextSaved = !savedIds.has(articleId);
    setSavedIds(current => {
      const next = new Set(current);
      if (next.has(articleId)) next.delete(articleId);
      else next.add(articleId);
      return next;
    });
    void persistArticleStates([articleId], { isSaved: nextSaved });
  };

  const markAllRead = () => {
    const articleIds = visibleArticles.map(article => article.id);
    setReadIds(current => {
      const next = new Set(current);
      visibleArticles.forEach(article => next.add(article.id));
      return next;
    });
    void persistArticleStates(articleIds, { isRead: true });
  };

  const deleteSelectedArticle = async () => {
    if (!selectedArticleId || articleDeleteId !== null) return;
    const articleId = selectedArticleId;
    setArticleDeleteError(null);
    setArticleDeleteId(articleId);
    try {
      await deleteArticle(articleId);
      setArticles(current => current.filter(article => article.id !== articleId));
      setReadIds(current => {
        const next = new Set(current);
        next.delete(articleId);
        return next;
      });
      setSavedIds(current => {
        const next = new Set(current);
        next.delete(articleId);
        return next;
      });
      setArticleResearchRuns([]);
      setApiStatus("connected");
      closeArticle();
    } catch (error) {
      setArticleDeleteError(error instanceof Error ? error.message : "文章删除失败，请重试");
      setApiStatus("unavailable");
    } finally {
      setArticleDeleteId(null);
    }
  };

  const addFeed = async (input: { url: string; title?: string }) => {
    await createFeed(input);
    await reloadReaderData();
    setApiStatus("connected");
  };

  const updateFeed = async (feedId: string) => {
    if (feedActionId !== null) return;
    setFeedActionId(feedId);
    try {
      await refreshFeed(feedId);
      await reloadReaderData();
      setApiStatus("connected");
    } finally {
      setFeedActionId(null);
    }
  };

  const unsubscribeFeed = async (feedId: string) => {
    const feed = feeds.find(item => item.id === feedId);
    if (!feed || feedActionId !== null) return;
    setFeedActionId(feedId);
    try {
      await removeFeed(feedId);
      await reloadReaderData();
      if (selectedFeedId === feedId) navigate("/");
      setApiStatus("connected");
    } finally {
      setFeedActionId(null);
    }
  };

  const refreshResearchRuns = useCallback(async () => {
    try {
      const loadedResearchRuns = await getResearchRuns();
      setResearchRuns(loadedResearchRuns);
      setApiStatus("connected");
    } catch {
      setApiStatus("unavailable");
    }
  }, []);

  const createRun = async (input: {
    topic: string;
    feeds: string[];
    timeoutSeconds: number;
    limit: number;
  }) => {
    setResearchPhase("ingesting");
    setResearchError(null);
    agentTaskRequestId.current = null;
    setAgentTask(null);
    setAgentReport(null);
    try {
      const result = await createResearchRun(input);
      setIngestResult(result);
      setSelectedResearchRunId(result.run_id);
      agentTaskRequestId.current = null;
      setAgentTask(null);
      navigate(`${viewPaths.research}?run_id=${encodeURIComponent(result.run_id)}`);
      await refreshResearchRuns();
    } catch (error) {
      setResearchError(error instanceof Error ? error.message : "研究运行创建失败");
    } finally {
      setResearchPhase("idle");
    }
  };

  const runAgent = async (runId: string) => {
    const requestId = ++agentReportRequestId.current;
    const taskRequestId = createResearchAgentRequestId();
    agentTaskRequestId.current = taskRequestId;
    setResearchPhase("running-agent");
    setResearchError(null);
    setAgentReport(null);
    setAgentTask(null);
    try {
      const task = await runResearchAgent(
        runId,
        {
          timeoutSeconds: 180,
          maxSteps: 3,
          maxAttempts: 3,
        },
        taskRequestId,
      );
      if (agentReportRequestId.current === requestId) {
        setAgentTask(task);
        setAgentReport(task.report);
        setAgentPollKey(current => current + 1);
      }
      await refreshResearchRuns();
    } catch (error) {
      if (agentReportRequestId.current === requestId) {
        setResearchError(error instanceof Error ? error.message : "Agent 运行失败");
      }
    }
  };

  const stopAgent = async (runId: string) => {
    setResearchError(null);
    try {
      const task = await stopResearchAgent(runId, agentTaskRequestId.current);
      setAgentTask(task);
      setAgentReport(task.report);
      setAgentPollKey(current => current + 1);
    } catch (error) {
      setResearchError(error instanceof Error ? error.message : "Agent 停止失败");
    }
  };

  const openOverlay = useCallback(
    (overlay: OverlayName) => {
      updateSearchParams(
        next => {
          if (overlay === "add-feed") {
            next.set("dialog", overlay);
            next.delete("panel");
          } else {
            if (!selectedArticleId) return;
            next.set("article", selectedArticleId);
            next.set("panel", overlay);
            next.delete("dialog");
          }
        },
        { state: { overlay } },
      );
    },
    [selectedArticleId, updateSearchParams],
  );

  const closeOverlay = useCallback(
    (overlay: OverlayName) => {
      if (routeState?.overlay === overlay) {
        navigate(-1);
        return;
      }
      updateSearchParams(
        next => {
          if (overlay === "add-feed") next.delete("dialog");
          else next.delete("panel");
        },
        { replace: true },
      );
    },
    [navigate, routeState?.overlay, updateSearchParams],
  );

  const closeAddFeed = useCallback(() => closeOverlay("add-feed"), [closeOverlay]);
  const closeAsk = useCallback(() => closeOverlay("ask"), [closeOverlay]);
  const openSidebar = () => {
    setSidebarCollapsed(false);
    setSidebarOpen(true);
  };

  return (
    <div className="h-dvh min-h-[600px] overflow-hidden bg-[var(--reader-workspace-bg)] text-[#242528]">
      {sidebarOpen && (
        <button
          type="button"
          className="fixed inset-0 z-30 bg-black/25 xl:hidden"
          aria-label="关闭资料库导航"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <div
        className={`sidebar-layout grid h-full min-w-0 grid-cols-1 md:grid-cols-[340px_minmax(0,1fr)] xl:grid-cols-[252px_370px_minmax(0,1fr)] ${
          sidebarCollapsed ? "is-collapsed" : ""
        }`}
      >
        <AppSidebar
          activeView={activeView}
          feeds={feeds}
          researchRuns={researchRuns}
          selectedFeedId={selectedFeedId}
          unreadTotal={unreadTotal}
          apiStatus={apiStatus}
          open={sidebarOpen}
          collapsed={sidebarCollapsed}
          onClose={() => setSidebarOpen(false)}
          onCollapse={() => setSidebarCollapsed(true)}
          onExpand={openSidebar}
          onSelectView={selectView}
          onSelectFeed={selectFeed}
          onSelectResearchRun={selectResearchRun}
          onAddFeed={() => openOverlay("add-feed")}
          onUnsubscribe={feedId => {
            void unsubscribeFeed(feedId).catch(() => setApiStatus("unavailable"));
          }}
          settingsActive={location.pathname === "/settings"}
          onOpenSettings={() => {
            navigate("/settings");
            setSidebarOpen(false);
          }}
        />

        {location.pathname === "/settings" && outlet ? (
          <div className="h-full min-h-0 min-w-0 md:col-span-1 xl:col-span-2">{outlet}</div>
        ) : activeView === "research" ? (
          <div className="h-full min-h-0 min-w-0 overflow-hidden md:col-span-1 xl:col-span-2">
            <ResearchWorkspace
              runs={researchRuns}
              selectedRunId={selectedResearchRunId}
              ingestResult={ingestResult}
              agentReport={agentReport}
              agentTask={agentTask}
              phase={researchPhase}
              error={researchError}
              onCreateRun={createRun}
              onRunAgent={runAgent}
              onStopAgent={stopAgent}
              onSelectRun={selectResearchRun}
              onRefreshRuns={refreshResearchRuns}
            />
          </div>
        ) : (
          <>
            <div className={`min-h-0 min-w-0 ${articleOpen ? "hidden" : "block"} md:block`}>
              <ArticleList
                title={listTitle}
                articles={visibleArticles}
                selectedArticleId={selectedArticleId}
                savedIds={savedIds}
                searchQuery={searchQuery}
                onSearchChange={setSearchQuery}
                onSelectArticle={selectArticle}
                onToggleSaved={toggleSaved}
                onMarkAllRead={markAllRead}
                onOpenSidebar={openSidebar}
                onRefreshFeed={
                  selectedFeedId
                    ? () => {
                        void updateFeed(selectedFeedId).catch(() => setApiStatus("unavailable"));
                      }
                    : null
                }
                refreshingFeed={selectedFeedId !== null && feedActionId === selectedFeedId}
              />
            </div>

            <div className={`min-h-0 min-w-0 ${articleOpen ? "block" : "hidden"} md:block`}>
              <ReaderPane
                article={selectedArticle}
                saved={selectedArticle ? savedIds.has(selectedArticle.id) : false}
                read={selectedArticle ? readIds.has(selectedArticle.id) : false}
                onBack={closeArticle}
                onToggleSaved={() => {
                  if (selectedArticle) toggleSaved(selectedArticle.id);
                }}
                onMarkRead={() => {
                  if (selectedArticle) {
                    setReadIds(current => new Set(current).add(selectedArticle.id));
                    void persistArticleStates([selectedArticle.id], { isRead: true });
                  }
                }}
                onAsk={() => openOverlay("ask")}
                onDelete={() => void deleteSelectedArticle()}
                deleting={selectedArticle ? articleDeleteId === selectedArticle.id : false}
                deleteError={articleDeleteError}
                onProgress={reportReaderProgress}
                onVisibleSeconds={reportVisibleSeconds}
                onTestResearch={() => void runArticleResearchForSelected("manual")}
                onRetrySummary={() => void retrySelectedSummary()}
                researchRun={latestArticleResearch}
                researchLoading={articleResearchLoading}
                researchError={articleResearchError}
              />
            </div>
          </>
        )}
      </div>

      {selectedArticle && (
        <AskPanel article={selectedArticle} open={panelParam === "ask"} onClose={closeAsk} />
      )}
      <AddFeedDialog
        open={dialogParam === "add-feed"}
        feeds={feeds}
        onAddFeed={addFeed}
        onClose={closeAddFeed}
      />
    </div>
  );
}
