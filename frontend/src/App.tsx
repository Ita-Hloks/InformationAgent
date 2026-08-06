import { useState } from "react";

type ViewKey = "workspace" | "history";

const navigation: Array<{ key: ViewKey; label: string }> = [
  { key: "workspace", label: "工作台" },
  { key: "history", label: "分析记录" },
];

const viewCopy: Record<ViewKey, { eyebrow: string; title: string; description: string }> = {
  workspace: {
    eyebrow: "LOCAL WORKSPACE",
    title: "分析工作台",
    description: "当前工作区没有待处理的分析运行",
  },
  history: {
    eyebrow: "ANALYSIS HISTORY",
    title: "分析记录",
    description: "当前没有可展示的分析记录",
  },
};

function App() {
  const [activeView, setActiveView] = useState<ViewKey>("workspace");
  const currentView = viewCopy[activeView];

  return (
    <div className="min-h-screen bg-[#f3f6f5] text-[#14201e] md:flex">
      <aside className="border-b border-[#dbe4e1] bg-[#e8efed] md:min-h-screen md:w-64 md:shrink-0 md:border-b-0 md:border-r">
        <div className="flex items-center gap-3 px-5 py-5 md:block md:px-6 md:py-7">
          <div className="flex size-10 items-center justify-center rounded-xl bg-[#143c35] text-sm font-bold tracking-[0.08em] text-[#e7f4ef]">
            IA
          </div>
          <div className="md:mt-5">
            <p className="text-[0.68rem] font-semibold tracking-[0.18em] text-[#55716a]">
              INFORMATION AGENT
            </p>
            <h1 className="mt-2 text-lg font-semibold tracking-[-0.02em]">研究工作台</h1>
          </div>
        </div>

        <nav aria-label="主导航" className="flex gap-2 overflow-x-auto px-4 pb-4 md:block md:px-3">
          {navigation.map(item => (
            <button
              key={item.key}
              type="button"
              aria-current={activeView === item.key ? "page" : undefined}
              className={`min-w-28 rounded-lg px-3 py-2.5 text-left text-sm font-medium transition-colors md:mb-1 md:w-full ${
                activeView === item.key
                  ? "bg-[#143c35] text-white shadow-[0_5px_14px_rgba(20,60,53,0.14)]"
                  : "text-[#4f6962] hover:bg-[#dce8e4] hover:text-[#143c35]"
              }`}
              onClick={() => setActiveView(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <div className="hidden border-t border-[#d3dfdb] px-6 py-5 md:block">
          <p className="text-xs font-medium text-[#55716a]">本地模式</p>
          <p className="mt-1 text-xs leading-5 text-[#71857f]">等待数据连接</p>
        </div>
      </aside>

      <main className="min-w-0 flex-1">
        <header className="border-b border-[#dbe4e1] bg-[#f7faf9] px-5 py-4 sm:px-8 sm:py-5">
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-4">
            <div>
              <p className="text-xs font-medium text-[#71857f]">当前视图</p>
              <h2 className="mt-1 text-base font-semibold text-[#24332f]">{currentView.title}</h2>
            </div>
            <span className="rounded-full border border-[#bed5cb] bg-[#edf7f2] px-3 py-1 text-xs font-medium text-[#2c6a57]">
              未连接
            </span>
          </div>
        </header>

        <div className="mx-auto max-w-6xl px-5 py-8 sm:px-8 sm:py-10">
          <section aria-labelledby="page-title">
            <p className="text-xs font-semibold tracking-[0.18em] text-[#78918a]">
              {currentView.eyebrow}
            </p>
            <h3
              id="page-title"
              className="mt-3 text-2xl font-semibold tracking-[-0.03em] text-[#14201e]"
            >
              {currentView.title}
            </h3>
          </section>

          <section className="mt-8 flex min-h-[390px] items-center border border-[#dbe4e1] bg-white px-6 py-12 shadow-[0_12px_30px_rgba(20,60,53,0.04)] sm:px-12">
            <div className="max-w-md">
              <div className="flex size-12 items-center justify-center rounded-xl border border-[#c9dcd5] bg-[#f2f8f5] text-sm font-bold tracking-[0.08em] text-[#2c6a57]">
                IA
              </div>
              <h4 className="mt-7 text-xl font-semibold tracking-[-0.02em] text-[#24332f]">
                {currentView.description}
              </h4>
              <p className="mt-3 text-sm leading-6 text-[#71857f]">前端框架已就绪</p>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}

export default App;
