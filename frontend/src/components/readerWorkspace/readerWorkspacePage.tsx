import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useMatch, useNavigate, useOutlet, useSearchParams } from "react-router-dom";

import { feedPath, viewFromPath, viewPaths, viewTitles } from "../../app/navigation";
import {
  addFeed as createFeed,
  deleteArticle,
  getArticles,
  getArticleResearch,
  getArticleResearchRun,
  getReaderAutomationSettings,
  getFeeds,
  refreshFeed,
  removeFeed,
  retryArticleSummary,
  runArticleResearch,
  stopArticleResearch,
  type ArticleStateUpdate,
  updateArticleStates,
} from "../../api/client";
import { initialArticles, initialFeeds } from "../../data/localState";
import type { Feed, LibraryView, ArticleResearchRun, ReaderAutomationSettings } from "../../types";
import { isArticleToday } from "../../utils/date";
import { AppSidebar } from "../appShell";
import { AddFeedDialog } from "./addFeedDialog";
import { ArticleList } from "./articleList";
import { AskPanel } from "./askPanel";
import { ReaderPane } from "./readerPane";

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
  const [automationSettings, setAutomationSettings] = useState<ReaderAutomationSettings | null>(
    null,
  );
  const [articleResearchRuns, setArticleResearchRuns] = useState<ArticleResearchRun[]>([]);
  const [selectedArticleResearchId, setSelectedArticleResearchId] = useState<string | null>(null);
  const [articleResearchDetail, setArticleResearchDetail] = useState<ArticleResearchRun | null>(
    null,
  );
  const [articleResearchLoading, setArticleResearchLoading] = useState(false);
  const [articleResearchError, setArticleResearchError] = useState<string | null>(null);
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
    void Promise.all([reloadReaderData(), getReaderAutomationSettings()])
      .then(([, loadedAutomationSettings]) => {
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
    setSelectedArticleResearchId(null);
    setArticleResearchDetail(null);
    setArticleResearchError(null);
    setArticleDeleteError(null);
  }, [selectedArticleKey]);

  const syncArticleResearchStatus = useCallback((run: ArticleResearchRun) => {
    setArticles(current =>
      current.map(article =>
        article.id === run.articleId && article.snapshotId === run.snapshotId
          ? { ...article, researchStatus: run.status, researchMode: run.mode }
          : article,
      ),
    );
  }, []);

  const loadArticleResearch = useCallback(
    async (signal?: AbortSignal) => {
      if (!selectedArticleId) return;
      const history = await getArticleResearch(selectedArticleId, { signal });
      if (signal?.aborted) return history;
      setArticleResearchRuns(history.runs);
      const automaticRun = history.runs.find(
        run => run.mode === "auto" && run.snapshotId === selectedSnapshotId,
      );
      if (automaticRun && selectedArticleKey) {
        autoResearchAttempts.current.add(selectedArticleKey);
        syncArticleResearchStatus(automaticRun);
      }
      return history;
    },
    [selectedArticleId, selectedArticleKey, selectedSnapshotId, syncArticleResearchStatus],
  );

  useEffect(() => {
    if (!selectedArticleKey || !selectedArticleId) {
      setArticleResearchLoading(false);
      return;
    }
    const controller = new AbortController();
    let active = true;
    setArticleResearchLoading(true);
    void loadArticleResearch(controller.signal)
      .then(history => {
        if (!active || !history) return;
        const latestCurrentRun = history.runs.find(run => run.snapshotId === selectedSnapshotId);
        setSelectedArticleResearchId(latestCurrentRun?.id ?? null);
      })
      .catch(error => {
        if (active && !(error instanceof DOMException && error.name === "AbortError")) {
          setArticleResearchError(error instanceof Error ? error.message : "研究记录读取失败");
        }
      })
      .finally(() => {
        if (active) setArticleResearchLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [loadArticleResearch, selectedArticleId, selectedArticleKey, selectedSnapshotId]);

  const currentArticleResearchRuns = articleResearchRuns.filter(
    run => run.snapshotId === selectedSnapshotId,
  );
  const selectedArticleResearch =
    articleResearchRuns.find(run => run.id === selectedArticleResearchId) ?? null;
  const articleResearchActive = currentArticleResearchRuns.some(run =>
    ["queued", "running"].includes(run.status),
  );
  const selectedResearchActive = Boolean(
    selectedArticleResearch && ["queued", "running"].includes(selectedArticleResearch.status),
  );

  const loadArticleResearchDetail = useCallback(
    async (runId: string, signal?: AbortSignal) => {
      if (!selectedArticleId) return;
      const detail = await getArticleResearchRun(selectedArticleId, runId, signal);
      if (signal?.aborted) return;
      setArticleResearchDetail(detail);
      syncArticleResearchStatus(detail);
    },
    [selectedArticleId, syncArticleResearchStatus],
  );

  useEffect(() => {
    if (!selectedArticleId || !selectedArticleResearchId) {
      setArticleResearchDetail(null);
      setArticleResearchLoading(false);
      return;
    }
    const controller = new AbortController();
    let active = true;
    setArticleResearchLoading(true);
    void loadArticleResearchDetail(selectedArticleResearchId, controller.signal)
      .catch(error => {
        if (active && !(error instanceof DOMException && error.name === "AbortError")) {
          setArticleResearchDetail(null);
          setArticleResearchError(error instanceof Error ? error.message : "研究详情读取失败");
        }
      })
      .finally(() => {
        if (active) setArticleResearchLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [loadArticleResearchDetail, selectedArticleId, selectedArticleResearchId]);

  useEffect(() => {
    if (!selectedArticleId || !articleResearchActive) return;
    const timer = window.setInterval(() => {
      void loadArticleResearch().catch(error => {
        setArticleResearchError(error instanceof Error ? error.message : "研究记录读取失败");
      });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [articleResearchActive, loadArticleResearch, selectedArticleId]);

  useEffect(() => {
    if (!selectedArticleId || !selectedArticleResearchId || !selectedResearchActive) return;
    const timer = window.setInterval(() => {
      void loadArticleResearchDetail(selectedArticleResearchId).catch(error => {
        setArticleResearchError(error instanceof Error ? error.message : "研究详情读取失败");
      });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [
    loadArticleResearchDetail,
    selectedArticleId,
    selectedArticleResearchId,
    selectedResearchActive,
  ]);

  const runArticleResearchForSelected = useCallback(
    async (mode: "auto" | "manual") => {
      if (!selectedArticleId) return;
      if (articleResearchActive) {
        const activeRun = currentArticleResearchRuns.find(run =>
          ["queued", "running"].includes(run.status),
        );
        if (activeRun) setSelectedArticleResearchId(activeRun.id);
        return;
      }
      setArticleResearchError(null);
      try {
        const run = await runArticleResearch(selectedArticleId, { mode });
        setArticleResearchRuns(current => [run, ...current.filter(item => item.id !== run.id)]);
        setSelectedArticleResearchId(run.id);
        setArticleResearchDetail(null);
        syncArticleResearchStatus(run);
        setApiStatus("connected");
      } catch (error) {
        setArticleResearchError(error instanceof Error ? error.message : "研究任务启动失败");
        setApiStatus("unavailable");
      }
    },
    [
      articleResearchActive,
      currentArticleResearchRuns,
      selectedArticleId,
      syncArticleResearchStatus,
    ],
  );

  const stopArticleResearchForSelected = useCallback(async () => {
    if (!selectedArticleId || !selectedArticleResearch) return;
    if (!["queued", "running"].includes(selectedArticleResearch.status)) return;
    setArticleResearchError(null);
    try {
      const stoppedRun = await stopArticleResearch(selectedArticleId, selectedArticleResearch.id);
      setArticleResearchRuns(current =>
        current.map(run => (run.id === stoppedRun.id ? stoppedRun : run)),
      );
      setArticleResearchDetail(stoppedRun);
      syncArticleResearchStatus(stoppedRun);
      setApiStatus("connected");
    } catch (error) {
      setArticleResearchError(error instanceof Error ? error.message : "研究停止失败");
      setApiStatus("unavailable");
    }
  }, [selectedArticleId, selectedArticleResearch, syncArticleResearchStatus]);

  useEffect(() => {
    if (
      !automationSettings?.enabled ||
      !selectedArticleId ||
      !selectedArticleKey ||
      currentArticleResearchRuns.some(run => run.mode === "auto") ||
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
    automationSettings,
    currentArticleResearchRuns,
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
      setSelectedArticleResearchId(null);
      setArticleResearchDetail(null);
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

  const selectArticleResearch = (runId: string) => {
    setArticleResearchError(null);
    setArticleResearchDetail(null);
    setSelectedArticleResearchId(runId);
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
                onResearch={() => void runArticleResearchForSelected("manual")}
                onStopResearch={() => void stopArticleResearchForSelected()}
                onRetrySummary={() => void retrySelectedSummary()}
                researchRuns={articleResearchRuns}
                selectedResearchRunId={selectedArticleResearchId}
                onSelectResearchRun={selectArticleResearch}
                researchRun={articleResearchDetail}
                researchRunning={articleResearchActive}
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
