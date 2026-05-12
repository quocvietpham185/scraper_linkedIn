"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";

import { useDashboard } from "./dashboard-context";
import { MaterialIcon, type MaterialSymbolName } from "@/components/ui";
import {
  checkLinkStatus,
  getSeedingTasks,
  reportSeedingTask,
  verifySeedingTask,
  lockSeedingComments,
  type SeedingTask,
} from "@/services/linkedinCrawlerService";

const STATUS_LABEL: Record<SeedingTask["status"], string> = {
  assigned: "Cần comment",
  reported: "Đã báo cáo",
  verifying: "Đang xác minh",
  verified: "Đã tick",
  failed: "Cần kiểm tra lại",
  expired: "Hết hạn",
  cancelled: "Đã hủy",
};

const STATUS_CLASS: Record<SeedingTask["status"], string> = {
  assigned: "bg-primary/10 text-primary",
  reported: "bg-warning/10 text-warning",
  verifying: "bg-warning/10 text-warning",
  verified: "bg-success/10 text-success",
  failed: "bg-error/10 text-error",
  expired: "bg-surface-container-high text-on-surface-variant",
  cancelled: "bg-surface-container-high text-on-surface-variant",
};

function shortDate(value: string) {
  if (!value) return "N/A";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
}

function taskNeedsAction(task: SeedingTask) {
  return task.status === "assigned" || task.status === "failed";
}

function verifyHint(task: SeedingTask) {
  const text = (task.verify_error || "").toLowerCase();
  if (text.includes("session linkedin")) {
    return "Dang nhap lai LinkedIn trong he thong, sau do bam Verify lai.";
  }
  if (text.includes("dan link comment") || text.includes("dán link comment")) {
    return "Dan link comment vao o ben phai roi bam Toi da comment hoac Verify lai.";
  }
  if (!task.linkedin_public_id) {
    return "Task thieu LinkedIn ID cua nhan vien. Bao leader cap nhat cot linkedin_public_id.";
  }
  if (!task.post_id) {
    return "Task thieu post_id. Bao leader cap nhat cot post_id hoac post_url dung bai.";
  }
  return "";
}

function canSubmitComment(taskId: string, linkStatuses: Record<string, "live" | "dead" | "blocked" | "error" | "checking">) {
  return linkStatuses[taskId] !== "dead";
}

