import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { QRCodeSVG } from "qrcode.react";
import { api, fmtTime, lastDays, setCsrfToken } from "./api";
import type {
  AuthData,
  CreatedKey,
  Envelope,
  KeyRow,
  Sample,
  SleepRow,
  StatusData,
  SummaryRow,
  SyncJob,
} from "./types";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchInterval: 60_000, retry: 1 } },
});

function Login({ onLogin }: { onLogin: (auth: AuthData) => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await api<Envelope<AuthData>>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ password }),
      });
      setCsrfToken(response.data.csrf_token);
      onLogin(response.data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <h1>mi-health-mcp</h1>
        <p className="muted">管理登录 · 小米健康数据</p>
        <input
          type="password"
          autoFocus
          placeholder="管理员密码"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        {error && <div className="error">{error}</div>}
        <button type="submit" disabled={busy || !password}>
          {busy ? "登录中…" : "登录"}
        </button>
      </form>
    </div>
  );
}

function Badge({ stale, text }: { stale?: boolean; text?: string }) {
  const label = text || (stale ? "stale" : "fresh");
  return <span className={`badge ${stale ? "badge-warn" : "badge-ok"}`}>{label}</span>;
}

function Card({
  title,
  value,
  unit,
  meta,
  footer,
}: {
  title: string;
  value: string;
  unit?: string;
  meta?: string;
  footer?: ReactNode;
}) {
  return (
    <div className="card stat-card">
      <div className="card-title">{title}</div>
      <div className="stat-value">
        {value} {unit && <span className="unit">{unit}</span>}
      </div>
      {meta && <div className="muted small">{meta}</div>}
      {footer}
    </div>
  );
}

