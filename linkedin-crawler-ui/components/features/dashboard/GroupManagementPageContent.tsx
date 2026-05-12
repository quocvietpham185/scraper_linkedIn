"use client";
import React from "react";
import { N8nManagedGroupsSection } from "./N8nManagedGroupsSection";
import { MaterialIcon } from "@/components/ui";
import { useDashboard } from "./dashboard-context";
import { getAllN8nGroups, getAllGroupStatuses } from "@/services/linkedinCrawlerService";
import { normalizeN8nGroupsList, type ManagedGroupRow } from "@/lib/n8n-groups-normalize";

export function GroupManagementPageContent() {
  const { email, dashboardReloadToken } = useDashboard();
  const [rows, setRows] = React.useState<ManagedGroupRow[]>([]);
  const [groupStatuses, setGroupStatuses] = React.useState<Record<string, string>>({});
  const [loading, setLoading] = React.useState(false);

  const loadData = React.useCallback(async () => {
    if (!email) return;
    setLoading(true);
    try {
      // 1. Load groups
      const resGroups = await getAllN8nGroups({ email });
      if (resGroups.success) {
        const list = normalizeN8nGroupsList(resGroups.data?.groups ?? resGroups.data?.parsed);
        setRows(list);
      }
      
      // 2. Load statuses
      const resStats = await getAllGroupStatuses();
      if (resStats.success && resStats.data) {
        const flat: Record<string, string> = {};
        for (const [url, info] of Object.entries(resStats.data)) {
          const normUrl = url.trim().replace(/\/+$/, "");
          flat[normUrl] = (info as any).status;
        }
        setGroupStatuses(flat);
      }
    } catch (err) {
      console.error("Failed to load management data", err);
    } finally {
      setLoading(false);
    }
  }, [email]);

  React.useEffect(() => {
    loadData();
  }, [loadData, dashboardReloadToken]);

  const stats = React.useMemo(() => {
    const total = rows.length;
    const reach = rows.reduce((acc, r) => acc + (r.member || 0), 0);
    
    let liveCount = 0;
    let urgentCount = 0;
    
    rows.forEach(r => {
      const normUrl = r.url_group.trim().replace(/\/+$/, "");
      const s = groupStatuses[normUrl];
      if (s === "live") liveCount++;
      else if (s === "dead" || s === "blocked") urgentCount++;
    });

    return {
      total,
      reach: reach > 1000000 ? (reach / 1000000).toFixed(1) + "M" : reach.toLocaleString("vi-VN"),
      live: liveCount,
      urgent: urgentCount
    };
  }, [rows, groupStatuses]);

  return (
    <div className="flex flex-col gap-xl pb-20 animate-in fade-in duration-500">
      {/* Header */}
      <div className="mb-2">
        <div className="mb-2 flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-primary animate-pulse"></span>
          <span className="text-label-sm font-bold text-primary uppercase tracking-widest">Quản trị tài nguyên</span>
        </div>
        <h1 className="text-h1 font-black text-on-surface tracking-tight">Quản lý nhóm LinkedIn</h1>
        <p className="text-body-lg text-on-surface-variant">
          Quản lý danh sách các nhóm mục tiêu và tối ưu hóa phạm vi tiếp cận của bạn.
        </p>
      </div>

      {/* Stats Summary Bento Grid */}
      <div className="grid grid-cols-1 gap-md sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-2xl border border-outline-variant bg-surface p-lg shadow-sm transition-all hover:shadow-lg hover:shadow-primary/5 hover:border-primary/30 group">
          <div className="flex items-center justify-between mb-4">
             <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10 text-primary group-hover:scale-110 transition-transform">
                <MaterialIcon name="groups" className="text-[28px]" />
             </div>
             <div className="bg-success/10 text-success px-2 py-1 rounded-lg text-[10px] font-black uppercase tracking-widest">
                Stable
             </div>
          </div>
          <p className="text-[11px] font-black uppercase tracking-[0.2em] text-on-surface-variant mb-1">Tổng số nhóm</p>
          <div className="flex items-baseline gap-2">
            <h3 className="text-4xl font-black text-on-surface tracking-tighter">{stats.total}</h3>
            <span className="text-xs font-bold text-success">nhóm</span>
          </div>
        </div>

        <div className="rounded-2xl border border-outline-variant bg-surface p-lg shadow-sm transition-all hover:shadow-lg hover:shadow-success/5 hover:border-success/30 group">
          <div className="flex items-center justify-between mb-4">
             <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-success/10 text-success group-hover:scale-110 transition-transform">
                <MaterialIcon name="person_add" className="text-[28px]" />
             </div>
             <div className="bg-primary/10 text-primary px-2 py-1 rounded-lg text-[10px] font-black uppercase tracking-widest">
                Growth
             </div>
          </div>
          <p className="text-[11px] font-black uppercase tracking-[0.2em] text-on-surface-variant mb-1">Audience Reach</p>
          <div className="flex items-baseline gap-2">
            <h3 className="text-4xl font-black text-on-surface tracking-tighter">{stats.reach}</h3>
            <span className="text-xs font-bold text-success">người</span>
          </div>
        </div>

        <div className="rounded-2xl border border-outline-variant bg-surface p-lg shadow-sm transition-all hover:shadow-lg hover:shadow-warning/5 hover:border-warning/30 group">
          <div className="flex items-center justify-between mb-4">
             <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-warning/10 text-warning group-hover:scale-110 transition-transform">
                <MaterialIcon name="bolt" className="text-[28px]" />
             </div>
             <div className="bg-warning/10 text-warning px-2 py-1 rounded-lg text-[10px] font-black uppercase tracking-widest animate-pulse">
                Active
             </div>
          </div>
          <p className="text-[11px] font-black uppercase tracking-[0.2em] text-on-surface-variant mb-1">Đang cào (Live)</p>
          <div className="flex items-baseline gap-2">
            <h3 className="text-4xl font-black text-on-surface tracking-tighter">{stats.live}</h3>
            <span className="text-xs font-bold text-on-surface-variant">/ {stats.total}</span>
          </div>
        </div>

        <div className="rounded-2xl border border-outline-variant bg-surface p-lg shadow-sm transition-all hover:shadow-lg hover:shadow-error/5 hover:border-error/30 group">
          <div className="flex items-center justify-between mb-4">
             <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-error/10 text-error group-hover:scale-110 transition-transform">
                <MaterialIcon name="security_update_warning" className="text-[28px]" />
             </div>
             <div className="bg-error/10 text-error px-2 py-1 rounded-lg text-[10px] font-black uppercase tracking-widest">
                Urgent
             </div>
          </div>
          <p className="text-[11px] font-black uppercase tracking-[0.2em] text-on-surface-variant mb-1">Cần cập nhật</p>
          <div className="flex items-baseline gap-2">
            <h3 className="text-4xl font-black text-on-surface tracking-tighter">{stats.urgent}</h3>
            <span className="text-xs font-bold text-error">cần check</span>
          </div>
        </div>
      </div>

      <N8nManagedGroupsSection 
        rows={rows}
        groupStatuses={groupStatuses}
        setGroupStatuses={setGroupStatuses}
        loading={loading}
        onRefresh={loadData}
      />
    </div>
  );
}
