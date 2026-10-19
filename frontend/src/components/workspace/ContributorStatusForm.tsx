'use client';

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Button } from '@/components/ui/Button';
import { describeError } from '@/lib/api/client';
import { changeContributorStatus, type ContributorApplicationOut } from '@/lib/api/creator';
import { useAuthStore } from '@/stores/authStore';

export function ContributorStatusForm({ application, selfExit = false, onChanged }: {
  application: ContributorApplicationOut;
  selfExit?: boolean;
  onChanged?: () => void;
}) {
  const client = useQueryClient();
  const [status, setStatus] = useState<'active' | 'suspended' | 'exited'>(
    selfExit ? 'exited' : application.status === 'active' ? 'suspended' : 'active',
  );
  const [reason, setReason] = useState('');
  const mutation = useMutation({
    mutationFn: () => changeContributorStatus(application, status, reason.trim(), selfExit),
    onSuccess: async () => {
      toast.success('专业身份已更新');
      await Promise.all([
        client.invalidateQueries({ queryKey: ['my-contributor-applications'] }),
        client.invalidateQueries({ queryKey: ['admin-contributor-applications'] }),
        client.invalidateQueries({ queryKey: ['my-assignments'] }),
      ]);
      if (selfExit) await useAuthStore.getState().hydrate();
      setReason('');
      onChanged?.();
    },
    onError: (error) => toast.error(describeError(error).message),
  });
  return (
    <form className="space-y-3 border-t border-divider pt-3" onSubmit={(event) => {
      event.preventDefault();
      mutation.mutate();
    }}>
      <p className="text-sm text-text-secondary">
        暂停或退出将撤销受托管理范围；本人内容、既有合同和收益记录保留。恢复后需重新分配管理范围。
      </p>
      {!selfExit && <label className="block text-sm">专业身份
        <select className="ml-3 min-h-11 border border-control-border bg-surface px-3"
          value={status} onChange={(event) => setStatus(event.target.value as typeof status)}>
          {application.status === 'suspended' && <option value="active">恢复</option>}
          {application.status === 'active' && <option value="suspended">暂停</option>}
          <option value="exited">退出</option>
        </select>
      </label>}
      <label className="block text-sm">{selfExit ? '退出理由' : '变更理由'}
        <textarea required maxLength={255} value={reason} rows={2}
          onChange={(event) => setReason(event.target.value)}
          className="mt-1 w-full border border-control-border bg-surface px-3 py-2" />
      </label>
      <Button type="submit" variant="secondary" isLoading={mutation.isPending} disabled={!reason.trim()}>
        {selfExit ? '确认退出专业身份' : '确认变更'}
      </Button>
    </form>
  );
}