function Overview() {
  const range = lastDays(30);
  const summary = useQuery({
    queryKey: ["summary", range.start, range.end],
    queryFn: () =>
      api<Envelope<SummaryRow[]>>(`/api/summary?start_date=${range.start}&end_date=${range.end}`),
  });
  const sleep = useQuery({
    queryKey: ["sleep", range.start, range.end],
    queryFn: () =>
      api<Envelope<SleepRow[]>>(`/api/sleep?start_date=${range.start}&end_date=${range.end}`),
  });
  const spo2 = useQuery({
    queryKey: ["spo2", range.start, range.end],
    queryFn: () =>
      api<Envelope<Sample[]>>(`/api/spo2?start_date=${range.start}&end_date=${range.end}&limit=50`),
  });
  const stress = useQuery({
    queryKey: ["stress", range.start, range.end],
    queryFn: () =>
      api<Envelope<Sample[]>>(`/api/stress?start_date=${range.start}&end_date=${range.end}&limit=50`),
  });
  const status = useQuery({
    queryKey: ["status"],
    queryFn: () => api<Envelope<StatusData>>("/api/status"),
  });

  const latest = summary.data?.data.at(-1);
  const latestSleep = sleep.data?.data.filter((s) => !s.is_nap).at(-1);
  const latestSpo2 = spo2.data?.data.at(-1);
  const latestStress = stress.data?.data.at(-1);
  const summaryMeta = summary.data?.meta;

  return (
    <div className="stack">
      <div className="section-head">
        <div>
          <h2>最近 30 天</h2>
          <div className="muted small">
            最后同步 {fmtTime(summaryMeta?.last_synced_at)} · 数据时间 {fmtTime(summaryMeta?.data_as_of)}
          </div>
        </div>
        <Badge stale={summaryMeta?.stale} />
      </div>

      <div className="grid">
        <Card
          title="步数"
          value={latest ? latest.steps.toLocaleString() : "—"}
          unit="步"
          meta={latest ? `${latest.distance_m.toFixed(0)} m · ${latest.active_kcal.toFixed(0)} kcal` : "暂无数据"}
        />
        <Card
          title="最近睡眠"
          value={latestSleep ? (latestSleep.duration_minutes / 60).toFixed(1) : "—"}
          unit="小时"
          meta={latestSleep ? `HRV ${latestSleep.avg_hrv_ms ?? "—"} ms` : "暂无数据"}
          footer={<Badge stale={sleep.data?.meta.stale} />}
        />
        <Card
          title="血氧"
          value={latestSpo2?.spo2_pct != null ? `${latestSpo2.spo2_pct}%` : "—"}
          meta={latestSpo2 ? fmtTime(latestSpo2.timestamp) : "暂无数据"}
        />
        <Card
          title="压力"
          value={latestStress?.stress_score != null ? String(latestStress.stress_score) : "—"}
          meta={latestStress ? fmtTime(latestStress.timestamp) : "暂无数据"}
        />
      </div>

      <div className="card">
        <div className="card-title">同步状态</div>
        {status.isError && <div className="error">{status.error.message}</div>}
        <table className="table">
          <thead>
            <tr>
              <th>类型</th>
              <th>状态</th>
              <th>数据时间</th>
              <th>最后同步</th>
              <th>错误</th>
            </tr>
          </thead>
          <tbody>
            {(status.data?.data.sync ?? []).map((state) => (
              <tr key={state.data_type}>
                <td>{state.data_type}</td>
                <td>
                  <Badge stale={status.data?.data.stale[state.data_type]} text={state.status} />
                </td>
                <td>{fmtTime(state.last_record_at)}</td>
                <td>{fmtTime(state.last_synced_at)}</td>
                <td className="error small">{state.error || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Charts() {
  const range = lastDays(30);
  const summary = useQuery({
    queryKey: ["summary", range.start, range.end],
    queryFn: () =>
      api<Envelope<SummaryRow[]>>(`/api/summary?start_date=${range.start}&end_date=${range.end}`),
  });
  const sleep = useQuery({
    queryKey: ["sleep", range.start, range.end],
    queryFn: () =>
      api<Envelope<SleepRow[]>>(`/api/sleep?start_date=${range.start}&end_date=${range.end}`),
  });
  const spo2 = useQuery({
    queryKey: ["spo2", range.start, range.end],
    queryFn: () =>
      api<Envelope<Sample[]>>(`/api/spo2?start_date=${range.start}&end_date=${range.end}&limit=100`),
  });
  const stress = useQuery({
    queryKey: ["stress", range.start, range.end],
    queryFn: () =>
      api<Envelope<Sample[]>>(`/api/stress?start_date=${range.start}&end_date=${range.end}&limit=100`),
  });

  const stepsOption = useMemo(() => {
    const rows = summary.data?.data ?? [];
    return {
      tooltip: { trigger: "axis" },
      grid: { left: 50, right: 20, top: 30, bottom: 40 },
      xAxis: { type: "category", data: rows.map((r) => r.date) },
      yAxis: { type: "value" },
      series: [{ type: "bar", name: "步数", data: rows.map((r) => r.steps), itemStyle: { color: "var(--accent)" } }],
    };
  }, [summary.data]);

  const sleepOption = useMemo(() => {
    const rows = (sleep.data?.data ?? []).filter((r) => !r.is_nap);
    return {
      tooltip: { trigger: "axis" },
      legend: { data: ["时长 (h)", "HRV (ms)"], textStyle: { color: "#aaa" } },
      grid: { left: 50, right: 55, top: 40, bottom: 40 },
      xAxis: { type: "category", data: rows.map((r) => r.start_at.slice(0, 10)) },
      yAxis: [
        { type: "value", name: "小时" },
        { type: "value", name: "ms" },
      ],
      series: [
        { type: "bar", name: "时长 (h)", data: rows.map((r) => +(r.duration_minutes / 60).toFixed(2)), itemStyle: { color: "#6c8cff" } },
        { type: "line", name: "HRV (ms)", yAxisIndex: 1, data: rows.map((r) => r.avg_hrv_ms), connectNulls: true, itemStyle: { color: "#42d392" } },
      ],
    };
  }, [sleep.data]);

  const sampleOption = useMemo(() => {
    const spo2Rows = spo2.data?.data ?? [];
    const stressRows = stress.data?.data ?? [];
    return {
      tooltip: { trigger: "axis" },
      legend: { data: ["SpO2", "压力"], textStyle: { color: "#aaa" } },
      grid: { left: 50, right: 20, top: 40, bottom: 50 },
      xAxis: {
        type: "category",
        data: (spo2Rows.length ? spo2Rows : stressRows).map((r) => r.timestamp.slice(5, 16).replace("T", " ")),
      },
      yAxis: { type: "value" },
      series: [
        { type: "line", name: "SpO2", data: spo2Rows.map((r) => r.spo2_pct), itemStyle: { color: "#42d392" } },
        { type: "line", name: "压力", data: stressRows.map((r) => r.stress_score), itemStyle: { color: "#ffb020" } },
      ],
    };
  }, [spo2.data, stress.data]);

  return (
    <div className="stack">
      <h2>趋势</h2>
      <div className="card">
        <div className="card-title">步数</div>
        <ReactECharts option={stepsOption} style={{ height: 280 }} />
      </div>
      <div className="card">
        <div className="card-title">睡眠时长与整晚 HRV</div>
        <ReactECharts option={sleepOption} style={{ height: 300 }} />
      </div>
      <div className="card">
        <div className="card-title">SpO2 / 压力（最近采样）</div>
        <ReactECharts option={sampleOption} style={{ height: 280 }} />
      </div>
    </div>
  );
}

function Keys() {
  const syncClient = useQueryClient();
  const [label, setLabel] = useState("agent");
  const [scopes, setScopes] = useState<string[]>(["read"]);
  const [created, setCreated] = useState<CreatedKey | null>(null);

  const keys = useQuery({
    queryKey: ["keys"],
    queryFn: () => api<Envelope<KeyRow[]>>("/api/keys"),
  });
  const create = useMutation({
    mutationFn: () =>
      api<Envelope<CreatedKey>>("/api/keys", {
        method: "POST",
        body: JSON.stringify({ label, scopes }),
      }),
    onSuccess: (response) => {
      setCreated(response.data);
      syncClient.invalidateQueries({ queryKey: ["keys"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (prefix: string) =>
      api(`/api/keys/${encodeURIComponent(prefix)}`, { method: "DELETE" }),
    onSuccess: () => syncClient.invalidateQueries({ queryKey: ["keys"] }),
  });

  function toggleScope(scope: string) {
    setScopes((current) =>
      current.includes(scope) ? current.filter((item) => item !== scope) : [...current, scope],
    );
  }

  return (
    <div className="stack">
      <h2>Agent API Key</h2>
      <div className="card">
        <div className="row">
          <input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="标签" />
          <label className="check">
            <input type="checkbox" checked={scopes.includes("read")} onChange={() => toggleScope("read")} />
            read
          </label>
          <label className="check">
            <input type="checkbox" checked={scopes.includes("sync")} onChange={() => toggleScope("sync")} />
            sync
          </label>
          <button onClick={() => create.mutate()} disabled={create.isPending || scopes.length === 0}>
            创建 Key
          </button>
        </div>
        {create.isError && <div className="error">{create.error.message}</div>}
        {created && (
          <div className="token-box">
            <div className="muted small">只显示一次：</div>
            <code>{created.token}</code>
          </div>
        )}
      </div>
      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>前缀</th>
              <th>标签</th>
              <th>权限</th>
              <th>创建</th>
              <th>最后使用</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(keys.data?.data ?? []).map((key) => (
              <tr key={key.id}>
                <td><code>{key.prefix}</code></td>
                <td>{key.label || "—"}</td>
                <td>{key.scopes}</td>
                <td>{fmtTime(key.created_at)}</td>
                <td>{fmtTime(key.last_used_at)}</td>
                <td>
                  {key.revoked ? (
                    <Badge stale text="revoked" />
                  ) : (
                    <button className="danger" onClick={() => revoke.mutate(key.prefix)}>
                      吊销
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Sync() {
  const syncClient = useQueryClient();
  const status = useQuery({
    queryKey: ["status"],
    queryFn: () => api<Envelope<StatusData>>("/api/status"),
  });
  const coverage = useQuery({
    queryKey: ["coverage"],
    queryFn: () => api<Envelope<{ data_type: string; first: string | null; last: string | null; count: number }[]>>("/api/coverage"),
  });
  const sync = useMutation({
    mutationFn: () => api<Envelope<SyncJob[]>>("/api/sync", { method: "POST", body: JSON.stringify({}) }),
    onSuccess: () => {
      syncClient.invalidateQueries({ queryKey: ["status"] });
      syncClient.invalidateQueries({ queryKey: ["coverage"] });
    },
  });

  return (
    <div className="stack">
      <div className="section-head">
        <h2>同步</h2>
        <button onClick={() => sync.mutate()} disabled={sync.isPending}>
          {sync.isPending ? "入队中…" : "立即同步"}
        </button>
      </div>
      {sync.isError && <div className="error">{sync.error.message}</div>}
      {sync.data && (
        <div className="card">
          <div className="card-title">已入队</div>
          <table className="table">
            <thead><tr><th>类型</th><th>状态</th><th>任务 ID</th></tr></thead>
            <tbody>
              {sync.data.data.map((job) => (
                <tr key={job.id}><td>{job.data_type}</td><td>{job.status}</td><td><code>{job.id}</code></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="card">
        <div className="card-title">覆盖</div>
        <table className="table">
          <thead><tr><th>类型</th><th>最早</th><th>最新</th><th>条数</th></tr></thead>
          <tbody>
            {(coverage.data?.data ?? []).map((row) => (
              <tr key={row.data_type}>
                <td>{row.data_type}</td><td>{row.first ? row.first.slice(0, 10) : "—"}</td>
                <td>{row.last ? row.last.slice(0, 10) : "—"}</td><td>{row.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card">
        <div className="card-title">状态</div>
        <table className="table">
          <thead><tr><th>类型</th><th>状态</th><th>最后同步</th><th>错误</th></tr></thead>
          <tbody>
            {(status.data?.data.sync ?? []).map((state) => (
              <tr key={state.data_type}>
                <td>{state.data_type}</td><td>{state.status}</td>
                <td>{fmtTime(state.last_synced_at)}</td><td className="error small">{state.error || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function QrModal({ onClose, onConfirmed }: { onClose: () => void; onConfirmed: () => void }) {
  const [token, setToken] = useState<string | null>(null);
  const [loginUrl, setLoginUrl] = useState("");
  const [status, setStatus] = useState("准备中…");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api<Envelope<{ qr_token: string; login_url: string; expires_in: number }>>("/api/auth/qr/start", {
      method: "POST",
    })
      .then((response) => {
        if (cancelled) return;
        setToken(response.data.qr_token);
        setLoginUrl(response.data.login_url);
        setStatus("等待扫码…");
      })
      .catch((err) => setError(err.message));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!token) return;
    const timer = setInterval(async () => {
      try {
        const response = await api<Envelope<{ status: string; user_id?: string }>>(
          `/api/auth/qr/poll?token=${encodeURIComponent(token)}`,
        );
        const next = response.data.status;
        setStatus(next);
        if (next === "confirmed") {
          clearInterval(timer);
          onConfirmed();
          onClose();
        } else if (next === "expired") {
          clearInterval(timer);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "轮询失败");
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [token]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <h3>扫码登录小米账号</h3>
        {error && <div className="error">{error}</div>}
        <div className="qr-wrap">
          {loginUrl ? <QRCodeSVG value={loginUrl} size={220} /> : <div className="muted">生成中…</div>}
        </div>
        <div className="muted small">状态：{status}</div>
        <div className="row">
          <button onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}

function Shell({ auth, onLogout }: { auth: AuthData; onLogout: () => void }) {
  const [tab, setTab] = useState("overview");
  const [qrOpen, setQrOpen] = useState(false);
  const syncClient = useQueryClient();
  const status = useQuery({
    queryKey: ["status"],
    queryFn: () => api<Envelope<StatusData>>("/api/status"),
  });

  const tabs = [
    ["overview", "概览"],
    ["charts", "趋势"],
    ["keys", "Key"],
    ["sync", "同步"],
  ] as const;

  return (
    <div className="app">
      <header className="header">
        <div>
          <strong>mi-health-mcp</strong>
          <span className="muted small">
            {status.data?.data.user_id || "未配置账号"} · {status.data?.data.region || "cn"}
          </span>
        </div>
        <nav className="tabs">
          {tabs.map(([id, label]) => (
            <button key={id} className={tab === id ? "tab active" : "tab"} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </nav>
        <div className="row">
          <button onClick={() => setQrOpen(true)}>扫码登录</button>
          <button className="ghost" onClick={onLogout}>退出</button>
        </div>
      </header>
      <main className="main">
        {tab === "overview" && <Overview />}
        {tab === "charts" && <Charts />}
        {tab === "keys" && <Keys />}
        {tab === "sync" && <Sync />}
      </main>
      {qrOpen && (
        <QrModal
          onClose={() => setQrOpen(false)}
          onConfirmed={() => {
            syncClient.invalidateQueries();
          }}
        />
      )}
    </div>
  );
}

export default function App() {
  const [auth, setAuth] = useState<AuthData | null>(null);

  useEffect(() => {
    api<Envelope<AuthData>>("/api/auth/me")
      .then((response) => {
        setCsrfToken(response.data.csrf_token);
        setAuth(response.data);
      })
      .catch(() => setAuth({ authenticated: false, csrf_token: null }));
  }, []);

  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    setCsrfToken(null);
    setAuth({ authenticated: false, csrf_token: null });
  }

  if (!auth) return <div className="login-page muted">加载中…</div>;
  if (!auth.authenticated) {
    return (
      <QueryClientProvider client={queryClient}>
        <Login onLogin={setAuth} />
      </QueryClientProvider>
    );
  }
  return (
    <QueryClientProvider client={queryClient}>
      <Shell auth={auth} onLogout={logout} />
    </QueryClientProvider>
  );
}
