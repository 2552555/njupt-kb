'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMutation, useQuery } from '@tanstack/react-query';
import toast from 'react-hot-toast';

import { Notice } from '@/components/common/Notice';
import { Button } from '@/components/ui/Button';
import { describeError } from '@/lib/api/client';
import { createBaoyanExperience, listColleges, listMajors } from '@/lib/api/topics';

export default function BaoyanSubmitPage() {
  const router = useRouter();
  const [collegeId, setCollegeId] = useState('');
  const [majorId, setMajorId] = useState('');
  const [form, setForm] = useState({
    target_university: '',
    target_college: '',
    target_major: '',
    offer_result: '',
    gpa_rank: '',
    year: String(new Date().getFullYear()),
    summer_camp_experience: '',
    pre_interview_experience: '',
    advice: '',
  });

  const colleges = useQuery({ queryKey: ['colleges'], queryFn: listColleges });
  const majors = useQuery({ queryKey: ['majors'], queryFn: listMajors });

  useEffect(() => {
    // 学院变化后清空不再属于该学院的专业，避免提交错配。
    setMajorId('');
  }, [collegeId]);

  const filteredMajors = (majors.data ?? []).filter(
    (item) => !collegeId || !item.college_id || item.college_id === collegeId,
  );

  const mutation = useMutation({
    mutationFn: () =>
      createBaoyanExperience({
        college_id: collegeId,
        major_id: majorId,
        target_university: form.target_university.trim(),
        target_college: form.target_college.trim() || undefined,
        target_major: form.target_major.trim() || undefined,
        offer_result: form.offer_result.trim() || undefined,
        gpa_rank: form.gpa_rank.trim() || undefined,
        summer_camp_experience: form.summer_camp_experience.trim() || undefined,
        pre_interview_experience: form.pre_interview_experience.trim() || undefined,
        advice: form.advice.trim() || undefined,
        year: Number(form.year),
      }),
    onSuccess: () => {
      toast.success('经验帖已提交，审核通过后公开展示');
      router.push('/baoyan');
    },
    onError: (error) => toast.error(describeError(error).message),
  });

  function update<K extends keyof typeof form>(key: K, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  const canSubmit =
    collegeId &&
    majorId &&
    form.target_university.trim().length > 0 &&
    Number(form.year) >= 2000 &&
    (form.summer_camp_experience.trim().length > 0 ||
      form.pre_interview_experience.trim().length > 0 ||
      form.advice.trim().length > 0);

  return (
    <div className="space-y-6">
      <header className="border-b border-divider pb-6">
        <Link href="/baoyan" className="text-sm text-brand underline underline-offset-2">返回保研专题</Link>
        <h1 className="dn-display mt-4 text-3xl">分享保研经验</h1>
        <p className="mt-3 text-text-secondary">
          写清楚时间线、考核形式与自己的准备过程，比只写结论更有帮助。提交后进入审核，通过后公开。
        </p>
      </header>

      <Notice tone="warning" title="不要填写个人信息">
        不要填写身份证号、手机号、考生编号或他人姓名。目标院校与专业写公开信息即可。
      </Notice>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor="college" className="block text-sm font-medium">本校学院</label>
          <select
            id="college"
            value={collegeId}
            onChange={(event) => setCollegeId(event.target.value)}
            className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm"
          >
            <option value="">请选择学院</option>
            {(colleges.data ?? []).map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="major" className="block text-sm font-medium">本校专业</label>
          <select
            id="major"
            value={majorId}
            onChange={(event) => setMajorId(event.target.value)}
            className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm"
            disabled={!collegeId}
          >
            <option value="">{collegeId ? '请选择专业' : '请先选择学院'}</option>
            {filteredMajors.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="year" className="block text-sm font-medium">推免年份</label>
          <input
            id="year"
            type="number"
            min={2000}
            max={2100}
            value={form.year}
            onChange={(event) => update('year', event.target.value)}
            className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm"
          />
        </div>
        <div>
          <label htmlFor="result" className="block text-sm font-medium">结果</label>
          <select
            id="result"
            value={form.offer_result}
            onChange={(event) => update('offer_result', event.target.value)}
            className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm"
          >
            <option value="">不填写</option>
            <option value="录取">录取</option>
            <option value="候补">候补</option>
            <option value="未录取">未录取</option>
            <option value="放弃">放弃</option>
          </select>
        </div>
        <div>
          <label htmlFor="target-university" className="block text-sm font-medium">目标院校</label>
          <input
            id="target-university"
            value={form.target_university}
            maxLength={200}
            onChange={(event) => update('target_university', event.target.value)}
            className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm"
            placeholder="例如：东南大学"
          />
        </div>
        <div>
          <label htmlFor="target-major" className="block text-sm font-medium">目标专业</label>
          <input
            id="target-major"
            value={form.target_major}
            maxLength={200}
            onChange={(event) => update('target_major', event.target.value)}
            className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm"
            placeholder="例如：计算机技术"
          />
        </div>
        <div className="sm:col-span-2">
          <label htmlFor="target-college" className="block text-sm font-medium">目标学院（可选）</label>
          <input
            id="target-college"
            value={form.target_college}
            maxLength={200}
            onChange={(event) => update('target_college', event.target.value)}
            className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm"
          />
        </div>
        <div className="sm:col-span-2">
          <label htmlFor="rank" className="block text-sm font-medium">本校排名（可选，写区间即可）</label>
          <input
            id="rank"
            value={form.gpa_rank}
            maxLength={50}
            onChange={(event) => update('gpa_rank', event.target.value)}
            className="mt-1 min-h-11 w-full border border-control-border bg-surface px-3 text-sm"
            placeholder="例如：前 10%"
          />
        </div>
        {[
          { key: 'summer_camp_experience' as const, label: '夏令营经历', placeholder: '时间线、材料、笔试面试形式' },
          { key: 'pre_interview_experience' as const, label: '预推免 / 面试经历', placeholder: '面试问题、考察重点、自己的表现' },
          { key: 'advice' as const, label: '给后来者的建议', placeholder: '哪些准备最有用，哪些可以省略' },
        ].map((item) => (
          <div key={item.key} className="sm:col-span-2">
            <label htmlFor={item.key} className="block text-sm font-medium">{item.label}</label>
            <textarea
              id={item.key}
              rows={4}
              value={form[item.key]}
              onChange={(event) => update(item.key, event.target.value)}
              className="mt-1 w-full border border-control-border bg-surface px-3 py-2 text-sm"
              placeholder={item.placeholder}
            />
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-2">
        <Button onClick={() => mutation.mutate()} isLoading={mutation.isPending} disabled={!canSubmit}>
          提交审核
        </Button>
        <Link href="/baoyan" className="inline-flex min-h-11 items-center border border-control-border px-4 text-sm">
          取消
        </Link>
      </div>
      {!canSubmit && (
        <p className="text-sm text-text-secondary">
          需要选择学院与专业、填写目标院校，并至少写一段经历或建议。
        </p>
      )}
    </div>
  );
}
