import { useCallback, useEffect, useMemo, useState } from "react";
import { Outlet, useLocation, useMatch, useNavigate, useSearchParams } from "react-router-dom";

import { feedPath, viewFromPath, viewPaths, viewTitles } from "../app/navigation";
import { AddFeedDialog } from "../components/AddFeedDialog";
import { AppSidebar } from "../components/AppSidebar";
import { ArticleList } from "../components/ArticleList";
import { AskPanel } from "../components/AskPanel";
import { ReaderPane } from "../components/ReaderPane";
import { addFeed as createFeed, getArticles, getFeeds } from "../api/client";
import { initialArticles, initialFeeds, researchRuns } from "../data/localState";
import type { Feed, LibraryView } from "../types";

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

export function ReaderWorkspace() {
  const location = useLocation();
  const navigate = useNavigate();
  const feedMatch = useMatch("/feeds/:feedId");
  const [searchParams] = useSearchParams();
  const [feeds, setFeeds] = useState<Feed[]>(initialFeeds);
  const [articles, setArticles] = useState(initialArticles);
  const [searchQuery, setSearchQuery] = useState("");
  const [savedIds, setSavedIds] = useState(
    () => new Set(initialArticles.filter(article => article.starred).map(article => article.id)),
  );
  const [readIds, setReadIds] = useState(
    () => new Set(initialArticles.filter(article => !article.unread).map(article => article.id)),
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [apiStatus, setApiStatus] = useState<ApiStatus>("connecting");

  useEffect(() => {
    void Promise.all([getFeeds(), getArticles()])
      .then(([loadedFeeds, loadedArticles]) => {
        setFeeds(loadedFeeds);
        setArticles(bindArticleSources(loadedArticles, loadedFeeds));
        setReadIds(new Set());
        setApiStatus("connected");
      })
      .catch(() => {
        setApiStatus("unavailable");
      });
  }, []);

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

  const selectArticle = (articleId: string) => {
    setReadIds(current => new Set(current).add(articleId));
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
    setSavedIds(current => {
      const next = new Set(current);
      if (next.has(articleId)) next.delete(articleId);
      else next.add(articleId);
      return next;
    });
  };

  const markAllRead = () => {
    setReadIds(current => {
      const next = new Set(current);
      visibleArticles.forEach(article => next.add(article.id));
      return next;
    });
  };

  const addFeed = async (input: { url: string; title?: string }) => {
    const feed = await createFeed(input);
    const [loadedFeeds, loadedArticles] = await Promise.all([getFeeds(), getArticles()]);
    setFeeds(loadedFeeds.some(item => item.id === feed.id) ? loadedFeeds : [...loadedFeeds, feed]);
    setArticles(bindArticleSources(loadedArticles, loadedFeeds));
    setApiStatus("connected");
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
    <div className="h-dvh min-h-[600px] overflow-hidden bg-[#f2f2ef] text-[#242528]">
      <Outlet />
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
          onAddFeed={() => openOverlay("add-feed")}
        />

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
              }
            }}
            onAsk={() => openOverlay("ask")}
          />
        </div>
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
