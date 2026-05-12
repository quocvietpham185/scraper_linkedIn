"use client";

import React, { useEffect, useState, useCallback } from "react";
import { MaterialIcon } from "@/components/ui";
import { useDashboard } from "./dashboard-context";
import { 
  getKpiStatus, 
  reportKpiTask, 
  addPersonalTask,
  updatePersonalTask,
  deletePersonalTask,
  getSeedingTasks,
  updateSeedingStatus,
  verifySeedingTask,
  reportSeedingTask,
  lockSeedingComments,
  SeedingTask
} from "@/services/linkedinCrawlerService";

type TabType = "overview" | "team" | "seeding" | "cost" | "skills" | "tasks";

export function KpiDashboardContent() {
  const { email } = useDashboard();
  const [activeTab, setActiveTab] = useState<TabType>("overview");
  const [kpiData, setKpiData] = useState<any>(null);
  const [seedingTasks, setSeedingTasks] = useState<SeedingTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingSeeding, setLoadingSeeding] = useState(false);
  const [reportingUrl, setReportingUrl] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!email) return;
    setLoading(true);
    try {
      const kpiRes = await getKpiStatus(email);
      setKpiData(kpiRes.data);
    } catch (error) {
      console.error("Failed to load KPI data", error);
    } finally {
      setLoading(false);
    }
  }, [email]);

  const loadSeedingTasks = useCallback(async () => {
    if (!email) return;
    setLoadingSeeding(true);
    try {
      const res = await getSeedingTasks(email);
      setSeedingTasks(res.data || []);
    } catch (error) {
      console.error("Failed to load seeding tasks", error);
    } finally {
      setLoadingSeeding(false);
    }
  }, [email]);

  useEffect(() => {
    loadData();
    if (activeTab === "seeding") {
      loadSeedingTasks();
    }
    const interval = setInterval(() => {
      loadData();
      if (activeTab === "seeding") loadSeedingTasks();
    }, 60000);
    return () => clearInterval(interval);
  }, [loadData, loadSeedingTasks, activeTab]);

  const handleReport = async (postUrl: string) => {
    if (!email) return;
    setReportingUrl(postUrl);
    try {
      await reportKpiTask(email, postUrl);
      await loadData();
    } catch (error) {
      console.error("Report error", error);
    } finally {
      setReportingUrl(null);
    }
  };

  if (!email) {
    return (
      <div className="flex h-96 items-center justify-center rounded-2xl bg-surface border-2 border-dashed border-outline-variant">
        <div className="text-center">
          <div className="mx-auto mb-4 flex h-16 w-16 items-center justify-center rounded-full bg-surface-container-high">
            <MaterialIcon name="lock" className="text-3xl text-on-surface-variant" />
          </div>
          <h3 className="text-h3 text-on-surface font-bold">Yêu cầu đăng nhập</h3>
          <p className="text-body-md text-on-surface-variant mt-2">
            Vui lòng đăng nhập LinkedIn để truy cập hệ thống KPI Ops Center.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-xl pb-20">
      {/* Header & Tabs */}
      <div className="flex flex-col gap-lg md:flex-row md:items-end md:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-success animate-pulse"></span>
            <span className="text-label-sm font-bold text-success uppercase tracking-widest">Hệ thống đang hoạt động</span>
          </div>
          <h1 className="text-h1 font-black text-on-surface tracking-tight">CEO AI Ops Center</h1>
          <p className="text-body-lg text-on-surface-variant">Quản trị hiệu suất và năng suất AI toàn team</p>
        </div>
        
        <div className="flex gap-1 rounded-xl bg-surface-container-high p-1">
          {(["overview", "team", "seeding", "tasks", "cost", "skills"] as TabType[]).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`rounded-lg px-md py-2 text-xs font-bold uppercase tracking-wider transition-all ${
                activeTab === tab 
                ? "bg-primary text-on-primary shadow-md" 
                : "text-on-surface-variant hover:bg-surface-container-lowest"
              }`}
            >
              {tab === "overview" && "📊 Tổng quan"}
              {tab === "team" && "👥 Team"}
              {tab === "seeding" && "🌱 Seeding"}
              {tab === "tasks" && "📝 My Tasks"}
              {tab === "cost" && "💰 Chi phí"}
              {tab === "skills" && "💡 Skills"}
            </button>
          ))}
        </div>
      </div>

      {activeTab === "overview" && (
        <KpiOverviewTab 
          data={kpiData} 
          onReport={handleReport} 
          reportingUrl={reportingUrl} 
        />
      )}
      {activeTab === "team" && <TeamStatsTab />}
      {activeTab === "seeding" && (
        <SeedingTasksTab 
          tasks={seedingTasks} 
          loading={loadingSeeding} 
          onRefresh={loadSeedingTasks} 
        />
      )}
      {activeTab === "cost" && <CostEfficiencyTab />}
      {activeTab === "skills" && <TeamSkillsTab />}
      {activeTab === "tasks" && (
        <MyTasksTab tasks={kpiData?.personal_tasks || []} onRefresh={loadData} />
      )}
    </div>
  );
}

