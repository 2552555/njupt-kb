'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { hasPermission, isAdmin, useAuthStore } from '@/stores/authStore';

export const adminItems = [
  { href: '/admin/workbench', label: '工作台', permission: 'ops.view' },
  { href: '/admin/reviews', label: '审核队列', permission: 'resource.review' },
  { href: '/admin/publications', label: '发布队列', permission: 'resource.publish' },
  { href: '/admin/reports', label: '投诉工单', permission: 'report.handle' },
  { href: '/admin/corrections', label: '纠错工单', permission: 'correction.handle' },
  { href: '/admin/jobs', label: '异步任务', permission: 'audit.view' },
  { href: '/admin/audit', label: '审计记录', permission: 'audit.view' },
  { href: '/admin/cases', label: '案件', permission: 'case.handle' },
  { href: '/admin/contributor-applications', label: '入驻审核', permission: 'contributor.review' },
  { href: '/admin/entitlements', label: '访问权益', permission: 'entitlement.grant' },
  { href: '/admin/catalog', label: '目录维护', permission: 'catalog.manage' },
  { href: '/admin/comments', label: '评论管理', permission: 'comment.moderate' },
  { href: '/admin/events', label: '失败事件', permission: 'job.retry' },
  { href: '/admin/security', label: '安全概览', permission: 'audit.view' },
  { href: '/admin/assignments', label: '权限分配', permission: 'assignment.manage' },
  { href: '/admin/metrics', label: '指标', permission: 'metrics.view' },
  { href: '/admin/analytics', label: '数据分析', permission: 'metrics.view' },
  { href: '/admin/guides', label: '攻略审核', permission: 'resource.review' },
  { href: '/admin/defense', label: '主动防御', permission: 'defense.manage' },
  { href: '/admin/pricing', label: '价格方案', permission: 'price.confirm' },
  { href: '/admin/settlements', label: '结算管理', permission: 'settlement.manage' },
  { href: '/admin/payouts', label: '付款核对', permission: 'payout.manage' },
 ];

export function AdminNav() {
  const pathname = usePathname();
  const user = useAuthStore(s => s.user);
  const items = [
    ...(isAdmin(user) ? [
      { href: '/admin/imports', label: '批量上传' },
      { href: '/admin/users', label: '用户治理' },
    ] : []),
    ...adminItems.filter(item => hasPermission(user, item.permission)),
  ];
  return <nav aria-label="管理后台导航" className="border-b border-divider">
    <ul className="flex flex-wrap gap-1">{items.map(item => {
      const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
      return <li key={item.href}><Link href={item.href} aria-current={active ? 'page' : undefined}
        className={`flex min-h-11 items-center px-4 text-sm font-medium ${active ? 'bg-brand text-on-color' : 'text-text-secondary hover:bg-canvas hover:text-text-primary'}`}>
        {item.label}
      </Link></li>;
    })}</ul>
  </nav>;
}
