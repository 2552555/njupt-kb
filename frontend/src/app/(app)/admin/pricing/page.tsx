'use client';
import { PriceProposals } from '@/components/workspace/PriceProposals';
import { Notice } from '@/components/common/Notice';
import { hasPermission, useAuthStore } from '@/stores/authStore';

export default function AdminPricingPage() {
  const user = useAuthStore(s => s.user);
  return hasPermission(user, 'price.confirm') ? <PriceProposals admin /> : <Notice title="需要价格确认权限" />;
}
