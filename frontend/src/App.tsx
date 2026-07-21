import { useState } from "react";

import type { Opportunity, ScannerStatus } from "./types";
import { useScanner } from "./useScanner";

type Tab = "live" | "history" | "status";

const connectionLabel = {
  connecting: "正在连接",
  connected: "实时连接",
  resyncing: "正在重新同步",
  offline: "连接已中断",
};

const closeReasonLabel: Record<string, string> = {
  below_threshold: "跌破收益门槛",
  connection_lost: "深度连接中断",
  leg_skew: "三腿时间偏差过大",
  missing_depth: "深度快照不完整",
  stale_market_data: "行情已过期",
  stale_depth: "深度行情已过期",
  route_unavailable: "路径不可用",
  simulation_rejected: "精确模拟未通过",
  subscription_removed: "候选订阅已移除",
  process_restart: "服务重新启动",
};

function formatTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function formatAge(milliseconds: number): string {
  if (milliseconds < 1_000) return `${milliseconds} ms`;
  if (milliseconds < 60_000) return `${Math.floor(milliseconds / 1_000)} 秒`;
  return `${Math.floor(milliseconds / 60_000)} 分钟`;
}

function signed(value: string, suffix = ""): string {
  return `${value.startsWith("-") ? "" : "+"}${value}${suffix}`;
}

function RoutePath({ opportunity }: { opportunity: Opportunity }) {
  return (
    <span className="route-path" aria-label={`套利路径 ${opportunity.assets.join(" 到 ")}`}>
      {opportunity.assets.map((asset, index) => (
        <span key={`${asset}-${index}`}>
          {index > 0 && <i aria-hidden="true">→</i>}
          <b>{asset}</b>
        </span>
      ))}
    </span>
  );
}

function LegDetails({ opportunity }: { opportunity: Opportunity }) {
  return (
    <div className="opportunity-detail">
      <div className="risk-note">
        <strong>20 档深度已确认 · 仍为预估</strong>
        <span>{opportunity.execution_warning}。三腿非原子成交，成交期间价格可能变化。</span>
      </div>
      <div className="leg-grid">
        {opportunity.legs.map((leg, index) => (
          <article className="leg-card" key={`${leg.symbol}-${index}`}>
            <div className="leg-heading">
              <span>第 {index + 1} 腿</span>
              <strong>{leg.symbol}</strong>
              <em className={`side side--${leg.side.toLowerCase()}`}>{leg.side}</em>
            </div>
            <dl>
              <div><dt>方向</dt><dd>{leg.from_asset} → {leg.to_asset}</dd></div>
              <div><dt>平均成交价</dt><dd>{leg.average_price}</dd></div>
              <div><dt>输入 / 输出</dt><dd>{leg.input_amount} / {leg.output_amount}</dd></div>
              <div><dt>费率 / 手续费</dt><dd>{leg.fee_rate} / {leg.fee_amount}</dd></div>
              <div><dt>余量</dt><dd>{leg.dust_amount}</dd></div>
              <div><dt>消耗深度</dt><dd>{leg.levels_consumed} 档</dd></div>
              <div><dt>交易所时间</dt><dd>{formatTime(leg.source_time)}</dd></div>
              <div><dt>本地接收</dt><dd>{formatTime(leg.received_time)}</dd></div>
            </dl>
          </article>
        ))}
      </div>
      <div className="audit-strip">
        <span>总收益 {signed(opportunity.gross_return_bps, " bps")}</span>
        <span>建模后 {signed(opportunity.modeled_return_bps, " bps")}</span>
        <span>安全缓冲 −{opportunity.safety_buffer_bps} bps</span>
        <span>腿间时差 {opportunity.leg_skew_ms} ms</span>
      </div>
    </div>
  );
}

function OpportunityTable({ opportunities, history = false }: { opportunities: Opportunity[]; history?: boolean }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (opportunities.length === 0) {
    return (
      <div className="empty-state">
        <span aria-hidden="true">△</span>
        <h2>{history ? "尚无已关闭机会" : "正在等待符合门槛的机会"}</h2>
        <p>{history ? "机会生命周期关闭后会保留在这里。" : "仅展示经过 MEXC 20 档深度确认的真实候选，不生成演示数据。"}</p>
      </div>
    );
  }
  return (
    <div className="opportunity-list">
      <div className="table-head" aria-hidden="true">
        <span>路径</span><span>预估净收益</span><span>预估利润</span><span>确认容量</span><span>{history ? "关闭时间" : "行情年龄"}</span><span />
      </div>
      {opportunities.map((opportunity) => {
        const isExpanded = expanded === opportunity.id;
        return (
          <article className={`opportunity ${isExpanded ? "opportunity--expanded" : ""}`} key={opportunity.id}>
            <button className="opportunity-row" type="button" aria-expanded={isExpanded} onClick={() => setExpanded(isExpanded ? null : opportunity.id)}>
              <span data-label="路径"><RoutePath opportunity={opportunity} />{history && <small>{closeReasonLabel[opportunity.close_reason ?? ""] ?? opportunity.close_reason ?? "已关闭"}</small>}</span>
              <span data-label="预估净收益" className="return-value">{signed(opportunity.net_return_bps, " bps")}</span>
              <span data-label="预估利润"><strong>{signed(opportunity.estimated_profit_usdt)}</strong> <small>USDT</small></span>
              <span data-label="确认容量"><strong>{opportunity.confirmed_capacity_usdt}</strong> <small>USDT</small></span>
              <span data-label={history ? "关闭时间" : "行情年龄"}>{history ? formatTime(opportunity.closed_at) : formatAge(opportunity.market_age_ms)}</span>
              <span className="expand-icon" aria-hidden="true">{isExpanded ? "−" : "+"}</span>
            </button>
            {isExpanded && <LegDetails opportunity={opportunity} />}
          </article>
        );
      })}
    </div>
  );
}

