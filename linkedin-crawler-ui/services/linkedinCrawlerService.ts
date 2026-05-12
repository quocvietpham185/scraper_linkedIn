import { API_BASE_URL, API_KEY } from '@/lib/env'
import type {
  AddListGroupRequest,
  AddN8nGroupRequest,
  BulkGroupImportResponse,
  CrawlGroupRequest,
  CrawlResponse,
  CrawlTaskStatusResponse,
  FilterDataRequest,
  FilterDataResponse,
  GetAllN8nGroupsRequest,
  GetAllPostsRequest,
  GetAllPostsResponse,
  LoginRequest,
  LoginResponse,
  N8nGroupOperationResponse,
  RemoveN8nGroupRequest,
  StartWorkflowRequest,
  StartWorkflowResponse,
  StatusResponse,
  UpdateN8nGroupRequest,
  VerifyLoginRequest,
  VerifyLoginResponse,
} from '@/types/api'

const JSON_HEADERS = {
  'Content-Type': 'application/json',
} as const

function buildHeaders(): HeadersInit {
  if (!API_KEY) {
    return JSON_HEADERS
  }
  return {
    ...JSON_HEADERS,
    'x-api-key': API_KEY,
  }
}

async function requestJson<TResponse>(
  path: string,
  init?: RequestInit,
): Promise<TResponse> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: init?.credentials ?? 'include',
    headers: {
      ...buildHeaders(),
      ...init?.headers,
    },
  })

  const contentType = response.headers.get('content-type') ?? ''
  let payload: TResponse | undefined

  if (contentType.includes('application/json')) {
    payload = (await response.json()) as TResponse
  } else {
    // Server trả về plain text hoặc HTML (ví dụ 500 Internal Server Error)
    const text = await response.text()
    if (!response.ok) {
      throw new Error(`API ${response.status}: ${text.slice(0, 200)}`)
    }
    // JSON parse fallback
    try {
      payload = JSON.parse(text) as TResponse
    } catch {
      throw new Error(`API ${response.status}: Unexpected non-JSON response`)
    }
  }

  if (!response.ok) {
    const errorPayload = payload as
      | { message?: string; detail?: string }
      | undefined
    const backendMessage =
      errorPayload?.message?.trim() || errorPayload?.detail?.trim()
    throw new Error(
      backendMessage
        ? `API ${response.status}: ${backendMessage}`
        : `API ${response.status}: ${response.statusText}`,
    )
  }
  return payload as TResponse
}

export function fetchCrawlerStatus(): Promise<StatusResponse> {
  return requestJson<StatusResponse>('/status', { method: 'GET' })
}