export function EmployeeSeedingTasksContent() {
  const { email } = useDashboard();
  const [tasks, setTasks] = useState<SeedingTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null);
  const [commentUrls, setCommentUrls] = useState<Record<string, string>>({});
  const [linkStatuses, setLinkStatuses] = useState<Record<string, "live" | "dead" | "blocked" | "error" | "checking">>({});
  const [error, setError] = useState("");

  const loadTasks = useCallback(async () => {
    if (!email) return;
    setLoading(true);
    setError("");
    try {
      const res = await getSeedingTasks(email);
      setTasks(res.data || []);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Không tải được nhiệm vụ.");
    } finally {
      setLoading(false);
    }
  }, [email]);

  useEffect(() => {
    loadTasks();
    const timer = setInterval(loadTasks, 30000);
    return () => clearInterval(timer);
  }, [loadTasks]);

  const stats = useMemo(() => {
    const verified = tasks.filter((t) => t.status === "verified").length;
    const pending = tasks.filter((t) => t.status === "assigned" || t.status === "failed").length;
    const verifying = tasks.filter((t) => t.status === "verifying" || t.status === "reported").length;
    const locked = tasks.filter((t) => t.comments_locked === "TRUE").length;
    return { verified, pending, verifying, locked, total: tasks.length };
  }, [tasks]);

  // --- Pagination Logic ---
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;
  
  const paginatedTasks = useMemo(() => {
    const start = (currentPage - 1) * itemsPerPage;
    return tasks.slice(start, start + itemsPerPage);
  }, [tasks, currentPage]);

  const totalPages = Math.ceil(tasks.length / itemsPerPage);

  useEffect(() => {
    // Reset to page 1 when tasks change or filter?
    // setCurrentPage(1);
  }, [tasks.length]);

  const handleReport = async (task: SeedingTask) => {
    if (!email) return;
    setBusyTaskId(task.task_id);
    setError("");
    try {
      await reportSeedingTask(email, task.task_id, commentUrls[task.task_id] || "");
      await loadTasks();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Không báo cáo được task.");
    } finally {
      setBusyTaskId(null);
    }
  };

  const handleRetryVerify = async (task: SeedingTask) => {
    if (!email) return;
    setBusyTaskId(task.task_id);
    setError("");
    try {
      await verifySeedingTask(email, task.task_id);
      await loadTasks();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Không xác minh được task.");
    } finally {
      setBusyTaskId(null);
    }
  };

  const handleLockComments = async (task: SeedingTask) => {
    if (!email) return;
    if (!window.confirm("Xác nhận bài viết này đã khóa comment? Hệ thống sẽ đánh dấu và hủy task này.")) return;
    
    setBusyTaskId(task.task_id);
    setError("");
    try {
      await lockSeedingComments(email, task.task_id);
      await loadTasks();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Không cập nhật được.");
    } finally {
      setBusyTaskId(null);
    }
  };

  const handleCheckLink = async (taskId: string) => {
    const url = (commentUrls[taskId] || "").trim();
    if (!url) {
      setError("Nhap link can kiem tra truoc.");
      return;
    }
    setError("");
    setLinkStatuses((prev) => ({ ...prev, [taskId]: "checking" }));
    try {
      const res = await checkLinkStatus(url);
      setLinkStatuses((prev) => ({ ...prev, [taskId]: res.data.status }));
    } catch (exc) {
      setLinkStatuses((prev) => ({ ...prev, [taskId]: "error" }));
      setError(exc instanceof Error ? exc.message : "Khong kiem tra duoc link.");
    }
  };

  if (!email) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-outline-variant bg-surface">
        <div className="text-center">
          <MaterialIcon name="lock" className="mb-3 text-4xl text-on-surface-variant" />
          <h2 className="text-title-lg font-bold text-on-surface">Cần đăng nhập LinkedIn</h2>
          <p className="mt-1 text-body-sm text-on-surface-variant">Đăng nhập để xem nhiệm vụ seeding được giao cho email của bạn.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-xl pb-16">
      <div className="flex flex-col gap-md md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-h1 font-black tracking-tight text-on-surface">Nhiệm vụ seeding</h1>
          <p className="text-body-md text-on-surface-variant">Mở đúng bài, comment bằng tài khoản LinkedIn của bạn, rồi báo hệ thống xác minh.</p>
        </div>
        <button
          onClick={loadTasks}
          disabled={loading}
          className="inline-flex items-center gap-2 rounded-xl border border-outline-variant bg-surface px-lg py-2 text-sm font-bold text-on-surface hover:border-primary disabled:opacity-60"
        >
          <MaterialIcon name="refresh" className={loading ? "animate-spin" : ""} />
          Làm mới
        </button>
      </div>

      <div className="grid grid-cols-2 gap-md lg:grid-cols-5">
        <Stat label="Tổng task" value={stats.total} icon="list_alt" />
        <Stat label="Cần làm" value={stats.pending} icon="comment" />
        <Stat label="Đang verify" value={stats.verifying} icon="refresh" />
        <Stat label="Đã khóa" value={stats.locked} icon="comments_disabled" />
        <Stat label="Đã tick" value={stats.verified} icon="check_circle" />
      </div>

      {error && (
        <div className="rounded-xl border border-error/30 bg-error/5 p-md text-sm font-medium text-error">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-md">
        {paginatedTasks.map((task) => (
          <article key={task.task_id} className="rounded-2xl border border-outline-variant bg-surface p-lg shadow-sm">
            <div className="flex flex-col gap-md lg:flex-row lg:items-start lg:justify-between">
              <div className="min-w-0 flex-1">
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <span className={`inline-flex items-center gap-1 rounded-full px-3 py-1 text-[11px] font-black uppercase ${
                    task.comments_locked === "TRUE" ? "bg-error text-on-error" : (STATUS_CLASS[task.status] || STATUS_CLASS.assigned)
                  }`}>
                    <MaterialIcon name={task.comments_locked === "TRUE" ? "comments_disabled" : (task.status === "verified" ? "check_circle" : "info")} className="text-[14px]" />
                    {task.comments_locked === "TRUE" ? "Bài viết khóa comment" : (STATUS_LABEL[task.status] || task.status)}
                  </span>
                  {task.group_name && <span className="text-xs font-bold text-on-surface-variant">{task.group_name}</span>}
                  <span className="text-xs text-on-surface-variant">Giao lúc {shortDate(task.assigned_at)}</span>
                </div>

                <p className="line-clamp-4 whitespace-pre-wrap text-sm leading-6 text-on-surface">
                  {task.post_content || task.post_url}
                </p>

                <div className="mt-4 grid grid-cols-1 gap-sm text-xs text-on-surface-variant md:grid-cols-3">
                  <Info label="Task ID" value={task.task_id} />
                  <Info label="Post ID" value={task.post_id || "Chưa có"} />
                  <Info label="LinkedIn ID" value={task.linkedin_public_id || "Chưa có"} />
                </div>

                {task.status === "failed" && task.verify_error && (
                  <div className="mt-4 rounded-xl border border-error/20 bg-error/5 p-md text-xs leading-5 text-error">
                    <div>{task.verify_error}</div>
                    {verifyHint(task) && (
                      <div className="mt-2 font-bold text-on-surface">
                        {verifyHint(task)}
                      </div>
                    )}
                  </div>
                )}

                {task.status === "verified" && (
                  <div className="mt-4 rounded-xl border border-success/20 bg-success/5 p-md">
                    <div className="text-xs font-bold text-success">Đã xác minh lúc {shortDate(task.verified_at)}</div>
                    {task.comment_text && <p className="mt-2 line-clamp-3 text-xs leading-5 text-on-surface-variant">{task.comment_text}</p>}
                  </div>
                )}
              </div>

              <div className="flex w-full flex-col gap-sm lg:w-72">
                <a
                  href={task.post_url || "#"}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-primary px-lg py-2.5 text-sm font-bold text-on-primary hover:shadow-lg hover:shadow-primary/20"
                >
                  <MaterialIcon name="open_in_new" />
                  Mở bài để comment
                </a>

                {taskNeedsAction(task) && (
                  <>
                    <input
                      value={commentUrls[task.task_id] || ""}
                      onChange={(event) => setCommentUrls((prev) => ({ ...prev, [task.task_id]: event.target.value }))}
                      className="rounded-xl border border-outline-variant bg-surface px-md py-2 text-sm outline-none focus:border-primary"
                      placeholder="Link comment nếu có"
                    />
                    <button
                      onClick={() => handleCheckLink(task.task_id)}
                      disabled={busyTaskId === task.task_id || linkStatuses[task.task_id] === "checking"}
                      className="inline-flex items-center justify-center gap-2 rounded-xl border border-outline-variant px-lg py-2 text-sm font-bold text-on-surface hover:border-primary disabled:opacity-60"
                    >
                      <MaterialIcon
                        name={linkStatuses[task.task_id] === "checking" ? "refresh" : "visibility"}
                        className={linkStatuses[task.task_id] === "checking" ? "animate-spin" : ""}
                      />
                      Kiem tra link
                    </button>
                    {linkStatuses[task.task_id] && linkStatuses[task.task_id] !== "checking" && (
                      <div
                        className={`rounded-lg px-3 py-2 text-xs font-bold uppercase tracking-wider ${
                          linkStatuses[task.task_id] === "live"
                            ? "bg-success/10 text-success"
                            : linkStatuses[task.task_id] === "dead"
                              ? "bg-error/10 text-error"
                              : linkStatuses[task.task_id] === "blocked"
                                ? "bg-warning/10 text-warning"
                                : "bg-surface-container-high text-on-surface-variant"
                        }`}
                      >
                        {linkStatuses[task.task_id] === "live" && "Link dang song"}
                        {linkStatuses[task.task_id] === "dead" && "Link da chet"}
                        {linkStatuses[task.task_id] === "blocked" && "Link bi chan hoac can login"}
                        {linkStatuses[task.task_id] === "error" && "Khong kiem tra duoc link"}
                      </div>
                    )}
                    <button
                      onClick={() => handleReport(task)}
                      disabled={busyTaskId === task.task_id || !canSubmitComment(task.task_id, linkStatuses)}
                      className="inline-flex items-center justify-center gap-2 rounded-xl bg-success px-lg py-2.5 text-sm font-bold text-on-success disabled:opacity-60"
                    >
                      <MaterialIcon name={busyTaskId === task.task_id ? "refresh" : "check_circle"} className={busyTaskId === task.task_id ? "animate-spin" : ""} />
                      Tôi đã comment
                    </button>
                    {linkStatuses[task.task_id] === "dead" && (
                      <div className="rounded-lg border border-error/20 bg-error/5 px-3 py-2 text-xs font-bold text-error">
                        Link da chet. Khong the gui bao cao voi link nay.
                      </div>
                    )}
                  </>
                )}

                {(task.status === "failed" || task.status === "verifying") && (
                  <button
                    onClick={() => handleRetryVerify(task)}
                    disabled={busyTaskId === task.task_id}
                    className="inline-flex items-center justify-center gap-2 rounded-xl border border-outline-variant px-lg py-2.5 text-sm font-bold text-on-surface hover:border-primary disabled:opacity-60"
                  >
                    <MaterialIcon name="check_circle" />
                    Verify lại
                  </button>
                )}

                {task.status !== "verified" && task.comments_locked !== "TRUE" && (
                  <button
                    onClick={() => handleLockComments(task)}
                    disabled={busyTaskId === task.task_id}
                    className="inline-flex items-center justify-center gap-2 rounded-xl border border-error/30 px-lg py-2 text-xs font-bold text-error hover:bg-error/5 disabled:opacity-60"
                  >
                    <MaterialIcon name="comments_disabled" />
                    Bài khóa comment
                  </button>
                )}
              </div>
            </div>
          </article>
        ))}
      </div>

      {/* Pagination Controls */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 mt-8">
          <button
            onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
            disabled={currentPage === 1}
            className="h-10 w-10 flex items-center justify-center rounded-xl border border-outline-variant bg-surface hover:border-primary disabled:opacity-40"
          >
            <MaterialIcon name="chevron_left" />
          </button>
          
          <div className="flex items-center gap-1">
            {Array.from({ length: totalPages }, (_, i) => i + 1).map((p) => (
              <button
                key={p}
                onClick={() => setCurrentPage(p)}
                className={`h-10 w-10 flex items-center justify-center rounded-xl font-bold text-sm transition-all ${
                  currentPage === p 
                  ? "bg-primary text-on-primary shadow-md shadow-primary/20" 
                  : "bg-surface border border-outline-variant hover:border-primary"
                }`}
              >
                {p}
              </button>
            ))}
          </div>

          <button
            onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
            disabled={currentPage === totalPages}
            className="h-10 w-10 flex items-center justify-center rounded-xl border border-outline-variant bg-surface hover:border-primary disabled:opacity-40"
          >
            <MaterialIcon name="chevron_right" />
          </button>
        </div>
      )}

      {!loading && tasks.length === 0 && (
        <div className="rounded-2xl border-2 border-dashed border-outline-variant bg-surface-container-lowest py-20 text-center">
          <MaterialIcon name="list_alt" className="mb-3 text-4xl text-on-surface-variant" />
          <p className="font-bold text-on-surface-variant">Chưa có nhiệm vụ seeding nào được giao cho bạn.</p>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, icon }: { label: string; value: number; icon: MaterialSymbolName }) {
  return (
    <div className="rounded-2xl border border-outline-variant bg-surface p-lg">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[11px] font-black uppercase tracking-widest text-on-surface-variant">{label}</span>
        <MaterialIcon name={icon} className="text-primary" />
      </div>
      <div className="text-h2 font-black text-on-surface">{value}</div>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg bg-surface-container-low px-3 py-2">
      <div className="text-[10px] font-black uppercase tracking-widest text-on-surface-variant">{label}</div>
      <div className="truncate font-mono text-[11px] text-on-surface">{value}</div>
    </div>
  );
}