function StatusPanel({ status }: { status: ScannerStatus | null }) {
  if (!status) return <div className="empty-state"><h2>等待服务状态</h2><p>连接恢复后会自动显示行情链路详情。</p></div>;
  const age = (value: number | null) => value === null ? "未收到" : formatAge(value);
  return (
    <div className="status-layout">
      <section className="status-card">
        <p className="section-kicker">REST DATA AGE</p>
        <h2>公共 REST 行情</h2>
        <dl className="status-list">
          <div><dt>市场元数据</dt><dd>{age(status.rest_metadata_age_ms)}</dd></div>
          <div><dt>交易所时钟</dt><dd>{age(status.rest_clock_age_ms)}</dd></div>
          <div><dt>全市场报价</dt><dd>{age(status.rest_ticker_age_ms)}</dd></div>
          <div><dt>最近扫描</dt><dd>{formatTime(status.last_scan_at)}</dd></div>
        </dl>
      </section>
      <section className="status-card">
        <p className="section-kicker">DEPTH STREAMS</p>
        <h2>MEXC 深度连接</h2>
        {status.websocket_connections.length === 0 ? <p className="muted">当前没有深度订阅。</p> : (
          <div className="shard-list">
            {status.websocket_connections.map((connection) => (
              <div key={connection.shard_id}>
                <span className={`status-dot status-dot--${connection.state.toLowerCase()}`} />
                <strong>分片 {connection.shard_id + 1}</strong>
                <span>{connection.state}</span>
                <small>{connection.subscription_count} 个市场 · 第 {connection.generation} 代</small>
                {connection.error && <em>{connection.error}</em>}
              </div>
            ))}
          </div>
        )}
      </section>
      <section className="status-card status-card--wide">
        <p className="section-kicker">SCANNER</p>
        <h2>扫描覆盖</h2>
        <div className="coverage-grid">
          <div><strong>{status.market_count}</strong><span>市场</span></div>
          <div><strong>{status.route_count}</strong><span>三角路径</span></div>
          <div><strong>{status.ticker_count}</strong><span>最新报价</span></div>
          <div><strong>{status.depth_book_count}</strong><span>深度簿</span></div>
        </div>
        {status.last_error && <p className="error-message">最近错误：{status.last_error}</p>}
      </section>
    </div>
  );
}

export default function App() {
  const scanner = useScanner();
  const [tab, setTab] = useState<Tab>("live");
  const status = scanner.live.status;
  const degraded = scanner.live.connection !== "connected" || (status !== null && !status.ready);

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Tri-Arb Scanner 首页"><span className="brand-mark" aria-hidden="true">△</span><span>TRI·ARB</span></a>
        <div className="topbar-actions">
          <button className="sound-toggle" type="button" aria-pressed={scanner.soundEnabled} onClick={scanner.toggleSound}>声音 {scanner.soundEnabled ? "开" : "关"}</button>
          <div className={`connection connection--${scanner.live.connection}`} role="status"><span className="connection-dot" />{connectionLabel[scanner.live.connection]}</div>
        </div>
      </header>

      <section className="intro">
        <div><p className="eyebrow">MEXC SPOT · READ ONLY</p><h1>三角套利扫描器</h1><p>全市场广筛，候选路径再用实时订单簿逐腿模拟。所有收益均为预估，不执行交易。</p></div>
        <div className="intro-badge"><span>当前阶段</span><strong>{status?.phase ?? "初始化"}</strong></div>
      </section>

      {degraded && <div className="degraded-banner" role="alert"><strong>{scanner.live.connection === "connected" ? "行情链路尚未就绪" : "实时连接不可用"}</strong><span>{status?.last_error ?? "页面会自动重连并等待服务端完整快照，本地增量不会继续沿用。"}</span></div>}

      <section className="metrics" aria-label="扫描器概览">
        <article><span>实时机会</span><strong>{scanner.live.opportunities.length}</strong><small>20 档深度已确认</small></article>
        <article><span>扫描路径</span><strong>{status?.route_count ?? "—"}</strong><small>USDT 闭环</small></article>
        <article><span>预估门槛</span><strong>{scanner.config?.min_net_return_bps ?? "—"}</strong><small>bps 净收益</small></article>
        <article><span>模拟规模</span><strong>{scanner.config?.notional ?? "—"}</strong><small>{scanner.config?.anchor_asset ?? "USDT"} / 路径</small></article>
      </section>

      <nav className="tabs" aria-label="扫描器页面">
        {(["live", "history", "status"] as const).map((value) => <button key={value} className={tab === value ? "active" : ""} type="button" onClick={() => setTab(value)}>{value === "live" ? `实时机会 ${scanner.live.opportunities.length}` : value === "history" ? "历史记录" : "系统状态"}</button>)}
      </nav>

      <section className="workspace">
        {tab === "live" && <OpportunityTable opportunities={scanner.live.opportunities} />}
        {tab === "history" && <>{scanner.historyError && <p className="inline-error" role="alert">{scanner.historyError}</p>}<OpportunityTable opportunities={scanner.history} history />{scanner.historyCursor && <button className="load-more" type="button" disabled={scanner.historyLoading} onClick={scanner.loadMoreHistory}>{scanner.historyLoading ? "正在加载…" : "加载更多历史"}</button>}</>}
        {tab === "status" && <StatusPanel status={status} />}
      </section>

      <footer><span>只读扫描器 · 不使用 API Key · 不下单</span><strong>三腿非原子成交，不保证利润</strong></footer>
    </main>
  );
}
