/**
 * 管理端数据分析与审计导出（backend/app/modules/ops/router.py，beta16 §43、§44）。
 *
 * 两个端点都要求对应权限码（METRICS_VIEW / AUDIT_VIEW）：前端只负责入口与 403 提示，
 * 权限最终由服务端判定。导出由后端生成 CSV，前端不拼表内容。
 */
import { api } from './client';

export interface AnalyticsCounts {
  new_users: number;
  new_resources: number;
  published_versions: number;
  downloads: number;
  favorites: number;
  ratings: number;
  comments: number;
  reports: number;
  corrections: number;
  requests: number;
  cases: number;
}

/** 一天的分桶计数；窗口内无数据的日期由后端补 0，前端可连续画图。 */
export interface AnalyticsPoint extends AnalyticsCounts {
  /** UTC 自然日，YYYY-MM-DD。 */
  date: string;
}

export interface AnalyticsFunnel {
  uploaded: number;
  submitted: number;
  approved: number;
  published: number;
  downloaded: number;
}

export interface TopCourseItem {
  course_id: string;
  name: string;
  download_count: number;
}

export interface AnalyticsOverview {
  window_days: number;
  generated_at: string;
  series: AnalyticsPoint[];
  totals: AnalyticsCounts;
  funnel: AnalyticsFunnel;
  top_courses: TopCourseItem[];
  /** 口径说明：由后端给出，页面必须原样展示，不能折叠隐藏。 */
  data_notes: string[];
}

export async function getAnalyticsOverview(days: number): Promise<AnalyticsOverview> {
  const { data } = await api.get<AnalyticsOverview>('/admin/analytics/overview', {
    params: { days },
  });
  return data;
}

export interface AuditExportParams {
  /** ISO 8601 时间；留空由后端按默认窗口处理。 */
  from?: string;
  to?: string;
  /** 动作前缀匹配，如 `review.`；后端上限 80 字符。 */
  action?: string;
}

export interface AuditExportFile {
  blob: Blob;
  /** 取自 Content-Disposition；缺失时回退为 audit.csv。 */
  filename: string;
  /** 行数超过服务端上限时后端截断并置为 true。 */
  truncated: boolean;
  rows: number | null;
}

/** 解析 `attachment; filename="..."`，同时兼容 RFC 5987 的 `filename*=UTF-8''...`。 */
function filenameFromDisposition(header: string | undefined): string {
  if (!header) return 'audit.csv';
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1]);
    } catch {
      return 'audit.csv';
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1] : 'audit.csv';
}

function stringHeader(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

/**
 * 下载审计元数据 CSV。
 * 不传 `limit`：行数上限由服务端配置决定，避免前端传超过上限的值。
 */
export async function exportAuditCsv(params: AuditExportParams = {}): Promise<AuditExportFile> {
  const response = await api.get<Blob>('/admin/audit/export', {
    params: {
      from: params.from || undefined,
      to: params.to || undefined,
      action: params.action || undefined,
    },
    responseType: 'blob',
  });

  const rowsHeader = stringHeader(response.headers['x-export-rows']);
  const rows = rowsHeader && /^\d+$/.test(rowsHeader) ? Number(rowsHeader) : null;

  return {
    blob: response.data,
    filename: filenameFromDisposition(stringHeader(response.headers['content-disposition'])),
    truncated: stringHeader(response.headers['x-export-truncated']) === 'true',
    rows,
  };
}