export function loginLinkedIn(payload: LoginRequest): Promise<LoginResponse> {
  return requestJson<LoginResponse>('/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function verifyLinkedInOtp(
  payload: VerifyLoginRequest,
): Promise<VerifyLoginResponse> {
  return requestJson<VerifyLoginResponse>('/verify', {
    method: 'POST',
    body: JSON.stringify({
      session_id: payload.sessionId,
      otp: payload.otp,
      checkpoint_url: payload.checkpointUrl,
    }),
  })
}

export function startN8nWorkflow(
  payload: StartWorkflowRequest,
): Promise<StartWorkflowResponse> {
  return requestJson<StartWorkflowResponse>('/start', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function crawlLinkedInGroup(
  payload: CrawlGroupRequest,
): Promise<CrawlResponse> {
  return requestJson<CrawlResponse>('/crawl-linkedin-group', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

/**
 * Option B: Crawl thủ công 1 nhóm bằng Apify API (không dùng Playwright).
 * Chỉ cần group_url, không cần session/email.
 */
export function apifyCrawlGroup(groupUrl: string): Promise<CrawlResponse> {
  return requestJson<CrawlResponse>('/apify/crawl-group', {
    method: 'POST',
    body: JSON.stringify({ group_url: groupUrl }),
  })
}

export function filterLinkedInPosts(
  payload: FilterDataRequest,
): Promise<FilterDataResponse> {
  return requestJson<FilterDataResponse>('/filter-data', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getAllLinkedInPosts(
  payload: GetAllPostsRequest,
): Promise<GetAllPostsResponse> {
  return requestJson<GetAllPostsResponse>('/get-all-posts', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getAllN8nGroups(
  payload: GetAllN8nGroupsRequest,
): Promise<N8nGroupOperationResponse> {
  return requestJson<N8nGroupOperationResponse>('/groups/get-all', {
    method: 'POST',
    body: JSON.stringify({ email: payload.email.trim() }),
  })
}

export function addListGroupBulk(
  payload: AddListGroupRequest,
): Promise<BulkGroupImportResponse> {
  const body: Record<string, unknown> = {
    group_urls: payload.group_urls.map((u) => u.trim()).filter(Boolean),
    post_to_webhook: payload.post_to_webhook ?? true,
    delay_min_sec: payload.delay_min_sec ?? 2,
    delay_max_sec: payload.delay_max_sec ?? 5,
  }
  if (payload.email?.trim()) body.email = payload.email.trim()
  if (payload.webhook_timeout_sec != null)
    body.webhook_timeout_sec = payload.webhook_timeout_sec
  return requestJson<BulkGroupImportResponse>('/groups/add-list-group', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function addN8nGroup(
  payload: AddN8nGroupRequest,
): Promise<N8nGroupOperationResponse> {
  const body: Record<string, unknown> = {
    url_group: payload.url_group.trim(),
    name_group: payload.name_group.trim(),
    member: payload.member,
  }
  if (payload.email?.trim()) body.email = payload.email.trim()
  return requestJson<N8nGroupOperationResponse>('/groups/add', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function removeN8nGroup(
  payload: RemoveN8nGroupRequest,
): Promise<N8nGroupOperationResponse> {
  const body: Record<string, unknown> = {
    url_group: payload.url_group.trim(),
  }
  if (payload.email?.trim()) body.email = payload.email.trim()
  return requestJson<N8nGroupOperationResponse>('/groups/remove', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateN8nGroup(
  payload: UpdateN8nGroupRequest,
): Promise<N8nGroupOperationResponse> {
  const body: Record<string, unknown> = {
    url_group_need_update: payload.url_group_need_update.trim(),
    name_group: payload.name_group.trim(),
    member: payload.member,
  }
  if (payload.new_url_group != null && payload.new_url_group !== '')
    body.new_url_group = payload.new_url_group.trim()
  if (payload.new_name_group != null && payload.new_name_group !== '')
    body.new_name_group = payload.new_name_group.trim()
  if (payload.new_member != null) body.new_member = payload.new_member
  if (payload.email?.trim()) body.email = payload.email.trim()
  return requestJson<N8nGroupOperationResponse>('/groups/update', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function reportKpiTask(
  email: string,
  postUrl: string,
): Promise<{ success: boolean; message: string; data: any }> {
  return requestJson('/kpi/report', {
    method: 'POST',
    body: JSON.stringify({ email, post_url: postUrl }),
  })
}

export function getKpiStatus(
  email: string,
): Promise<{ success: boolean; message: string; data: any }> {
  return requestJson(`/kpi/status?email=${encodeURIComponent(email)}`, {
    method: 'GET',
  })
}

export type SeedingTask = {
  task_id: string
  post_id: string
  post_url: string
  group_id: string
  group_name: string
  post_content: string
  assignee_email: string
  linkedin_public_id: string
  assigned_at: string
  reported_at: string
  verified_at: string
  status:
    | 'assigned'
    | 'reported'
    | 'verifying'
    | 'verified'
    | 'failed'
    | 'expired'
    | 'cancelled'
  comment_id: string
  comment_text: string
  comment_url: string
  verify_method: string
  verify_error: string
  comments_locked?: string
}

export function getSeedingTasks(
  email: string,
): Promise<{ success: boolean; message: string; data: SeedingTask[] }> {
  return requestJson(`/seeding/list?email=${encodeURIComponent(email)}`, {
    method: 'GET',
  })
}

export function reportSeedingTask(
  email: string,
  taskId: string,
  commentUrl = '',
): Promise<{ success: boolean; message: string; data: SeedingTask }> {
  return requestJson('/seeding/report', {
    method: 'POST',
    body: JSON.stringify({ email, task_id: taskId, comment_url: commentUrl }),
  })
}

export function verifySeedingTask(
  email: string,
  taskId: string,
): Promise<{ success: boolean; message: string; data: SeedingTask }> {
  return requestJson('/seeding/verify', {
    method: 'POST',
    body: JSON.stringify({ email, task_id: taskId }),
  })
}

export function updateSeedingStatus(
  email: string,
  taskId: string,
  status: string,
): Promise<{ success: boolean; message: string; data: SeedingTask }> {
  return requestJson('/seeding/update-status', {
    method: 'POST',
    body: JSON.stringify({ email, task_id: taskId, status }),
  })
}

export function lockSeedingComments(
  email: string,
  taskId: string,
): Promise<{ success: boolean; message: string; data: SeedingTask }> {
  return requestJson('/seeding/lock-comments', {
    method: 'POST',
    body: JSON.stringify({ email, task_id: taskId }),
  })
}

export function checkLinkStatus(
  url: string,
): Promise<{
  success: boolean
  message: string
  data: { url: string; status: 'live' | 'dead' | 'blocked' | 'error' }
}> {
  return requestJson('/links/check-status', {
    method: 'POST',
    body: JSON.stringify({ url }),
  })
}

export function getTaskStatus(
  sessionId: string,
): Promise<CrawlTaskStatusResponse> {
  return requestJson<CrawlTaskStatusResponse>(`/tasks/status/${sessionId}`, {
    method: 'GET',
  })
}

/** Kiểm tra trạng thái URL nhóm (Live/Dead). */
export async function checkGroupStatus(
  url: string,
): Promise<{ success: boolean; message: string }> {
  return requestJson<{ success: boolean; message: string }>(
    '/groups/check-status',
    {
      method: 'POST',
      body: JSON.stringify({ url }),
    },
  )
}

/** Thêm nhiệm vụ cá nhân. */
export async function addPersonalTask(
  email: string,
  title: string,
  priority: string,
  deadline: string,
): Promise<any> {
  return requestJson('/kpi/tasks/add', {
    method: 'POST',
    body: JSON.stringify({ email, title, priority, deadline }),
  })
}

/** Cập nhật nhiệm vụ cá nhân. */
export async function updatePersonalTask(
  email: string,
  taskId: string,
  updates: any,
): Promise<any> {
  return requestJson('/kpi/tasks/update', {
    method: 'PATCH',
    body: JSON.stringify({ email, task_id: taskId, updates }),
  })
}

/** Xóa nhiệm vụ cá nhân. */
export async function deletePersonalTask(
  email: string,
  taskId: string,
): Promise<any> {
  return requestJson(
    `/kpi/tasks/delete?email=${encodeURIComponent(email)}&task_id=${taskId}`,
    {
      method: 'DELETE',
    },
  )
}

/** Lấy trạng thái cache của toàn bộ nhóm. */
export async function getAllGroupStatuses(): Promise<{
  success: boolean
  data: Record<string, { status: string; checked_at: string }>
}> {
  return requestJson('/groups/all-statuses')
}

interface LiveFeedEvent {
  id: number
  type: string
  user: string
  message: string
  timestamp: string
  metadata: Record<string, any>
}

/** Lấy danh sách hoạt động đội ngũ thời gian thực. */
export async function getLiveFeed(): Promise<{
  success: boolean
  message: string
  data: LiveFeedEvent[]
}> {
  return requestJson('/team/live-feed')
}

/** Lấy thống kê dashboard của nhân viên (cá nhân). */
export async function getEmployeeDashboardStats(email: string): Promise<{
  success: boolean
  message?: string
  data?: {
    email: string
    total_posts: number
    total_groups: number
    active_groups: number
    completed_groups: number
    success_rate_percent: number
    today_posts_count: number
    recent_posts_count: number
    last_updated: string
  }
}> {
  return requestJson(
    `/employee/dashboard-stats?email=${encodeURIComponent(email)}`,
  )
}
