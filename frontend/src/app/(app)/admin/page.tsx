'use client';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { adminItems } from './AdminNav';
import { hasPermission, useAuthStore } from '@/stores/authStore';

export default function AdminPage() {
  const router = useRouter();
  const user = useAuthStore(s => s.user);
  const first = adminItems.find(item => hasPermission(user, item.permission));
  useEffect(() => { if (first) router.replace(first.href); }, [first, router]);
  return <p role="status">正在打开管理工作台…</p>;
}
