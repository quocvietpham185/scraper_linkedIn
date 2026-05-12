'use client'

import React, { useState, useEffect } from 'react'
import { MaterialIcon } from '@/components/ui'
import * as crawlerService from '@/services/linkedinCrawlerService'

interface Activity {
  id: number
  user: string
  action: string
  target: string
  count: number
  time: string
  avatar: string
  eventType?: string
}

export function TeamLiveContent() {
  const [activities, setActivities] = useState<Activity[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>('')

  const fetchLiveFeed = async () => {
    try {
      setError('')
      const response = await crawlerService.getLiveFeed()
      if (response?.success && Array.isArray(response?.data)) {
        const transformedActivities = response.data
          .map((event: any, index: number) => {
            // Get user initials for avatar
            const userParts = (event?.user || 'Unknown').split(' ')
            const avatar = userParts
              .map((p: string) => p[0])
              .join('')
              .toUpperCase()
              .slice(0, 2)

            // Parse timestamp to relative time
            const eventTime = new Date(event?.timestamp || new Date())
            const now = new Date()
            const diffMs = now.getTime() - eventTime.getTime()
            const diffMins = Math.floor(diffMs / 60000)
            let relativeTime = 'vừa xong'
            if (diffMins > 0) {
              if (diffMins < 60) {
                relativeTime = `${diffMins} phút trước`
              } else {
                const diffHours = Math.floor(diffMins / 60)
                relativeTime =
                  diffHours === 1 ? '1 giờ trước' : `${diffHours} giờ trước`
              }
            }

            // Transform event to activity display format
            let action = ''
            let target = ''
            let count = 0

            switch (event?.event_type) {
              case 'crawl_start':
                action = 'bắt đầu cào'
                target = event?.metadata?.group_name || 'một nhóm'
                break
              case 'crawl_complete':
                action = 'vừa hoàn thành cào'
                target = event?.metadata?.group_name || 'một nhóm'
                count = event?.metadata?.posts_count || 0
                break
              case 'report':
                action = 'vừa báo cáo bài viết'
                target = 'seeding'
                break
              case 'task_add':
                action = 'vừa thêm nhiệm vụ'
                target = event?.metadata?.task_title || 'mới'
                break
              case 'seeding_report':
                action = 'gửi báo cáo seeding'
                target = 'cho manager'
                count = event?.metadata?.posts_count || 0
                break
              case 'verify_success':
                action = 'xác thực thành công'
                target = 'bài viết seeding'
                break
              default:
                action = 'có hoạt động'
                target = event?.message || ''
            }

            return {
              id: index + 1,
              user: event?.user || 'Unknown',
              action,
              target,
              count,
              time: relativeTime,
              avatar,
              eventType: event?.event_type,
            }
          })
          .slice(0, 10) // Show only latest 10 events

        setActivities(transformedActivities)
        setError('')
      } else {
        const errorMsg = response?.message || 'Lỗi: Không thể tải dữ liệu'
        setError(errorMsg)
        setActivities([])
      }
    } catch (err: any) {
      console.error('Failed to fetch live feed:', err)
      // Ensure error message is always a string
      const errorMsg =
        typeof err?.message === 'string'
          ? err.message
          : 'Không thể kết nối đến server'
      setError(`Lỗi: ${errorMsg}`)
      setActivities([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchLiveFeed()

    // Auto-refresh every 30 seconds
    const interval = setInterval(fetchLiveFeed, 30 * 1000)

    return () => clearInterval(interval)
  }, [])

  return (
    <div className="flex flex-col gap-xl pb-20 animate-in fade-in duration-500">
      {/* Header */}
      <div>
        <div className="mb-2 flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-primary animate-pulse"></span>
          <span className="text-label-sm font-bold text-primary uppercase tracking-widest">
            Hoạt động trực tuyến
          </span>
        </div>
        <h1 className="text-h1 font-black text-on-surface tracking-tight">
          Team Live Feed
        </h1>
        <p className="text-body-lg text-on-surface-variant">
          Theo dõi hoạt động cào dữ liệu thời gian thực từ các đồng nghiệp.
        </p>
      </div>

      {error && (
        <div className="rounded-2xl border border-error/30 bg-error/5 p-lg flex items-start gap-lg">
          <MaterialIcon
            name="error"
            className="text-error text-[24px] shrink-0 mt-0.5"
          />
          <div className="flex-1">
            <h3 className="font-bold text-error mb-1">Lỗi tải Live Feed</h3>
            <p className="text-sm text-on-surface-variant">{error}</p>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-xl lg:grid-cols-3">
        {/* Main Feed */}
        <div className="lg:col-span-2 flex flex-col gap-lg">
          <h3 className="text-title-lg font-bold text-on-surface uppercase tracking-tight flex items-center gap-2">
            <MaterialIcon
              name="rss_feed"
              className="text-primary"
            />{' '}
            Dòng hoạt động
          </h3>

          <div className="space-y-4">
            {loading ? (
              <div className="p-lg rounded-2xl border border-outline-variant bg-surface flex items-center justify-center h-32">
                <div className="flex flex-col items-center gap-2">
                  <div className="h-8 w-8 rounded-full border-2 border-primary/30 border-t-primary animate-spin"></div>
                  <span className="text-sm text-on-surface-variant">
                    Đang tải hoạt động...
                  </span>
                </div>
              </div>
            ) : activities.length === 0 ? (
              <div className="p-lg rounded-2xl border border-outline-variant bg-surface flex items-center justify-center h-32">
                <div className="flex flex-col items-center gap-2 text-on-surface-variant">
                  <MaterialIcon
                    name="inbox"
                    className="text-[32px]"
                  />
                  <span className="text-sm">Chưa có hoạt động gần đây</span>
                </div>
              </div>
            ) : (
              activities.map((act) => (
                <div
                  key={act.id}
                  className="p-lg rounded-2xl border border-outline-variant bg-surface flex items-start gap-4 hover:border-primary/30 transition-all shadow-sm"
                >
                  <div className="h-12 w-12 rounded-full bg-primary/10 text-primary flex items-center justify-center font-bold text-lg shrink-0">
                    {act.avatar}
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center justify-between">
                      <h4 className="font-bold text-on-surface">{act.user}</h4>
                      <span className="text-[10px] text-on-surface-variant font-medium">
                        {act.time}
                      </span>
                    </div>
                    <p className="text-sm text-on-surface-variant mt-1">
                      {act.action}{' '}
                      <strong className="text-primary">{act.target}</strong>
                      {act.count > 0 && ` (${act.count} bài viết)`}
                    </p>
                    <div className="mt-3 flex items-center gap-2">
                      <button className="text-[10px] font-black uppercase tracking-widest text-primary px-3 py-1 bg-primary/5 rounded-full hover:bg-primary/10 transition-colors">
                        Xem kết quả
                      </button>
                      <button className="text-[10px] font-black uppercase tracking-widest text-on-surface-variant px-3 py-1 hover:bg-surface-container-high rounded-full transition-colors">
                        Tương tác
                      </button>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Sidebar: Leaderboard & Stats */}
        <div className="flex flex-col gap-xl">
          <div className="p-lg rounded-2xl border border-outline-variant bg-surface shadow-sm">
            <h3 className="mb-lg text-title-lg font-bold text-on-surface uppercase tracking-tight">
              Ngôi sao hôm nay
            </h3>
            <div className="space-y-4">
              <LeaderboardRow
                rank={1}
                name="Viet PQ"
                count="2,450"
                color="bg-warning"
              />
              <LeaderboardRow
                rank={2}
                name="Anh Tuấn"
                count="1,820"
                color="bg-slate-300"
              />
              <LeaderboardRow
                rank={3}
                name="Minh Thu"
                count="1,200"
                color="bg-orange-400"
              />
            </div>
          </div>

          <div className="p-lg rounded-2xl border border-outline-variant bg-surface shadow-sm">
            <h3 className="mb-lg text-title-lg font-bold text-on-surface uppercase tracking-tight">
              Thống kê Team (24h)
            </h3>
            <div className="grid grid-cols-2 gap-md">
              <div className="p-md rounded-xl bg-surface-container-high text-center">
                <p className="text-[10px] font-black text-on-surface-variant uppercase tracking-widest">
                  Tổng bài
                </p>
                <p className="text-xl font-black text-primary">12.5k</p>
              </div>
              <div className="p-md rounded-xl bg-surface-container-high text-center">
                <p className="text-[10px] font-black text-on-surface-variant uppercase tracking-widest">
                  Live Scrapers
                </p>
                <p className="text-xl font-black text-success">8</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function LeaderboardRow({ rank, name, count, color }: any) {
  return (
    <div className="flex items-center gap-4">
      <div
        className={`h-6 w-6 rounded-full flex items-center justify-center text-[10px] font-black text-white ${color}`}
      >
        {rank}
      </div>
      <div className="flex-1">
        <p className="text-sm font-bold text-on-surface">{name}</p>
        <p className="text-[10px] text-on-surface-variant">{count} bài viết</p>
      </div>
      <div className="h-1 w-12 bg-surface-container-high rounded-full overflow-hidden">
        <div
          className="h-full bg-primary"
          style={{ width: rank === 1 ? '100%' : rank === 2 ? '70%' : '40%' }}
        ></div>
      </div>
    </div>
  )
}
