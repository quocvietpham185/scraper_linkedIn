'use client'

import React, { useEffect, useState } from 'react'
import { ApifyCrawlCard } from './ApifyCrawlCard'
import { CrawlerConfigCard } from './CrawlerConfigCard'
import { CrawlResultsSection } from './CrawlResultsSection'
import { MaterialIcon } from '@/components/ui'
import { useDashboard } from './dashboard-context'
import { cn } from '@/lib/utils'
import * as crawlerService from '@/services/linkedinCrawlerService'
import { readLinkedInCredentials } from '@/lib/credentials'

export function DashboardHomeContent() {
  const { status } = useDashboard()
  const isOnline = status?.api_key_enabled !== undefined
  const [stats, setStats] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>('')

  useEffect(() => {
    const fetchStats = async () => {
      try {
        setLoading(true)
        setError('')

        // Get email from credentials
        const stored = readLinkedInCredentials()
        if (!stored?.email) {
          setError('Vui lòng đăng nhập để xem thống kê')
          setLoading(false)
          return
        }

        const email = stored.email.trim()

        const response = await crawlerService.getEmployeeDashboardStats(email)
        if (response.success && response.data) {
          setStats(response.data)
          setError('')
        } else {
          setError(response.message || 'Không thể lấy thống kê')
          setStats(null)
        }
      } catch (err: any) {
        console.error('Failed to fetch dashboard stats:', err)
        setError(`Lỗi: ${err.message || 'Không thể tải thống kê'}`)
        setStats(null)
      } finally {
        setLoading(false)
      }
    }

    fetchStats()
    // Refresh stats every 5 minutes
    const interval = setInterval(fetchStats, 5 * 60 * 1000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="flex flex-col gap-xl pb-20 animate-in fade-in duration-500">
      <div className="flex flex-col gap-lg md:flex-row md:items-end md:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <span
              className={cn(
                'h-2 w-2 rounded-full animate-pulse',
                isOnline ? 'bg-success' : 'bg-error',
              )}
            ></span>
            <span
              className={cn(
                'text-label-sm font-bold uppercase tracking-widest',
                isOnline ? 'text-success' : 'text-error',
              )}
            >
              {isOnline ? 'Hệ thống Scraper sẵn sàng' : 'Mất kết nối máy chủ'}
            </span>
          </div>
          <h1 className="text-h1 font-black text-on-surface tracking-tight">
            LinkedIn Scraper Dashboard
          </h1>
          <p className="text-body-lg text-on-surface-variant">
            Thu thập dữ liệu thông minh từ các nhóm LinkedIn mục tiêu.
          </p>
        </div>
      </div>
      {error && (
        <div className="rounded-2xl border border-error/30 bg-error/5 p-lg flex items-start gap-lg">
          <MaterialIcon
            name="error"
            className="text-error text-[24px] shrink-0 mt-0.5"
          />
          <div className="flex-1">
            <h3 className="font-bold text-error mb-1">Lỗi tải thống kê</h3>
            <p className="text-sm text-on-surface-variant">{error}</p>
          </div>
        </div>
      )}
      <div className="grid grid-cols-1 gap-md sm:grid-cols-2 lg:grid-cols-4">
        <HomeStatCard
          icon="analytics"
          label="Tổng dữ liệu"
          value={
            stats?.total_posts !== undefined && stats?.total_posts !== null
              ? `${Number(stats.total_posts).toLocaleString()}`
              : '...'
          }
          sub="bài viết đã lưu"
          color="primary"
        />
        <HomeStatCard
          icon="hub"
          label="Nhóm mục tiêu"
          value={
            stats?.total_groups !== undefined && stats?.total_groups !== null
              ? String(stats.total_groups)
              : '...'
          }
          sub="đang theo dõi"
          color="secondary"
        />
        <HomeStatCard
          icon="speed"
          label="Nhóm hoạt động"
          value={
            stats?.active_groups !== undefined && stats?.active_groups !== null
              ? String(stats.active_groups)
              : '...'
          }
          sub="Đang cào dữ liệu"
          color="success"
        />
        <HomeStatCard
          icon="verified_user"
          label="Success Rate"
          value={
            stats?.success_rate_percent !== undefined &&
            stats?.success_rate_percent !== null
              ? `${Number(stats.success_rate_percent).toFixed(1)}%`
              : '...'
          }
          sub="Bài viết/Nhóm"
          color="info"
        />
      </div>

      <div className="grid grid-cols-1 gap-xl lg:grid-cols-3">
        <div className="lg:col-span-2 flex flex-col gap-xl">
          <CrawlerConfigCard />

          <div className="flex flex-col gap-lg">
            <div className="flex items-center gap-md">
              <div className="border-outline-variant flex-1 border-t" />
              <span className="text-label-sm text-on-surface-variant font-semibold uppercase tracking-widest">
                Phương án dự phòng
              </span>
              <div className="border-outline-variant flex-1 border-t" />
            </div>
            <ApifyCrawlCard />
          </div>

          <CrawlResultsSection />
        </div>

        {/* Executive Insights Sidebar */}
        <div className="flex flex-col gap-xl">
          <div className="rounded-2xl border border-outline-variant bg-surface p-lg shadow-sm">
            <h3 className="mb-lg text-title-lg font-bold text-on-surface uppercase tracking-tight">
              Executive Alerts
            </h3>
            <div className="flex flex-col gap-md">
              <div className="p-md rounded-xl bg-primary/5 border border-primary/20">
                <div className="flex items-center gap-2 text-primary mb-1">
                  <MaterialIcon
                    name="auto_awesome"
                    className="text-[18px]"
                  />
                  <span className="text-xs font-bold uppercase">
                    Tối ưu hóa
                  </span>
                </div>
                <p className="text-xs text-on-surface-variant leading-relaxed">
                  Sáng nay hệ thống cào <strong>1,200 bài</strong>. Bạn có thể
                  giảm Max Posts xuống 20 để tăng tốc độ cào lên 40%.
                </p>
              </div>

              <div className="p-md rounded-xl bg-warning/5 border border-warning/20">
                <div className="flex items-center gap-2 text-warning mb-1">
                  <MaterialIcon
                    name="warning"
                    className="text-[18px]"
                  />
                  <span className="text-xs font-bold uppercase">Cảnh báo</span>
                </div>
                <p className="text-xs text-on-surface-variant leading-relaxed">
                  Group <strong>React Vietnam</strong> đang có tỷ lệ cào thất
                  bại cao (30%). Kiểm tra lại tính chất nhóm kín.
                </p>
              </div>

              <div className="p-md rounded-xl bg-success/5 border border-success/20">
                <div className="flex items-center gap-2 text-success mb-1">
                  <MaterialIcon
                    name="trending_up"
                    className="text-[18px]"
                  />
                  <span className="text-xs font-bold uppercase">Cơ hội</span>
                </div>
                <p className="text-xs text-on-surface-variant leading-relaxed">
                  Dữ liệu cho thấy từ khóa <strong>"AI Agent"</strong> đang tăng
                  200% lượt quan tâm trong các nhóm Tech.
                </p>
              </div>
            </div>
          </div>

          <div className="rounded-2xl border border-outline-variant bg-surface p-lg shadow-sm">
            <h3 className="mb-lg text-title-lg font-bold text-on-surface uppercase tracking-tight">
              Hệ thống Trạng thái
            </h3>
            <div className="space-y-4">
              <StatusRow
                label="Tier 3 Playwright"
                status={isOnline ? 'Online' : 'Offline'}
                color={isOnline ? 'bg-success' : 'bg-error'}
              />
              <StatusRow
                label="Tier 1-2 Apify Actors"
                status="Standby"
                color="bg-primary"
              />
              <StatusRow
                label="Telegram Bot"
                status={isOnline ? 'Active' : 'Disabled'}
                color={isOnline ? 'bg-success' : 'bg-on-surface-variant'}
              />
              <StatusRow
                label="Google Sheets"
                status={status?.google_sheet_configured ? 'Synced' : 'Missing'}
                color={
                  status?.google_sheet_configured ? 'bg-success' : 'bg-error'
                }
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function HomeStatCard({ icon, label, value, sub, color }: any) {
  const colorMap: any = {
    primary: 'text-primary bg-primary/10',
    secondary: 'text-secondary bg-secondary/10',
    success: 'text-success bg-success/10',
    info: 'text-info bg-info/10',
  }
  return (
    <div className="rounded-2xl border border-outline-variant bg-surface p-lg shadow-sm hover:border-primary/30 transition-all group">
      <div className="flex items-center gap-3 mb-3">
        <div
          className={`flex h-10 w-10 items-center justify-center rounded-xl ${colorMap[color]}`}
        >
          <MaterialIcon
            name={icon}
            className="text-[24px]"
          />
        </div>
        <p className="text-[10px] font-black uppercase tracking-widest text-on-surface-variant">
          {label}
        </p>
      </div>
      <h3 className="text-h2 font-black text-on-surface">{value}</h3>
      <p className="mt-1 text-[10px] font-bold text-on-surface-variant">
        {sub}
      </p>
    </div>
  )
}

function StatusRow({ label, status, color }: any) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs font-medium text-on-surface-variant">
        {label}
      </span>
      <div className="flex items-center gap-2">
        <span className={`h-1.5 w-1.5 rounded-full ${color}`}></span>
        <span className="text-[10px] font-bold uppercase text-on-surface tracking-wider">
          {status}
        </span>
      </div>
    </div>
  )
}
