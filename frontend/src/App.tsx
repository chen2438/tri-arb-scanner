import { useEffect, useState } from "react";

type ConnectionState = "checking" | "online" | "offline";

const stateLabel: Record<ConnectionState, string> = {
  checking: "正在连接本地服务",
  online: "本地服务在线",
  offline: "本地服务未连接",
};

export default function App() {
  const [connection, setConnection] = useState<ConnectionState>("checking");

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/health/live", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`health request failed: ${response.status}`);
        }
        setConnection("online");
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") {
          return;
        }
        setConnection("offline");
      });
    return () => controller.abort();
  }, []);

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Tri-Arb Scanner 首页">
          <span className="brand-mark" aria-hidden="true">
            △
          </span>
          <span>TRI·ARB</span>
        </a>
        <div className={`connection connection--${connection}`} role="status">
          <span className="connection-dot" />
          {stateLabel[connection]}
        </div>
      </header>

      <section className="hero">
        <p className="eyebrow">MEXC SPOT · READ ONLY</p>
        <h1>
          找到价差之前，
          <span>先证明它真实存在。</span>
        </h1>
        <p className="hero-copy">
          全市场广筛与三腿 20 档深度确认将运行在同一条可审计链路中。当前工程骨架已就绪，
          行情与机会计算将在后续里程碑接入。
        </p>
      </section>

      <section className="metrics" aria-label="扫描器默认配置">
        <article>
          <span>锚定资产</span>
          <strong>USDT</strong>
          <small>固定闭环起点</small>
        </article>
        <article>
          <span>模拟规模</span>
          <strong>100</strong>
          <small>USDT / 路径</small>
        </article>
        <article>
          <span>机会门槛</span>
          <strong>20</strong>
          <small>bps 净收益</small>
        </article>
        <article>
          <span>确认深度</span>
          <strong>20</strong>
          <small>档 / 交易对</small>
        </article>
      </section>

      <section className="empty-panel">
        <div>
          <p className="panel-kicker">LIVE OPPORTUNITIES</p>
          <h2>行情管线尚未接入</h2>
          <p>这里不会显示演示或伪造机会。深度确认功能完成后，真实候选会自动出现在此处。</p>
        </div>
        <span className="phase-chip">里程碑 1 / 6</span>
      </section>
    </main>
  );
}