// --- Sub-Components for Tabs ---

function KpiOverviewTab({ data, onReport, reportingUrl }: any) {
  const dailyGoal = data?.daily_goal || 20;
  const verifiedToday = data?.verified_today || 0;
  const progressPercent = Math.min(100, Math.round((verifiedToday / dailyGoal) * 100));

  return (
    <div className="flex flex-col gap-xl animate-in fade-in duration-500">
      {/* KPI Bento Grid */}
      <div className="grid grid-cols-1 gap-md sm:grid-cols-2 lg:grid-cols-5">
        <KpiCard 
          label="Tổng bài đã cào" 
          value={data?.total_posts || 1240} 
          sub="↑ 12% vs tuần trước" 
          color="#4f46e5" 
          trend="up" 
        />
        <KpiCard 
          label="Tỷ lệ thành công" 
          value="94.2%" 
          sub="Duy trì ổn định" 
          color="#10b981" 
          trend="neutral" 
        />
        <KpiCard 
          label="Bài đã Seed (Hôm nay)" 
          value={`${verifiedToday}/${dailyGoal}`} 
          sub={`${dailyGoal - verifiedToday} bài cần hoàn thành`} 
          color="#f59e0b" 
          trend={progressPercent > 50 ? "up" : "down"} 
        />
        <KpiCard 
          label="Productivity Score" 
          value="8.4" 
          sub="↑ 0.6 điểm vs Q1" 
          color="#8b5cf6" 
          trend="up" 
        />
        <KpiCard 
          label="Nhiệm vụ chờ" 
          value={data?.tasks?.filter((t:any) => t.status === "pending").length || 0} 
          sub="Cần xử lý ngay" 
          color="#06b6d4" 
          trend="neutral" 
        />
      </div>

      <div className="grid grid-cols-1 gap-xl lg:grid-cols-3">
        {/* Main Column */}
        <div className="lg:col-span-2 flex flex-col gap-xl">
          {/* Recent Tasks */}
          <div className="rounded-2xl border border-outline-variant bg-surface p-lg shadow-sm">
            <h3 className="mb-lg text-title-lg font-bold text-on-surface uppercase tracking-tight">Lịch sử nhiệm vụ gần đây</h3>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left">
                <thead>
                  <tr className="border-b border-outline-variant">
                    <th className="pb-md text-xs font-bold text-on-surface-variant uppercase tracking-wider">Trạng thái</th>
                    <th className="pb-md text-xs font-bold text-on-surface-variant uppercase tracking-wider">URL Bài viết</th>
                    <th className="pb-md text-xs font-bold text-on-surface-variant uppercase tracking-wider">Thời gian</th>
                    <th className="pb-md text-right text-xs font-bold text-on-surface-variant uppercase tracking-wider">Hành động</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-outline-variant/30">
                  {(data?.tasks || []).slice(0, 15).map((task: any, idx: number) => (
                    <tr key={idx} className="group hover:bg-surface-container-low transition-colors">
                      <td className="py-md">
                        <StatusBadge status={task.status} />
                      </td>
                      <td className="py-md">
                        <div className="max-w-xs truncate text-sm font-medium text-on-surface">
                          {task.post_url}
                        </div>
                      </td>
                      <td className="py-md text-sm text-on-surface-variant">
                        {task.reported_at ? new Date(task.reported_at).toLocaleTimeString() : "N/A"}
                      </td>
                      <td className="py-md text-right">
                        <a href={task.post_url} target="_blank" className="text-on-surface-variant hover:text-primary transition-colors">
                          <MaterialIcon name="open_in_new" className="text-[18px]" />
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Sidebar Column */}
        <div className="flex flex-col gap-xl">
          {/* Daily Progress */}
          <div className="rounded-2xl border border-outline-variant bg-surface p-lg shadow-sm">
            <h3 className="mb-lg text-title-lg font-bold text-on-surface uppercase tracking-tight">Tiến độ hôm nay</h3>
            <div className="flex flex-col gap-md">
              <div className="flex items-center justify-between">
                <span className="text-body-md text-on-surface-variant">Cá nhân</span>
                <span className="text-title-md font-bold text-on-surface">{progressPercent}%</span>
              </div>
              <div className="h-2 w-full rounded-full bg-surface-container-high overflow-hidden">
                <div className="h-full bg-primary transition-all duration-1000" style={{ width: `${progressPercent}%` }} />
              </div>
              <div className="flex items-center justify-between text-xs text-on-surface-variant">
                <span>{verifiedToday} đã hoàn thành</span>
                <span>Mục tiêu: {dailyGoal}</span>
              </div>
            </div>
            
            <div className="mt-xl rounded-xl bg-primary/5 p-md border border-primary/10">
              <div className="flex gap-sm text-primary mb-2">
                <MaterialIcon name="auto_awesome" className="text-[18px]" />
                <span className="text-xs font-bold uppercase">Gợi ý từ AI</span>
              </div>
              <p className="text-xs leading-relaxed text-on-surface-variant">
                Dựa trên dữ liệu cào sáng nay, <strong>Marketing team</strong> đang thiếu bài ở các nhóm Công nghệ. Hãy tập trung vào các nhóm đã lên lịch.
              </p>
            </div>
          </div>

          {/* Alerts & Notifications */}
          <div className="rounded-2xl border border-outline-variant bg-surface p-lg shadow-sm">
            <h3 className="mb-lg text-title-lg font-bold text-on-surface uppercase tracking-tight">Cảnh báo & Gợi ý</h3>
            <div className="flex flex-col gap-md">
              <AlertItem 
                type="warn" 
                icon="warning" 
                title="Thiếu Token" 
                desc="Email vietpq... sắp đạt giới hạn cào hàng ngày. Cân nhắc đổi tài khoản dự phòng." 
              />
              <AlertItem 
                type="info" 
                icon="lightbulb" 
                title="Xu hướng mới" 
                desc="Phát hiện nhiều bài viết về AI Agent đang viral mạnh trong Group React Vietnam." 
              />
              <AlertItem 
                type="success" 
                icon="check_circle" 
                title="Tối ưu chi phí" 
                desc="Hệ thống đã tự động chuyển sang Apify Low-cost mode để tiết kiệm chi phí." 
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function TeamStatsTab() {
  return (
    <div className="flex flex-col gap-xl animate-in slide-in-from-bottom-4 duration-500">
      <div className="rounded-2xl border border-outline-variant bg-surface p-lg shadow-sm">
        <h3 className="mb-lg text-title-lg font-bold text-on-surface uppercase tracking-tight">Hiệu suất cào dữ liệu theo thành viên</h3>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-outline-variant">
                <th className="pb-md text-xs font-bold text-on-surface-variant uppercase">Thành viên</th>
                <th className="pb-md text-xs font-bold text-on-surface-variant uppercase">Phòng ban</th>
                <th className="pb-md text-xs font-bold text-on-surface-variant uppercase">Bài đã cào</th>
                <th className="pb-md text-xs font-bold text-on-surface-variant uppercase">Success Rate</th>
                <th className="pb-md text-xs font-bold text-on-surface-variant uppercase">Điểm HP</th>
                <th className="pb-md text-right text-xs font-bold text-on-surface-variant uppercase">Trạng thái</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-outline-variant/30">
              <TeamRow name="Viet PQ" dept="Founder" count={850} rate="98%" score="9.8" color="#6366f1" status="green" />
              <TeamRow name="Linh Nguyễn" dept="Marketing" count={420} rate="92%" score="8.5" color="#10b981" status="green" />
              <TeamRow name="Huy Hoàng" dept="Tech" count={610} rate="85%" score="9.2" color="#f59e0b" status="yellow" />
              <TeamRow name="Phương Anh" dept="Content" count={315} rate="95%" score="8.0" color="#8b5cf6" status="green" />
              <TeamRow name="Minh Tú" dept="Sales" count={120} rate="40%" score="4.2" color="#f43f5e" status="red" />
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function CostEfficiencyTab() {
  return (
    <div className="flex flex-col gap-xl animate-in slide-in-from-bottom-4 duration-500">
       <div className="grid grid-cols-1 gap-md lg:grid-cols-2">
         <div className="rounded-2xl border border-outline-variant bg-surface p-lg">
            <h3 className="mb-lg text-title-lg font-bold text-on-surface uppercase tracking-tight">Chi phí API & Proxy</h3>
            <div className="flex flex-col gap-lg">
              <div className="flex items-end justify-between">
                <div>
                  <p className="text-body-sm text-on-surface-variant">Tổng chi tiêu tháng này</p>
                  <h2 className="text-display-sm font-black text-on-surface">$24.50</h2>
                </div>
                <span className="text-xs font-bold text-success bg-success/10 px-sm py-1 rounded-full">↓ 12% so với dự báo</span>
              </div>
              <div className="flex flex-col gap-sm">
                <CostBar label="Apify API" value={18.2} total={30} color="#6366f1" />
                <CostBar label="LinkedIn Proxy" value={4.3} total={30} color="#10b981" />
                <CostBar label="Cloud Compute" value={2.0} total={30} color="#f59e0b" />
              </div>
            </div>
         </div>
         <div className="rounded-2xl border border-outline-variant bg-surface p-lg">
            <h3 className="mb-lg text-title-lg font-bold text-on-surface uppercase tracking-tight">Đề xuất tối ưu</h3>
            <div className="flex flex-col gap-md">
               <div className="p-md rounded-xl border border-outline-variant hover:border-primary/30 transition-all cursor-pointer">
                  <h4 className="text-sm font-bold text-on-surface mb-1">🔄 Thay đổi Model</h4>
                  <p className="text-xs text-on-surface-variant">Chuyển sang Apify Actor "No Cookies" giúp giảm 30% chi phí cào mỗi bài.</p>
               </div>
               <div className="p-md rounded-xl border border-outline-variant hover:border-primary/30 transition-all cursor-pointer">
                  <h4 className="text-sm font-bold text-on-surface mb-1">♻️ Tái sử dụng Session</h4>
                  <p className="text-xs text-on-surface-variant">Tăng thời gian sống của session lên 24h giúp giảm số lần login OTP.</p>
               </div>
            </div>
         </div>
       </div>
    </div>
  );
}

function TeamSkillsTab() {
  return (
    <div className="flex flex-col gap-xl animate-in slide-in-from-bottom-4 duration-500">
       <div className="grid grid-cols-1 gap-md md:grid-cols-2 lg:grid-cols-4">
          <SkillCard icon="auto_fix_high" title="AI Rewriter" count={128} desc="Viết lại bài LinkedIn viral" />
          <SkillCard icon="translate" title="Multi-Language" count={56} desc="Dịch và localize content" />
          <SkillCard icon="analytics" title="Data Analyzer" count={89} desc="Phân tích insight từ comment" />
          <SkillCard icon="psychology" title="CEO Mindset" count={42} desc="Prompt tư duy chiến lược" />
       </div>
    </div>
  );
}

// --- Helper Components ---

function KpiCard({ label, value, sub, color, trend }: { label: string, value: any, sub: string, color: string, trend: "up" | "down" | "neutral" }) {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-outline-variant bg-surface p-lg shadow-sm transition-all hover:border-primary/30 group">
      <div className="absolute top-0 left-0 h-full w-1.5" style={{ backgroundColor: color }}></div>
      <p className="text-[10px] font-black uppercase tracking-widest text-on-surface-variant mb-2">{label}</p>
      <h3 className="text-h3 font-black text-on-surface leading-none">{value}</h3>
      <p className={`mt-2 flex items-center gap-1 text-[10px] font-bold ${
        trend === "up" ? "text-success" : trend === "down" ? "text-error" : "text-on-surface-variant"
      }`}>
        {trend === "up" && "↑"} {trend === "down" && "↓"} {sub}
      </p>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const config: any = {
    pending: { label: "Đang chờ", bg: "bg-warning/10", text: "text-warning", icon: "hourglass_empty" },
    verified: { label: "Hợp lệ", bg: "bg-success/10", text: "text-success", icon: "check_circle" },
    failed: { label: "Lỗi", bg: "bg-error/10", text: "text-error", icon: "cancel" },
    in_progress: { label: "Đang cào", bg: "bg-primary/10", text: "text-primary", icon: "sync" },
  };
  const c = config[status] || config.pending;
  return (
    <div className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 ${c.bg} ${c.text}`}>
      <MaterialIcon name={c.icon} className="text-[14px]" />
      <span className="text-[10px] font-black uppercase tracking-wide">{c.label}</span>
    </div>
  );
}

function TeamRow({ name, dept, count, rate, score, color, status }: any) {
  const badgeClass = status === "green" ? "bg-success/10 text-success" : status === "yellow" ? "bg-warning/10 text-warning" : "bg-error/10 text-error";
  const statusLabel = status === "green" ? "Hiệu quả" : status === "yellow" ? "Cần hỗ trợ" : "Khẩn cấp";

  return (
    <tr className="hover:bg-surface-container-low">
      <td className="py-md">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-full font-bold text-white text-xs" style={{ backgroundColor: color }}>
            {name.charAt(0)}
          </div>
          <span className="text-sm font-bold text-on-surface">{name}</span>
        </div>
      </td>
      <td className="py-md text-xs text-on-surface-variant">{dept}</td>
      <td className="py-md text-sm font-bold text-on-surface">{count.toLocaleString()}</td>
      <td className="py-md text-xs font-medium text-on-surface-variant">{rate}</td>
      <td className="py-md">
        <span className="text-sm font-black text-primary">{score}</span>
      </td>
      <td className="py-md text-right">
         <span className={`px-2 py-0.5 rounded-full text-[9px] font-black uppercase ${badgeClass}`}>{statusLabel}</span>
      </td>
    </tr>
  );
}

function CostBar({ label, value, total, color }: any) {
  const pct = Math.round((value / total) * 100);
  return (
    <div className="flex flex-col gap-1.5">
       <div className="flex items-center justify-between text-xs font-bold uppercase tracking-wider text-on-surface-variant">
          <span>{label}</span>
          <span>${value.toFixed(2)}</span>
       </div>
       <div className="h-1.5 w-full rounded-full bg-surface-container-high overflow-hidden">
          <div className="h-full transition-all duration-1000" style={{ width: `${pct}%`, backgroundColor: color }} />
       </div>
    </div>
  );
}

function AlertItem({ type, icon, title, desc }: any) {
  const colors: any = {
    warn: "border-warning/30 bg-warning/5 text-warning",
    info: "border-primary/30 bg-primary/5 text-primary",
    success: "border-success/30 bg-success/5 text-success",
  };
  return (
    <div className={`flex gap-3 p-3 rounded-xl border ${colors[type]}`}>
       <MaterialIcon name={icon} className="text-[20px] shrink-0" />
       <div className="flex flex-col gap-0.5">
          <h4 className="text-xs font-black uppercase tracking-wide leading-none">{title}</h4>
          <p className="text-[11px] leading-relaxed text-on-surface-variant opacity-80">{desc}</p>
       </div>
    </div>
  );
}

function SkillCard({ icon, title, count, desc }: any) {
  return (
    <div className="p-lg rounded-2xl border border-outline-variant bg-surface hover:border-primary/40 transition-all cursor-pointer group">
       <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-surface-container-high text-primary group-hover:bg-primary group-hover:text-on-primary transition-all">
          <MaterialIcon name={icon} className="text-[24px]" />
       </div>
       <h4 className="text-sm font-bold text-on-surface mb-1">{title}</h4>
       <p className="text-xs text-on-surface-variant mb-3">{desc}</p>
       <div className="text-[10px] font-black text-primary uppercase tracking-widest">{count} lần sử dụng</div>
    </div>
  );
}

function MyTasksTab({ tasks, onRefresh }: { tasks: any[], onRefresh: () => void }) {
  const { email } = useDashboard();
  const [isAdding, setIsAdding] = useState(false);
  const [newTaskTitle, setNewTaskTitle] = useState("");

  const toggleTask = async (id: string, currentStatus: string) => {
    if (!email) return;
    const nextStatus = currentStatus === "completed" ? "pending" : currentStatus === "pending" ? "in_progress" : "completed";
    try {
      await updatePersonalTask(email, id, { status: nextStatus });
      onRefresh();
    } catch (error) {
      console.error("Update task error", error);
    }
  };

  const handleDelete = async (id: string) => {
    if (!email) return;
    if (window.confirm("Xóa nhiệm vụ này?")) {
      try {
        await deletePersonalTask(email, id);
        onRefresh();
      } catch (error) {
        console.error("Delete task error", error);
      }
    }
  };

  const handleAddTask = async () => {
    if (!email || !newTaskTitle.trim()) return;
    try {
      await addPersonalTask(email, newTaskTitle, "medium", "Hôm nay");
      setNewTaskTitle("");
      setIsAdding(false);
      onRefresh();
    } catch (error) {
      console.error("Add task error", error);
    }
  };

  return (
    <div className="flex flex-col gap-lg animate-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center justify-between">
        <h3 className="text-xl font-bold text-on-surface uppercase tracking-tight">Nhiệm vụ cá nhân của bạn</h3>
        <button 
          onClick={() => setIsAdding(!isAdding)}
          className="bg-primary text-on-primary px-lg py-2 rounded-xl text-sm font-bold flex items-center gap-2 hover:shadow-lg hover:shadow-primary/20 transition-all"
        >
          <MaterialIcon name={isAdding ? "close" : "add"} /> {isAdding ? "Hủy" : "Tạo nhiệm vụ mới"}
        </button>
      </div>

      {isAdding && (
        <div className="p-lg rounded-2xl border-2 border-primary/20 bg-primary/5 animate-in zoom-in-95 duration-300">
           <label className="text-xs font-black uppercase tracking-widest text-primary mb-2 block">Tên nhiệm vụ mới</label>
           <div className="flex gap-2">
              <input 
                autoFocus
                value={newTaskTitle}
                onChange={(e) => setNewTaskTitle(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleAddTask()}
                className="flex-1 bg-surface border border-outline-variant rounded-xl px-md py-sm outline-none focus:border-primary transition-all"
                placeholder="Nhập nội dung công việc..."
              />
              <button 
                onClick={handleAddTask}
                className="bg-primary text-on-primary px-lg py-2 rounded-xl font-bold"
              >Lưu</button>
           </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-md">
        {tasks.map(task => (
          <div key={task.id} className="p-lg rounded-2xl border border-outline-variant bg-surface flex flex-col md:flex-row md:items-center justify-between gap-md hover:border-primary/40 transition-all group shadow-sm">
            <div className="flex items-center gap-4">
              <button 
                onClick={() => toggleTask(task.id, task.status)}
                className={`h-12 w-12 rounded-xl flex items-center justify-center shrink-0 transition-all ${
                task.status === "completed" ? "bg-success/10 text-success" : 
                task.status === "in_progress" ? "bg-primary/10 text-primary" : "bg-surface-container-high text-on-surface-variant"
              }`}>
                <MaterialIcon name={task.status === "completed" ? "check_circle" : task.status === "in_progress" ? "hourglass_empty" : "pending_actions"} className="text-[24px]" />
              </button>
              <div>
                <h4 className={`font-bold text-on-surface group-hover:text-primary transition-colors ${task.status === "completed" ? "line-through opacity-50" : ""}`}>{task.title}</h4>
                <div className="flex items-center gap-3 mt-1">
                  <span className="text-[10px] font-black uppercase tracking-widest text-on-surface-variant flex items-center gap-1">
                    <MaterialIcon name="calendar_today" className="text-[12px]" /> Deadline: {task.deadline}
                  </span>
                  <span className={`text-[10px] font-black uppercase tracking-widest px-2 py-0.5 rounded ${
                    task.priority === "high" ? "bg-error/10 text-error" : 
                    task.priority === "medium" ? "bg-warning/10 text-warning" : "bg-primary/10 text-primary"
                  }`}>
                    {task.priority === "high" ? "Khẩn cấp" : task.priority === "medium" ? "Trung bình" : "Thấp"}
                  </span>
                </div>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="flex flex-col items-end">
                <span 
                  onClick={() => toggleTask(task.id, task.status)}
                  className={`cursor-pointer select-none text-[10px] font-black uppercase tracking-widest px-3 py-1.5 rounded-full transition-all ${
                  task.status === "completed" ? "bg-success text-on-success" : 
                  task.status === "in_progress" ? "bg-primary text-on-primary shadow-sm shadow-primary/30" : "bg-surface-container-high text-on-surface-variant"
                }`}>
                  {task.status === "completed" ? "Đã xong" : task.status === "in_progress" ? "Đang chạy" : "Chờ xử lý"}
                </span>
              </div>
              <button 
                onClick={() => handleDelete(task.id)}
                className="h-10 w-10 flex items-center justify-center rounded-xl hover:bg-error/10 hover:text-error transition-colors text-on-surface-variant"
              >
                <MaterialIcon name="delete" />
              </button>
            </div>
          </div>
        ))}
        {tasks.length === 0 && (
          <div className="py-20 text-center border-2 border-dashed border-outline-variant rounded-2xl bg-surface-container-lowest">
             <MaterialIcon name="assignment" className="text-4xl text-on-surface-variant mb-4" />
             <p className="text-on-surface-variant font-bold">Không có nhiệm vụ nào. Thảnh thơi quá!</p>
          </div>
        )}
      </div>
    </div>
  );
}

function SeedingTasksTab({ tasks, loading, onRefresh }: { tasks: SeedingTask[], loading: boolean, onRefresh: () => void }) {
  const { email } = useDashboard();
  const [updatingId, setUpdatingId] = useState<string | null>(null);

  const handleStatusUpdate = async (taskId: string, status: string) => {
    if (!email) return;
    setUpdatingId(taskId);
    try {
      await updateSeedingStatus(email, taskId, status);
      onRefresh();
    } catch (error) {
      console.error("Status update error", error);
      alert("Lỗi khi cập nhật trạng thái");
    } finally {
      setUpdatingId(null);
    }
  };

  const handleVerify = async (taskId: string) => {
    if (!email) return;
    setUpdatingId(taskId);
    try {
      const res = await verifySeedingTask(email, taskId);
      if (res.data.verify_error) {
        alert("Xác minh thất bại: " + res.data.verify_error);
      } else {
        alert("Xác minh thành công!");
      }
      onRefresh();
    } catch (error) {
      console.error("Verify error", error);
      alert("Lỗi khi xác minh");
    } finally {
      setUpdatingId(null);
    }
  };

  const handleLockComments = async (taskId: string) => {
    if (!email) return;
    if (!window.confirm("Xác nhận bài viết này đã khóa comment? Hệ thống sẽ đánh dấu và hủy task này.")) return;
    
    setUpdatingId(taskId);
    try {
      await lockSeedingComments(email, taskId);
      onRefresh();
    } catch (error) {
      console.error("Lock comments error", error);
      alert("Lỗi khi cập nhật");
    } finally {
      setUpdatingId(null);
    }
  };

  return (
    <div className="flex flex-col gap-lg animate-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-xl font-bold text-on-surface uppercase tracking-tight">Chiến dịch Seeding</h3>
          <p className="text-sm text-on-surface-variant">Quản lý các bài viết cần seeding từ Google Sheet</p>
        </div>
        <button 
          onClick={onRefresh}
          disabled={loading}
          className="bg-surface-container-high text-on-surface px-lg py-2 rounded-xl text-sm font-bold flex items-center gap-2 hover:bg-surface-container-highest transition-all disabled:opacity-50"
        >
          <MaterialIcon name="refresh" className={loading ? "animate-spin" : ""} /> Làm mới
        </button>
      </div>

      <div className="rounded-2xl border border-outline-variant bg-surface overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="bg-surface-container-low border-b border-outline-variant">
                <th className="px-lg py-md text-xs font-bold text-on-surface-variant uppercase tracking-wider">Trạng thái</th>
                <th className="px-lg py-md text-xs font-bold text-on-surface-variant uppercase tracking-wider">Nhóm / Bài viết</th>
                <th className="px-lg py-md text-xs font-bold text-on-surface-variant uppercase tracking-wider">Thời gian</th>
                <th className="px-lg py-md text-right text-xs font-bold text-on-surface-variant uppercase tracking-wider">Hành động</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-outline-variant/30">
              {tasks.map((task) => (
                <tr key={task.task_id} className="group hover:bg-surface-container-low transition-colors">
                  <td className="px-lg py-lg">
                    <div className="flex flex-col gap-2">
                      <StatusBadge status={task.status} />
                      {task.verify_error && (
                        <span className="text-[10px] text-error max-w-[150px] leading-tight">{task.verify_error}</span>
                      )}
                    </div>
                  </td>
                  <td className="px-lg py-lg">
                    <div className="flex flex-col gap-1">
                      <span className="text-xs font-black text-primary uppercase tracking-tight truncate max-w-[250px]">
                        {task.group_name}
                      </span>
                      <a 
                        href={task.post_url} 
                        target="_blank" 
                        rel="noreferrer"
                        className="text-sm font-medium text-on-surface hover:text-primary transition-colors truncate max-w-[300px] flex items-center gap-1"
                      >
                        {task.post_content ? task.post_content.substring(0, 50) + "..." : task.post_url}
                        <MaterialIcon name="open_in_new" className="text-[14px]" />
                      </a>
                    </div>
                  </td>
                  <td className="px-lg py-lg text-xs text-on-surface-variant">
                    <div className="flex flex-col">
                      <span>{task.assigned_at ? new Date(task.assigned_at).toLocaleDateString() : "---"}</span>
                      <span className="opacity-60">{task.assigned_at ? new Date(task.assigned_at).toLocaleTimeString() : ""}</span>
                    </div>
                  </td>
                  <td className="px-lg py-lg text-right">
                    <div className="flex items-center justify-end gap-2">
                      <select 
                        disabled={updatingId === task.task_id}
                        value={task.status}
                        onChange={(e) => handleStatusUpdate(task.task_id, e.target.value)}
                        className="bg-surface-container-high border-none rounded-lg px-2 py-1 text-xs font-bold outline-none cursor-pointer hover:bg-surface-container-highest"
                      >
                        <option value="assigned">Chờ xử lý</option>
                        <option value="verifying">Đang đợi check</option>
                        <option value="verified">Hoàn thành</option>
                        <option value="failed">Lỗi</option>
                      </select>

                      <button 
                        onClick={() => handleVerify(task.task_id)}
                        disabled={updatingId === task.task_id || task.status === "verified"}
                        title="Xác minh tự động"
                        className="h-8 w-8 flex items-center justify-center rounded-lg bg-primary/10 text-primary hover:bg-primary hover:text-on-primary transition-all disabled:opacity-30"
                      >
                        <MaterialIcon name="verified" className={updatingId === task.task_id ? "animate-pulse" : ""} />
                      </button>

                      <button 
                        onClick={() => handleLockComments(task.task_id)}
                        disabled={updatingId === task.task_id || task.comments_locked === "TRUE"}
                        title="Khóa comment"
                        className={`h-8 w-8 flex items-center justify-center rounded-lg transition-all disabled:opacity-30 ${
                          task.comments_locked === "TRUE" 
                          ? "bg-error text-on-error" 
                          : "bg-surface-container-highest text-on-surface hover:bg-error/10 hover:text-error"
                        }`}
                      >
                        <MaterialIcon name="comments_disabled" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {tasks.length === 0 && !loading && (
            <div className="py-20 text-center">
               <MaterialIcon name="task_alt" className="text-4xl text-on-surface-variant mb-4" />
               <p className="text-on-surface-variant font-bold">Chưa có nhiệm vụ seeding nào được tạo.</p>
               <p className="text-xs text-on-surface-variant mt-1">Hãy thực hiện cào dữ liệu để tự động tạo task.</p>
            </div>
          )}
          {loading && (
            <div className="py-20 text-center">
               <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent"></div>
               <p className="text-on-surface-variant font-bold mt-4">Đang tải dữ liệu từ Google Sheet...</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}