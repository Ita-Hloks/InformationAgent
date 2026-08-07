import { useEffect, useMemo, useState } from "react";

import { AddFeedDialog } from "./components/AddFeedDialog";
import { AppSidebar } from "./components/AppSidebar";
import { ArticleList } from "./components/ArticleList";
import { AskPanel } from "./components/AskPanel";
import { ReaderPane } from "./components/ReaderPane";
import { initialArticles, initialFeeds, researchRuns } from "./data/mockData";
import type { Feed, LibraryView, MobilePane } from "./types";

const viewTitles: Record<LibraryView, string> = {
  inbox: "收件箱",
  today: "今天",
  all: "全部文章",
  saved: "已收藏",
  research: "研究资料",
};

function App() {
  const [activeView, setActiveView] = useState<LibraryView>("inbox");
  const [selectedFeedId, setSelectedFeedId] = useState<string | null>(null);
  const [selectedArticleId, setSelectedArticleId] = useState(initialArticles[0].id);
  const [feeds, setFeeds] = useState<Feed[]>(initialFeeds);
  const [searchQuery, setSearchQuery] = useState("");
  const [savedIds, setSavedIds] = useState(
    () => new Set(initialArticles.filter(article => article.starred).map(article => article.id)),
  );
  const [readIds, setReadIds] = useState(
    () => new Set(initialArticles.filter(article => !article.unread).map(article => article.id)),
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [addFeedOpen, setAddFeedOpen] = useState(false);
  const [mobilePane, setMobilePane] = useState<MobilePane>("list");

  const visibleArticles = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();
    return initialArticles
      .filter(article => {
        if (selectedFeedId) return article.feedId === selectedFeedId;
        if (activeView === "inbox") {
          return !readIds.has(article.id) || article.id === selectedArticleId;
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
  }, [activeView, readIds, savedIds, searchQuery, selectedArticleId, selectedFeedId]);

  useEffect(() => {
    if (
      visibleArticles.length > 0 &&
      !visibleArticles.some(article => article.id === selectedArticleId)
    ) {
      setSelectedArticleId(visibleArticles[0].id);
    }
  }, [selectedArticleId, visibleArticles]);

  const selectedArticle =
    initialArticles.find(article => article.id === selectedArticleId) ?? initialArticles[0];
  const selectedFeed = feeds.find(feed => feed.id === selectedFeedId);
  const listTitle = selectedFeed?.name ?? viewTitles[activeView];
  const unreadTotal = initialArticles.filter(article => !readIds.has(article.id)).length;

  const selectView = (view: LibraryView) => {
    setActiveView(view);
    setSelectedFeedId(null);
    setSearchQuery("");
    setMobilePane("list");
  };

  const selectFeed = (feedId: string) => {
    setSelectedFeedId(feedId);
    setActiveView("all");
    setSearchQuery("");
    setMobilePane("list");
  };

  const selectArticle = (articleId: string) => {
    setSelectedArticleId(articleId);
    setReadIds(current => new Set(current).add(articleId));
    setMobilePane("reader");
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

  const addFeed = (feed: Feed) => {
    setFeeds(current => (current.some(item => item.id === feed.id) ? current : [...current, feed]));
  };

  return (
    <div className="h-dvh min-h-[600px] overflow-hidden bg-[#f2f2ef] text-[#242528]">
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
          open={sidebarOpen}
          onClose={() => setSidebarOpen(false)}
          onSelectView={selectView}
          onSelectFeed={selectFeed}
          onAddFeed={() => setAddFeedOpen(true)}
        />

        <div className={`min-h-0 min-w-0 ${mobilePane === "list" ? "block" : "hidden"} md:block`}>
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

        <div className={`min-h-0 min-w-0 ${mobilePane === "reader" ? "block" : "hidden"} md:block`}>
          <ReaderPane
            article={selectedArticle}
            saved={savedIds.has(selectedArticle.id)}
            read={readIds.has(selectedArticle.id)}
            onBack={() => setMobilePane("list")}
            onToggleSaved={() => toggleSaved(selectedArticle.id)}
            onMarkRead={() => setReadIds(current => new Set(current).add(selectedArticle.id))}
            onAsk={() => setAskOpen(true)}
          />
        </div>
      </div>

      <AskPanel article={selectedArticle} open={askOpen} onClose={() => setAskOpen(false)} />
      <AddFeedDialog
        open={addFeedOpen}
        feeds={feeds}
        onAddFeed={addFeed}
        onClose={() => setAddFeedOpen(false)}
      />
    </div>
  );
}

export default App;
