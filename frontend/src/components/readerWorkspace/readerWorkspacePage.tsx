import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useMatch, useNavigate, useOutlet, useSearchParams } from "react-router-dom";

import { feedPath, viewFromPath, viewPaths, viewTitles } from "../../app/navigation";
import {
  addFeed as createFeed,
  createResearchRun,
  getArticles,
  getFeeds,
  getResearchAgentReport,
  getResearchRuns,
  runResearchAgent,
  type ArticleStateUpdate,
  updateArticleStates,
} from "../../api/client";
import { initialArticles, initialFeeds } from "../../data/localState";
import type {
  AgentReport,
  Feed,
  LibraryView,
  ResearchIngestResult,
  ResearchRun,
} from "../../types";
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
  const [selectedResearchRunId, setSelectedResearchRunId] = useState<string | null>(null);
  const [ingestResult, setIngestResult] = useState<ResearchIngestResult | null>(null);
  const [agentReport, setAgentReport] = useState<AgentReport | null>(null);
  const [researchPhase, setResearchPhase] = useState<"idle" | "ingesting" | "running-agent">(
    "idle",
  );
  const [researchError, setResearchError] = useState<string | null>(null);
  const agentReportRequestId = useRef(0);
  const [searchQuery, setSearchQuery] = useState("");
  const [savedIds, setSavedIds] = useState(
    () => new Set(initialArticles.filter(article => article.starred).map(article => article.id)),
  );
  const [readIds, setReadIds] = useState(
    () => new Set(initialArticles.filter(article => !article.unread).map(article => article.id)),
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [apiStatus, setApiStatus] = useState<ApiStatus>("connecting");

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

  useEffect(() => {
    void Promise.all([getFeeds(), getArticles(), getResearchRuns()])
      .then(([loadedFeeds, loadedArticles, loadedResearchRuns]) => {
        setFeeds(loadedFeeds);
        const nextArticles = bindArticleSources(loadedArticles, loadedFeeds);
        setArticles(nextArticles);
        setResearchRuns(loadedResearchRuns);
        setReadIds(
          new Set(nextArticles.filter(article => !article.unread).map(article => article.id)),
        );
        setSavedIds(
          new Set(nextArticles.filter(article => article.starred).map(article => article.id)),
        );
        setApiStatus("connected");
      })
      .catch(() => {
        setApiStatus("unavailable");
      });
  }, []);

  useEffect(() => {
    if (!selectedResearchRunId) return;
    const requestId = ++agentReportRequestId.current;
    const controller = new AbortController();
    setAgentReport(null);
    void getResearchAgentReport(selectedResearchRunId, controller.signal)
      .then(report => {
        if (agentReportRequestId.current === requestId) setAgentReport(report);
      })
      .catch(error => {
        if (controller.signal.aborted || agentReportRequestId.current !== requestId) return;
        setResearchError(error instanceof Error ? error.message : "Agent 结果读取失败");
      });
    return () => {
      controller.abort();
    };
  }, [selectedResearchRunId]);

  const selectedFeedId = feedMatch?.params.feedId ?? null;
  const activeView = selectedFeedId ? "all" : viewFromPath(location.pathname);
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
        if (activeView === "today") return !["昨天", "周二"].includes(article.publishedAt);
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
  const selectedFeed = feeds.find(feed => feed.id === selectedFeedId);
  const listTitle = selectedFeed?.name ?? viewTitles[activeView];
  const unreadTotal = articles.filter(article => !readIds.has(article.id)).length;
  const articleOpen = selectedArticleId !== null && articleParam === selectedArticleId;
  const routeState = location.state as RouteState | null;

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
    setAgentReport(null);
    setResearchError(null);
    navigate(viewPaths.research);
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

  const addFeed = async (input: { url: string; title?: string }) => {
    const feed = await createFeed(input);
    const [loadedFeeds, loadedArticles] = await Promise.all([getFeeds(), getArticles()]);
    const nextArticles = bindArticleSources(loadedArticles, loadedFeeds);
    setFeeds(loadedFeeds.some(item => item.id === feed.id) ? loadedFeeds : [...loadedFeeds, feed]);
    setArticles(nextArticles);
    setReadIds(new Set(nextArticles.filter(article => !article.unread).map(article => article.id)));
    setSavedIds(
      new Set(nextArticles.filter(article => article.starred).map(article => article.id)),
    );
    setApiStatus("connected");
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
    setAgentReport(null);
    try {
      const result = await createResearchRun(input);
      setIngestResult(result);
      setSelectedResearchRunId(result.run_id);
      await refreshResearchRuns();
    } catch (error) {
      setResearchError(error instanceof Error ? error.message : "研究运行创建失败");
    } finally {
      setResearchPhase("idle");
    }
  };

  const runAgent = async (runId: string) => {
    const requestId = ++agentReportRequestId.current;
    setResearchPhase("running-agent");
    setResearchError(null);
    setAgentReport(null);
    try {
      const report = await runResearchAgent(runId, {
        timeoutSeconds: 180,
        maxSteps: 3,
        maxAttempts: 3,
      });
      if (agentReportRequestId.current === requestId) setAgentReport(report);
      await refreshResearchRuns();
    } catch (error) {
      if (agentReportRequestId.current === requestId) {
        setResearchError(error instanceof Error ? error.message : "Agent 运行失败");
      }
    } finally {
      setResearchPhase("idle");
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

      <div className="grid h-full min-w-0 grid-cols-1 md:grid-cols-[340px_minmax(0,1fr)] xl:grid-cols-[252px_370px_minmax(0,1fr)]">
        <AppSidebar
          activeView={activeView}
          feeds={feeds}
          researchRuns={researchRuns}
          selectedFeedId={selectedFeedId}
          unreadTotal={unreadTotal}
          apiStatus={apiStatus}
          open={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
          onSelectView={selectView}
          onSelectFeed={selectFeed}
          onSelectResearchRun={selectResearchRun}
          onAddFeed={() => openOverlay("add-feed")}
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
              phase={researchPhase}
              error={researchError}
              onCreateRun={createRun}
              onRunAgent={runAgent}
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
                onOpenSidebar={() => setSidebarOpen(true)}
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
