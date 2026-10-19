'use client';

import Link from 'next/link';
import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { AuthGuard } from '@/components/layout/AuthGuard';
import { describeError } from '@/lib/api/client';
import {
  CASE_SUBJECT_TYPE_LABELS,
  createRightsNotice,
  RIGHTS_NOTICE_STATUS_LABELS,
  type CaseSubjectType,
  type RightsNoticeReceiptOut,
} from '@/lib/api/cases';
import { isUuid } from '@/lib/utils';

/**
 * 真实性声明版本：与提交内容一并记录，便于事后追溯适用的声明文本。
 * 平台尚未提供声明版本接口，这里使用当前发布的固定版本号。
 */
const STATEMENT_VERSION = 'v1';

const textareaClass =
  'mt-1 min-h-11 w-full border border-control-border bg-surface px-3 py-2 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus';

export default function RightsNoticePage() {
  return (
    <AuthGuard>
      <div className="mx-auto max-w-2xl space-y-6">
        <header>
          <p className="dn-kicker text-text-secondary">RIGHTS NOTICE</p>
          <h1 className="dn-display mt-1 text-3xl">侵权通知</h1>
          <p className="mt-2 text-sm text-text-secondary">
            无需购买即可提交。请提供真实身份与可核验的权利证明，以便核实处理。
          </p>
        </header>

        <Notice tone="info" title="隐私提示">
          身份证明与联系方式仅受理人员可见，不会公开展示；请勿在说明中粘贴证件原件全文。
        </Notice>

        <Notice tone="info" title="处理说明">
          合格通知将进入案件流程并可能采取撤下、停止下载等必要措施，处理结果会通知你。
        </Notice>

        <RightsNoticeForm />
      </div>
    </AuthGuard>
  );
}

function RightsNoticeForm() {
  const [claimantName, setClaimantName] = useState('');
  const [claimantContact, setClaimantContact] = useState('');
  const [rightsBasisRef, setRightsBasisRef] = useState('');
  const [targetType, setTargetType] = useState<CaseSubjectType>('resource');
  const [targetId, setTargetId] = useState('');
  const [contentRef, setContentRef] = useState('');
  const [requestedAction, setRequestedAction] = useState('');
  const [evidenceRef, setEvidenceRef] = useState('');
  const [declared, setDeclared] = useState(false);
  const [receipt, setReceipt] = useState<RightsNoticeReceiptOut | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      createRightsNotice({
        claimant_name: claimantName.trim(),
        claimant_contact: claimantContact.trim(),
        rights_basis_ref: rightsBasisRef.trim(),
        target_type: targetType,
        target_id: targetId.trim(),
        content_ref: contentRef.trim(),
        requested_action: requestedAction.trim(),
        statement_version: STATEMENT_VERSION,
        evidence_ref: evidenceRef.trim(),
        truthfulness_declared: declared,
      }),
    onSuccess: (data) => {
      setReceipt(data);
      toast.success('侵权通知已提交');
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  if (receipt) {
    return (
      <div className="space-y-4 border border-divider bg-surface p-5">
        <Notice tone="success" title="已收到你的侵权通知">
          <span className="block break-all">工单号：{receipt.notice_id}</span>
          <span className="block">状态：{RIGHTS_NOTICE_STATUS_LABELS[receipt.status]}</span>
        </Notice>
        <div className="flex flex-wrap gap-3">
          <Link
            href="/me/cases"
            className="inline-flex min-h-11 items-center text-sm text-brand underline"
          >
            在「我的案件」中查看进度
          </Link>
          <Button variant="secondary" onClick={() => setReceipt(null)}>
            再提交一份
          </Button>
        </div>
      </div>
    );
  }

  const requiredFilled =
    claimantName.trim().length > 0 &&
    claimantContact.trim().length > 0 &&
    rightsBasisRef.trim().length > 0 &&
    targetId.trim().length > 0 &&
    contentRef.trim().length > 0 &&
    requestedAction.trim().length > 0 &&
    evidenceRef.trim().length > 0 &&
    declared;

  return (
    <form
      className="space-y-4 border border-divider bg-surface p-5"
      onSubmit={(event) => {
        event.preventDefault();
        if (!requiredFilled) {
          toast.error('请填写全部必填项并确认真实性声明');
          return;
        }
        if (!isUuid(targetId.trim())) {
          toast.error('对象标识需为有效的 UUID');
          return;
        }
        mutation.mutate();
      }}
    >
      <fieldset disabled={mutation.isPending} className="space-y-4">
        <Input
          label="权利人真实姓名或名称（必填）"
          value={claimantName}
          onChange={(event) => setClaimantName(event.target.value)}
          maxLength={300}
          required
        />
        <Input
          label="联系渠道（必填）"
          value={claimantContact}
          onChange={(event) => setClaimantContact(event.target.value)}
          maxLength={300}
          required
          placeholder="邮箱或电话，仅受理人员可见。"
        />
        <Input
          label="权利证明引用（必填）"
          value={rightsBasisRef}
          onChange={(event) => setRightsBasisRef(event.target.value)}
          maxLength={512}
          required
          placeholder="权属证明的引用标识或存放位置，不粘贴原件。"
        />

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm text-text-primary">
            对象类型（必填）
            <select
              value={targetType}
              onChange={(event) => setTargetType(event.target.value as CaseSubjectType)}
              className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm text-text-primary focus:border-brand focus:outline-2 focus:outline-offset-1 focus:outline-focus"
            >
              {Object.entries(CASE_SUBJECT_TYPE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <Input
            label="对象标识（必填）"
            value={targetId}
            onChange={(event) => setTargetId(event.target.value)}
            required
            placeholder="资料的 UUID"
          />
        </div>

        <label className="block text-sm text-text-primary">
          内容地址／版本说明（必填）
          <textarea
            value={contentRef}
            onChange={(event) => setContentRef(event.target.value)}
            rows={3}
            maxLength={1000}
            required
            className={textareaClass}
            placeholder="指向涉嫌侵权内容的具体地址或版本，不粘贴内容全文。"
          />
        </label>

        <Input
          label="请求措施（必填）"
          value={requestedAction}
          onChange={(event) => setRequestedAction(event.target.value)}
          maxLength={300}
          required
          placeholder="例如：撤下该版本并停止下载。"
        />
        <Input
          label="初步证据引用（必填）"
          value={evidenceRef}
          onChange={(event) => setEvidenceRef(event.target.value)}
          maxLength={512}
          required
          placeholder="证据的引用标识或存放位置，不保存证据原件副本。"
        />

        <fieldset className="space-y-2 border-t border-divider pt-4">
          <legend className="text-sm font-medium text-text-primary">真实性声明</legend>
          <p className="text-sm text-text-secondary">
            本人声明通知中的陈述真实、准确，权利证明与证据材料可核验；如陈述失实，愿承担相应责任。
          </p>
          <label className="flex min-h-11 items-center gap-2 text-sm text-text-primary">
            <input
              type="checkbox"
              checked={declared}
              onChange={(event) => setDeclared(event.target.checked)}
            />
            我已阅读并同意上述真实性声明
          </label>
        </fieldset>

        <Button type="submit" isLoading={mutation.isPending} disabled={!requiredFilled}>
          提交侵权通知
        </Button>
      </fieldset>
    </form>
  );
}
